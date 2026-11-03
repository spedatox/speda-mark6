from app.skills.base import Skill
from app.services.memory_runtime import packet_json
import asyncio
import json
from time import monotonic


class MemoryLookupSkill(Skill):
    name = "memory_lookup"
    read_only = True
    description = (
        "Retrieve a bounded batch of source-linked personal memory missing from the supplied context. "
        "Use it for a specific person, past event, or exact source detail needed to answer the owner. "
        "Do not call it when the supplied memory already answers the question or repeat overlapping searches. "
        "Returns attributed evidence, ambiguity and incomplete-coverage status within one shared turn budget."
    )
    input_schema = {"type": "object", "properties": {
        "query": {"type": "string", "maxLength": 2000},
        "corpus": {"type": "string", "enum": ["personal", "hall_of_records", "conversation_history"]},
        "after": {"type":"string", "maxLength":10, "description":"Inclusive owner-local YYYY-MM-DD archive date."},
        "before": {"type":"string", "maxLength":10, "description":"Inclusive owner-local YYYY-MM-DD archive date."},
        "before_message_id": {"type":"integer", "minimum":1},
        "limit": {"type":"integer", "minimum":1, "maximum":8},
        "operation": {"type":"string", "enum":["search","list","read"], "description":"Hall of Records supports bounded catalog listing and exact document reading in the same single lookup batch."},
        "document_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "revision_id": {"type":"string", "maxLength":128},
        "page": {"type":"integer", "minimum":1},
        "record_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
        "offset": {"type": "integer", "minimum": 0},
        "source_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 8}},
        "required": ["query"], "additionalProperties": False}

    def __init__(self, runtime, library=None, catalogue=None):
        self.runtime = runtime
        self.library = library
        self.catalogue = catalogue

    def available(self, context):
        return context.memory_budget.lookup_batches < 1

    async def execute(self, args, context):
        query = args.get("query", "")
        ids = args.get("source_ids", [])
        if not isinstance(query, str) or len(query) > 2000 or not isinstance(ids, list) or len(ids) > 8 or any(type(i) is not int for i in ids):
            return '{"status":"unavailable","reason":"Invalid bounded memory query"}'
        if args.get("record_id"):
            return await self._record(context, args["record_id"], args.get("offset", 0))
        if args.get("corpus") == "hall_of_records":
            return await self._library(context, query, args.get("document_ids"),args)
        if args.get('corpus')=='conversation_history':
            return await self._history(context,query,args)
        if args.get('operation','search')!='search':
            return '{"status":"unavailable","reason":"Personal retrieval uses search or an exact record_id"}'
        return packet_json(await self.runtime.lookup(context, query, source_ids=ids))

    async def _history(self, context, query, args):
        from app.services.history_lookup import search_archive
        from app.schemas.memory_runtime import Deadline
        budget=context.memory_budget
        if budget.lookup_batches>=1: return '{"status":"budget_exhausted"}'
        budget.lookup_batches+=1
        started=monotonic()
        deadline=Deadline.after(min(2.5,max(0,5-budget.elapsed_seconds)))
        try:
            async with asyncio.timeout(deadline.remaining()):
                async with self.runtime.store.reader(deadline) as db:
                    result=await search_archive(db,context,query,after=args.get('after'),before=args.get('before'),
                        limit=args.get('limit',6),cursor=args.get('before_message_id'))
            ceiling=min(budget.lookup_limit,budget.turn_limit-budget.used)
            while len(json.dumps(result,ensure_ascii=False).encode())>ceiling and result.get('results'):
                omitted=result['results'].pop()
                result.update(status='partial',has_more=True,next_before_message_id=omitted['message_id']+1)
            encoded=json.dumps(result,ensure_ascii=False)
            if len(encoded.encode())>ceiling: return '{"status":"budget_exhausted"}'
            budget.used+=len(encoded.encode())
            return encoded
        except (ValueError,TimeoutError): return '{"status":"unavailable","reason":"Invalid dates or archive retrieval deadline"}'
        except Exception: return '{"status":"unavailable","reason":"Archive unavailable within the retrieval deadline"}'
        finally: budget.elapsed_seconds+=monotonic()-started

    async def _record(self, context, identity, offset):
        budget = context.memory_budget
        if not self.catalogue or budget.lookup_batches >= 1:
            return '{"status":"unavailable","reason":"Lookup unavailable or turn batch used"}'
        if type(offset) is not int or offset < 0:
            return '{"status":"invalid_offset"}'
        budget.lookup_batches += 1
        started = monotonic()
        try:
            async with asyncio.timeout(min(2.5, max(0, 5-budget.elapsed_seconds))):
                row = await self.catalogue.get(context.user_id, identity)
            if row["status"] != "accepted" or row["evidence_status"] != "active":
                return '{"status":"withdrawn"}'
            ceiling = min(budget.lookup_limit, budget.turn_limit-budget.used)
            body = row["payload"].get("content") if row["kind"] == "document" else json.dumps(row["payload"], ensure_ascii=False)
            body = body or ""
            result = {"record_id": row["id"], "version": row["version"], "title": row["title"],
                "kind": row["kind"], "source_ids": list({e["source_id"] for e in row["evidence"]}),
                "evidence_scope": row["evidence_scope"], "warnings": [],
                "offset": offset, "text": body[offset:offset+2000], "next_offset": None,
                "warning": "Record excerpt; surrounding native structure may be omitted"}
            if row["evidence_scope"]["historical"]:
                result["warnings"].append("historical; not evidence of current status")
            if not row["evidence_scope"]["eligible_for_context"]:
                result["warnings"].append("Retained source excluded from automatic context; do not assert it as current")
            while result["text"] and len(json.dumps(result, ensure_ascii=False).encode()) + 64 > ceiling:
                result["text"] = result["text"][:-100]
            end = offset + len(result["text"])
            result["next_offset"] = end if end < len(body) else None
            encoded = json.dumps(result, ensure_ascii=False)
            if len(encoded.encode()) > ceiling:
                return '{"status":"budget_exhausted"}'
            budget.used += len(encoded.encode())
            return encoded
        except (KeyError, TimeoutError):
            return '{"status":"unavailable"}'
        finally:
            budget.elapsed_seconds += monotonic()-started

    async def _library(self, context, query, document_ids, args=None):
        args = args or {}
        budget = context.memory_budget
        if not self.library or budget.lookup_batches >= 1:
            return '{"status":"unavailable","reason":"Lookup unavailable or turn batch used"}'
        if document_ids is not None and (not isinstance(document_ids, list) or len(document_ids) > 8
                                        or any(not isinstance(i, str) or len(i) > 128 for i in document_ids)):
            return '{"status":"unavailable","reason":"Invalid document IDs"}'
        budget.lookup_batches += 1
        started = monotonic()
        try:
            async with asyncio.timeout(min(2.5, max(0, 5 - budget.elapsed_seconds))):
                operation = args.get('operation','search')
                if operation=='list':
                    result = (await self.library.list_documents(context,offset=args.get('offset',0),limit=8)).model_dump(mode='json')
                elif operation=='read':
                    if not document_ids or len(document_ids)!=1:
                        return '{"status":"unavailable","reason":"Exact reading requires one document ID"}'
                    result = (await self.library.read_passage(context,document_ids[0],page=args.get('page'),
                        revision_id=args.get('revision_id'),offset=args.get('offset',0),limit=4)).model_dump(mode='json')
                    # Evidence results already carry content and citation IDs.
                    result.pop('passages',None)
                    result.pop('citation_ids',None)
                elif operation=='search':
                    result = (await self.library.search(context, query, doc_ids=document_ids, limit=4)).model_dump(mode="json")
                else:
                    return '{"status":"unavailable","reason":"Unknown bounded library operation"}'
            ceiling = min(budget.lookup_limit, budget.turn_limit - budget.used)
            items_key = 'documents' if operation=='list' else 'results'
            while len(json.dumps(result, ensure_ascii=False).encode()) > ceiling and result.get(items_key):
                result[items_key].pop()
                result["warnings"] = ["Additional passages omitted by the shared turn budget"]
                if operation=='list': result['has_more']=True
                if operation=='read': result['next_offset']=args.get('offset',0)+len(result[items_key])
            encoded = json.dumps(result, ensure_ascii=False)
            if len(encoded.encode()) > ceiling:
                return '{"status":"budget_exhausted"}'
            budget.used += len(encoded.encode())
            return encoded
        except Exception:
            return '{"status":"unavailable","reason":"Hall of Records unavailable within retrieval deadline"}'
        finally:
            budget.elapsed_seconds += monotonic() - started
