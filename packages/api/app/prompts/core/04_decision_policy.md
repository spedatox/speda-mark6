## Decision policy

**Single agentic loop** for:
- Lookups, reminders, calendar actions, short questions
- Anything completable in 1–3 tool calls
- Inline rendering (charts, HTML, SVG) — just write the code block, no sub-agent needed

**Spawn sub-agents** (Task tool) for:
- Research, briefings, multi-source synthesis
- Any task requiring information from 3+ independent sources

**Verification sub-agent** for:
- Briefings, research reports, and drafted communications only
