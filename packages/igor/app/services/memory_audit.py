# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audit coverage is data, not a sentence Orion writes about itself.

Structural checks and semantic review are distinct. A clean formatter cannot
attest that a document contains the right subject. A content change invalidates
its review; outstanding findings remain visible until the changed content is
reviewed again. Nothing here autonomously rewrites the owner's knowledge.
"""

import json
import re
from sqlalchemy import select

from app.core.clock import owner_today
from app.models.memory_file import MemoryFile
from app.models.memory_review import MemoryReview
from app.services.memory_states import ROOT, OPEN, parse, version


async def coverage(db, user_id: int) -> dict:
    from app.services.memory_spec import spec_for
    files = (await db.execute(select(MemoryFile).where(MemoryFile.user_id == user_id))).scalars().all()
    reviews = (await db.execute(select(MemoryReview).where(MemoryReview.user_id == user_id)
                               .order_by(MemoryReview.id.desc()))).scalars().all()
    latest = {}
    for r in reviews:
        latest.setdefault(r.path, r)
    pending, issues, clean = [], [], 0
    today = owner_today().isoformat()
    for f in files:
        if "/." in f.path or f.path in ("/memories/current.md", "/memories/log.md"):
            continue
        spec = spec_for(f.path)
        if spec and spec.superseded_by:
            continue
        fingerprint = version(f.content)
        review = latest.get(f.path)
        if review is None or review.fingerprint != fingerprint:
            pending.append({"path": f.path, "fingerprint": fingerprint,
                            "reason": "never reviewed" if review is None else "changed since review"})
        elif json.loads(review.findings):
            issues.append({"path": f.path, "fingerprint": fingerprint, "findings": json.loads(review.findings)})
        else:
            clean += 1
        if f.path.startswith(ROOT):
            try:
                state = parse(f.content)
                if state["status"] in OPEN and (state["review_on"] < today or
                        (state.get("ends_on") and state["ends_on"] < today)):
                    issues.append({"path": f.path, "findings": ["State is overdue for verification; do not assume completion or continued validity."]})
            except (ValueError, TypeError, KeyError) as exc:
                issues.append({"path": f.path, "findings": [str(exc)]})
        # A literal countdown ages even when no writer touches the document.
        if re.search(r"\b\d+\s*(?:days? remaining|days? left|gün kaldı)\b", f.content, re.I):
            issues.append({"path": f.path, "findings": ["Stored relative countdown: retain the absolute target date and calculate remaining days at read time."]})
    return {"reviewed_clean": clean, "pending_review": pending, "unresolved": issues,
            "verdict": "review_required" if pending or issues else "reviewed",
            "note": "Semantic reviews are agent attestations, not proof of factual truth. Structural and temporal checks run separately."}


async def record_review(db, *, user_id, author, path, fingerprint, findings, rationale):
    from app.services.memory_verify import verify_document
    file = (await db.execute(select(MemoryFile).where(MemoryFile.user_id == user_id,
                        MemoryFile.path == path).execution_options(populate_existing=True))).scalar_one_or_none()
    if file is None or version(file.content) != fingerprint:
        raise ValueError("Document changed or is missing. Read and review the current content; no attestation saved.")
    if not isinstance(findings, list) or any(not isinstance(f, str) or not f.strip() for f in findings):
        raise ValueError("findings must be a list of concrete unresolved defects, or [] after review.")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("Explain the subject, temporal status, evidence and cross-file checks performed.")
    hard = [f.message for f in verify_document(path, file.content) if f.severity == "error"]
    if hard and not findings:
        raise ValueError("Cannot attest clean while structural errors remain: " + "; ".join(hard))
    db.add(MemoryReview(user_id=user_id, path=path, fingerprint=fingerprint,
                       author=author, findings=json.dumps(findings, ensure_ascii=False), rationale=rationale))
    await db.commit()
