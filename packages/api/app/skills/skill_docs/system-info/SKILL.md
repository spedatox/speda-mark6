---
name: system-info
description: Reports server health metrics (disk usage, memory, uptime) for the Contabo instance SPEDA runs on. Use when the user asks about server health, available storage, memory, or uptime.
---

# system_info

Returns live hardware metrics from the Contabo server.

## When to use

- User asks: "how is the server?", "how much disk space is left?", "what's the memory usage?", "server uptime?"

## When not to use

- User asks about application health, API status, or service-level metrics — use `/health` endpoint
- Questions about software, not server hardware

## Tool call

```json
{
  "metric": "all"
}
```

`metric`: `"disk"` | `"memory"` | `"uptime"` | `"all"`. Default `"all"`.

Returns a plain text summary, e.g.:
```
Disk: 180.3 GB free / 400.0 GB total
Memory total: 15.6 GiB
Memory available: 8.2 GiB
Uptime: 14h 32m
```
