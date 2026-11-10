"""Client for the root-owned Orion maintenance socket mounted into the backend."""

import asyncio
import hashlib
import json

SOCKET_PATH = "/run/speda-orion-ops/broker.sock"


async def host_operation(args, context):
    action = args["action"]
    operation_id = args.get("operation_id") if action == "operation_status" else hashlib.sha256(
        f"{context.request_id}:{context.tool_call_id}".encode()).hexdigest()[:32]
    request = {**args, "operation_id": operation_id}
    connect = getattr(asyncio, "open_unix_connection", None)
    if connect is None:
        return {"status": "unavailable", "reason": "Host maintenance requires the server broker."}
    try:
        async with asyncio.timeout(2):
            reader, writer = await connect(SOCKET_PATH, limit=128 * 1024)
    except (OSError, TimeoutError, NotImplementedError):
        return {"status": "unavailable", "reason": "The host maintenance broker is unavailable."}
    try:
        async with asyncio.timeout(58):
            writer.write(json.dumps(request, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
            reply = json.loads(await reader.readline())
            if not isinstance(reply, dict):
                raise ValueError("Invalid host operation response")
    except (OSError, TimeoutError, ValueError):
        reply = {"status": "unknown", "reason": "Check the receipt before repeating this operation."}
    finally:
        writer.close()
    return {**reply, "source": "host", "operation_id": operation_id}
