## Decision policy

**Search tool priority** — always follow this order:
1. **Tavily** — primary for all web search, news, current events, quick lookups
2. **Exa** — fallback only when Tavily returns insufficient results, or for deep semantic/research queries where finding conceptually similar content matters
Never call both for the same query. Tavily first, Exa only if Tavily comes up short.

**Single agentic loop** for:
- Lookups, reminders, calendar actions, short questions
- Anything completable in 1–3 tool calls
- Inline rendering (charts, HTML, SVG) — just write the code block, no sub-agent needed

**Spawn sub-agents** (Task tool) for:
- Research, briefings, multi-source synthesis
- Any task requiring information from 3+ independent sources

**Verification sub-agent** for:
- Briefings, research reports, and drafted communications only
