# Mark VI orchestration renovation — study and recommendation

Study begun 23 October; design completed 24 October 2026. Design only; no application changes or deployment.

**Recommendation: one shared coordination layer, with three modes: direct chat, collaborative rooms, and durable project work. Keep Iacon for memory and Forge for execution.**

| Area | What changes |
|---|---|
| Chat | Ordinary messages go directly to the addressed agent. No compulsory planner, committee, or personality-rewriting call. |
| Collaboration | SPEDA can invite selected agents into the same conversation. They see relevant shared evidence, speak under their own identities, challenge findings, and revise proposals. |
| Personality | Versioned character profiles, dialogue examples, relationship rules, and actual conversation tests. Your exact portrayals are the targets; generic warmth/humor sliders cannot establish fidelity. |
| Subagents | Named agents remain enduring characters. Anonymous workers perform bounded jobs. Optimus leads Autobots; Scourge leads Decepticons. |
| Hisar projects | A stable project record carries the objective, decisions, files, checks, open work, and next action across chats, workers, machines, and restarts. |
| Server work | Persisted jobs, scoped execution, verified results, cancellation, and recovery from recorded boundaries. An uncertain external action is reconciled before any retry. |
| Cost and speed | Shared spending limits across the entire task tree, focused context, selective participation, and separate capacity for interactive chat. |
| Iacon and Orion | Iacon remains the memory authority. Orion maintains records and follows up on states through the governed Telegram workflow. Project execution state has its own operational store. |

The mature `final` repository already contains substantial useful work: persona prompts, dialogue evaluations, a dispatch network, worker event journals, workspace ownership, and project checkpoints. The renovated implementation currently has a smaller, single-profile chat loop and bounded read-only workers. The missing piece is a consistent coordination and recovery contract across those systems.

The existing House Party path dispatches work and collects results; it does not provide the room membership, speaker turns, shared decision state, and recovery model envisioned here. Some continuity already works at the project-record level; that must survive the renovation.

**First implementation milestone:** one persistent room where SPEDA invites Optimus and Scourge, they respond to each other's evidence, and an attached Hisar project resumes correctly after a worker or backend interruption. Include cost admission and effect receipts from the beginning.

Character fidelity and answer speed still require live evaluation. This study makes no claim that prompts reproduce characters perfectly or that a complete multi-agent answer will finish in three seconds.

- [Architecture and operating contracts](<C:/Users/ahmet/OneDrive/Belgeler/ChatGPT/revamped mark VI/research/orchestration-20261023/ARCHITECTURE.md>)
- [Code findings and reuse decisions](<C:/Users/ahmet/OneDrive/Belgeler/ChatGPT/revamped mark VI/research/orchestration-20261023/CODE_AUDIT.md>)
- [Implementation sequence and acceptance checks](<C:/Users/ahmet/OneDrive/Belgeler/ChatGPT/revamped mark VI/research/orchestration-20261023/IMPLEMENTATION_PLAN.md>)
- [Primary-source comparison](<C:/Users/ahmet/OneDrive/Belgeler/ChatGPT/revamped mark VI/research/orchestration-20261023/SOURCES.md>)
