# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import configure_logging, settings
from app.profiles.speda import AGENT_NAME

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup sequence — order is non-negotiable (CLAUDE.md Build Order).
    Phase 1 done signal: all five criteria in CLAUDE.md must pass.
    """
    logger.info("startup_begin", extra={"version": "0.1.0"})

    # ── 1. Database ────────────────────────────────────────────────────────────
    from app.database import close_db, init_db

    await init_db()
    logger.info("startup_db_ready")

    # ── 2. LLM Client (created early — registry needs it for the Legion) ────────
    from app.services.llm_client import LLMClient

    llm_client = LLMClient()

    # ── 2.5 Profiles (multi-tenant) + dispatch primitive ───────────────────────
    # Profiles are constructed before the registry because the dispatch_agent
    # skill's tool schema is built from the roster. The dispatcher itself is
    # created empty here and late-bound to the orchestrator via wire() below.
    from app.core.dispatch import AgentDispatcher
    from app.profiles.registry import ProfileRegistry
    from app.profiles.atomix import AtomixProfile
    from app.profiles.centurion import CenturionProfile
    from app.profiles.nightcrawler import NightCrawlerProfile
    from app.profiles.optimus import OptimusProfile
    from app.profiles.orion import OrionProfile
    from app.profiles.sentinel import SentinelProfile
    from app.profiles.speda import SPEDAProfile
    from app.profiles.ultron import UltronProfile
    from app.profiles.warroom import WarRoomProfile

    profiles = ProfileRegistry()
    profiles.register(SPEDAProfile())       # orchestrator
    profiles.register(WarRoomProfile())     # House Party command channel (Speda alias)
    profiles.register(UltronProfile())      # academic research
    profiles.register(AtomixProfile())      # personal health
    profiles.register(SentinelProfile())    # finance
    profiles.register(NightCrawlerProfile())  # OSINT / web surveillance
    profiles.register(CenturionProfile())   # cyber security
    profiles.register(OptimusProfile())     # systems / code / infrastructure
    profiles.register(OrionProfile())       # Mark VI maintenance — memory custodian

    dispatcher = AgentDispatcher()

    # ── 2.6 Telegram bot fleet (one bot per agent with a presence) ─────────────
    # Built here (after profiles, before Tier-1 skills) so the delivery skill can
    # take the registry in its constructor — same pattern as the dispatch skill.
    # Only profiles that declare telegram_enabled get a bot; only those with a
    # configured token are actually constructed.
    from app.telegram.registry import TelegramBotRegistry

    telegram_agent_ids = {
        p.agent_id for p in profiles.roster() if getattr(p, "telegram_enabled", False)
    }
    telegram_bots = TelegramBotRegistry.from_config(telegram_agent_ids)

    # ── 3. Capability Registry ─────────────────────────────────────────────────
    from app.core.registry import CapabilityRegistry

    registry = CapabilityRegistry(client=llm_client, profiles=profiles)

    # Tier 0 — The Legion (wire name "Task", MUST be registered first).
    # Provider-agnostic workers: model resolution routes through the parent
    # agent's profile, so the Legion runs on whatever provider the chat does.
    registry.register_legion()

    # Tier 1 — Python Skills
    # read_skill is the progressive-disclosure meta-tool (registered first so it's
    # always available when Claude wants to load full SKILL.md instructions).
    from app.skills.automations import AutomationsSkill
    from app.skills.budget import BudgetModeSkill
    from app.skills.dispatch import AgentChannelSkill, DispatchAgentSkill, DispatchStatusSkill, HousePartySkill
    from app.skills.sandbox import RunCommandSkill, DeliverFileSkill
    from app.skills.toolsets import UseToolsetSkill
    from app.skills.documents import DocumentsSkill
    from app.skills.hisar import HisarSkill
    from app.skills.save_file import SaveFileSkill
    from app.skills.atomix_reports import GenerateDailyTrainingProgramSkill
    from app.skills.memory import MemorySkill
    from app.skills.memory_write import (
        LedgerAppendSkill,
        NarrativeReviseSkill,
        RegistryUpsertSkill,
    )
    from app.skills.observations import (
        ForgetObservationSkill,
        RecordObservationSkill,
        SearchMemorySkill,
    )
    from app.skills.news import (
        NewsDeepDiveSkill,
        NewsHeadlinesSkill,
        NewsWatchSkill,
        ReadArticleSkill,
    )
    from app.skills.notifications import NotificationsSkill
    from app.skills.telegram import SendTelegramFileSkill, SendTelegramMessageSkill
    from app.skills.osint import OSINT_SKILLS
    from app.skills.read_skill import ReadSkillSkill
    from app.skills.search_history import SearchHistorySkill
    from app.skills.semantic_search import SemanticSearchSkill
    from app.skills.stt import STTSkill
    from app.skills.system import SystemSkill
    from app.skills.system_ops import SystemOpsSkill
    from app.skills.tts import TTSSkill

    await registry.register_skill(ReadSkillSkill())
    await registry.register_skill(MemorySkill())
    # The observation tier — the sourced, addressable facts beneath the memory
    # files. Registered next to the memory tool because they are one capability
    # with two layers: the file is what gets read, the observation is what can be
    # traced. See app/skills/observations.py.
    # Shape-aware writes: the agent names a month, a date, a person or a chapter,
    # and the placement follows from it. See app/skills/memory_write.py.
    await registry.register_skill(LedgerAppendSkill())
    await registry.register_skill(RegistryUpsertSkill())
    await registry.register_skill(NarrativeReviseSkill())
    await registry.register_skill(RecordObservationSkill())
    await registry.register_skill(SearchMemorySkill())
    await registry.register_skill(ForgetObservationSkill())
    await registry.register_skill(SearchHistorySkill())
    await registry.register_skill(SemanticSearchSkill())
    await registry.register_skill(TTSSkill())
    await registry.register_skill(STTSkill())
    await registry.register_skill(NotificationsSkill())
    await registry.register_skill(SendTelegramMessageSkill(telegram_bots))
    await registry.register_skill(SendTelegramFileSkill(telegram_bots))
    await registry.register_skill(DocumentsSkill())
    await registry.register_skill(SaveFileSkill())
    await registry.register_skill(GenerateDailyTrainingProgramSkill())   # Atomix-only (restricted_to)
    await registry.register_skill(HisarSkill())
    await registry.register_skill(SystemSkill())
    await registry.register_skill(SystemOpsSkill())   # Orion-only (restricted_to)
    await registry.register_skill(BudgetModeSkill())
    await registry.register_skill(RunCommandSkill())
    await registry.register_skill(DeliverFileSkill())
    # News desk — Tier 1 (RSS store + watchlist + free article read) and Tier 2
    # (NewsData.io analyst, quota-budgeted).
    await registry.register_skill(NewsHeadlinesSkill())
    await registry.register_skill(NewsWatchSkill())
    await registry.register_skill(NewsDeepDiveSkill())
    await registry.register_skill(ReadArticleSkill())
    # Navigation desk — traffic-aware routing + place search (Google Maps
    # Platform). Backend-only key; clients render the ```map fence.
    from app.skills.navigation import GetRouteSkill, FindPlacesSkill
    await registry.register_skill(GetRouteSkill())
    await registry.register_skill(FindPlacesSkill())
    # Aircraft desk — live ADS-B lookup by tail number (airplanes.live,
    # keyless). Read-only + network-gated. Clients render the ```aircraft fence.
    from app.skills.aircraft import TrackAircraftSkill
    await registry.register_skill(TrackAircraftSkill())
    # Weather desk — current conditions + forecast (Open-Meteo, keyless).
    # Read-only + network-gated.
    from app.skills.weather import WeatherSkill
    await registry.register_skill(WeatherSkill())
    # Health desk — the owner's biometrics, synced from the phone via Health
    # Connect. Read-only; unrestricted so Speda can relay without a dispatch.
    from app.skills.health_data import HealthDataSkill
    await registry.register_skill(HealthDataSkill())

    # The browser — Playwright in its own container (packages/browser). The plan B
    # under every HTTP-only read path, and the only way into the owner's logged-in
    # portals. Skipped entirely when BROWSER_URL is unset: three tools advertising
    # a capability that cannot run costs a turn to discover, every turn.
    from app.skills.browser import BROWSER_SKILLS, browser_available

    if browser_available():
        for _browser_skill in BROWSER_SKILLS:
            await registry.register_skill(_browser_skill())
    else:
        logger.warning("browser_skills_skipped", extra={"reason": "BROWSER_URL not set"})

    # Ankara bus desk — live EGO "Otobüs Nerede?" arrivals by stop number. No
    # official API, and the form's POST sits behind a WAF that only a real
    # browser clears (see services/transit.py) — riding on the sidecar above,
    # so it needs the same BROWSER_URL and is gated the same way.
    if browser_available():
        from app.skills.transit import BusArrivalsSkill
        await registry.register_skill(BusArrivalsSkill())

    # Academic desk — Ultron's course attendance ledger, written from the watch
    # (Ultron Wear) and read back here. check_attendance is read-only and
    # unrestricted so Speda can answer "kaç hakkım kaldı" without a dispatch;
    # ask_attendance is the push side, normally driven by the n8n per-lecture
    # trigger. save_schedule is the other direction — the owner's timetable,
    # authored in chat, replacing what the watch caches — and pushes a resync
    # over the same FCM channel ask_attendance uses. See docs/ULTRON_WEAR.md.
    from app.skills.attendance import AskAttendanceSkill, AttendanceStatusSkill
    from app.skills.schedule import SaveScheduleSkill
    await registry.register_skill(AttendanceStatusSkill())
    await registry.register_skill(AskAttendanceSkill())
    await registry.register_skill(SaveScheduleSkill())
    # Persistent reminders — closing one from chat ("aldım") so the 5-minute
    # nag stops. The button on the reminder itself resolves without a turn.
    from app.skills.reminders import RemindersSkill
    await registry.register_skill(RemindersSkill(telegram_bots))
    await registry.register_skill(UseToolsetSkill())
    # Progressive tool disclosure — resolves a deferred tool's full schema on
    # demand. Must be registered (and never itself deferred), or the tools named
    # in the prompt's "Additional tools" index have no way to become callable.
    from app.skills.tool_search import ToolSearchSkill
    await registry.register_skill(ToolSearchSkill())
    # Kept in a local var (not thrown away like most Tier-1 skills) — its
    # action='test' needs engine refs that do not exist yet at this point in
    # startup, wired in later at the same spot the trigger reporters are.
    automations_skill = AutomationsSkill()
    await registry.register_skill(automations_skill)
    await registry.register_skill(DispatchAgentSkill(
        dispatcher,
        # Session-scope aliases (warroom) are not dispatch targets — keep them
        # out of the tool schema, matching AgentDispatcher.known_agents().
        [(p.agent_id, p.domain) for p in profiles.roster() if p.dispatch_target],
    ))
    await registry.register_skill(AgentChannelSkill())
    await registry.register_skill(DispatchStatusSkill())
    await registry.register_skill(HousePartySkill())
    # Orion's push of the composed owner memory out to a connected Forge peer
    # (docs: the Forge owner-memory bridge). Shares `dispatcher` with
    # DispatchAgentSkill above — same late-bind-via-wire() pattern, since
    # WebSocketManager doesn't exist yet at this point in startup.
    from app.skills.forge_sync import SyncOwnerMemoryToForgeSkill
    await registry.register_skill(SyncOwnerMemoryToForgeSkill(dispatcher))
    # Emergency inbound containment. Owner-only by construction (the skill
    # refuses any non-user trigger) — see app/skills/lockdown.py.
    from app.skills.lockdown import LockdownProtocolSkill

    await registry.register_skill(LockdownProtocolSkill())
    # Host resource reclamation. Orion/Optimus only (restricted_to), and the
    # destructive tiers refuse any non-user trigger — see app/skills/lifeboat.py.
    from app.skills.lifeboat import LifeboatProtocolSkill

    await registry.register_skill(LifeboatProtocolSkill())
    # Moving the deployment to a new domain. Orion/Optimus only, and every
    # phase refuses a non-user trigger — see app/skills/doormat.py.
    from app.skills.doormat import DoormatProtocolSkill

    await registry.register_skill(DoormatProtocolSkill())
    # Database backup to the owner's Drive. Orion/Optimus only; `backup` is the
    # one action an automated trigger may take — see app/skills/octavius.py.
    from app.skills.octavius import OctaviusProtocolSkill

    await registry.register_skill(OctaviusProtocolSkill())
    # The owner's launch rail. Speda only, owner-only, and it can ARM but never
    # fire — the countdown on their screen is what fires. See app/skills/skyfall.py.
    from app.skills.skyfall import SkyfallProtocolSkill

    await registry.register_skill(SkyfallProtocolSkill())
    # Legion background-ticket retrieval (Tier 0's async mode companion).
    from app.skills.legion import LegionStatusSkill
    await registry.register_skill(LegionStatusSkill())

    # OSINT / threat-intelligence suite (ip-api, AbuseIPDB, abuse.ch URLhaus/
    # ThreatFox/MalwareBazaar, HIBP Pwned Passwords, Ahmia dark-web search).
    # All read-only + network-gated (dropped in the Dead Zone).
    for _osint_skill in OSINT_SKILLS:
        await registry.register_skill(_osint_skill())

    # Tier 2 — MCP Servers
    from app.mcp.servers import register_all_mcp_servers

    await register_all_mcp_servers(registry)

    # Tier 3 — OSS Adapters
    from app.adapters.gpt_researcher import GptResearcherAdapter
    from app.adapters.shannon import ShannonAdapter

    await registry.register_adapter(GptResearcherAdapter())
    await registry.register_adapter(ShannonAdapter())

    # ── 4. Health checks — non-fatal (degraded = logged, not fatal) ────────────
    health = await registry.health_check_all()
    degraded = [name for name, ok in health.items() if not ok]
    if degraded:
        logger.warning("startup_adapters_degraded", extra={"degraded": degraded})
    logger.info(
        "startup_registry_ready",
        extra={"tools": len(registry.list_tools()), "degraded": degraded},
    )

    # ── 4. WebSocket Manager + Agent Registry + External Chat Proxy ────────────
    from app.core.agent_registry import AgentRegistry
    from app.core.external_proxy import ExternalAgentProxy
    from app.services.pending_asks import PendingAsks
    from app.skills.memory import MemoryRecallCache
    from app.websocket.manager import WebSocketManager

    # Constructed here rather than at 7 because the external proxy needs it too:
    # an external peer runs the owner's turn, so it must know what the owner's
    # in-process agents know. One instance for the process (Rule 6).
    memory_cache = MemoryRecallCache()

    ws_manager = WebSocketManager()
    agent_registry = AgentRegistry(ws_manager)
    agent_proxy = ExternalAgentProxy(ws_manager, memory_cache)
    pending_asks = PendingAsks(ws_manager)

    # ── 5. Session Manager ─────────────────────────────────────────────────────
    from app.core.session_manager import SessionManager

    session_manager = SessionManager()

    # ── 7. Orchestrator (reuses the client already injected into the registry) ──
    # Profiles were constructed at 2.5 — the dispatch skill's schema needed them.
    from app.core.orchestrator import AgentOrchestrator
    from app.services.welcome import WelcomeCache

    # One instance each for the process (Rule 6) — threaded into the
    # orchestrator/router instead of living as bare module globals.
    # (memory_cache is built at 4, above — the external proxy needs it.)
    welcome_cache = WelcomeCache()
    orchestrator = AgentOrchestrator(registry, llm_client, profiles, memory_cache)

    # Late-bind the dispatch primitive now that the full engine exists.
    dispatcher.wire(
        orchestrator=orchestrator,
        profiles=profiles,
        session_manager=session_manager,
        ws_manager=ws_manager,
        # The same registry the background legionnaires stream into. Shared
        # rather than duplicated: both mint their ticket from an AgentMessage
        # row id, so the id spaces cannot collide, and sharing means a spawned
        # peer job appears in the tray and attaches over /legion/attach with no
        # second endpoint and no client change.
        runs=registry.legion_runs,
    )

    # ── 7.5 Telegram channel — gateway + ingress ───────────────────────────────
    # The gateway turns an inbound update into a normal orchestrator run; it needs
    # the full engine, so it is built here (after the orchestrator + proxy exist)
    # and ingress is started per settings.telegram_mode (webhook sets per-bot
    # webhooks; polling spawns one long-poll task per bot; off = outbound-only).
    from app.telegram.gateway import TelegramGateway

    # The detached turn runner is created HERE, before the gateway and its
    # pollers start, so /break and steering can find an in-flight turn from the
    # first message. It is otherwise the same instance the rest of the app uses
    # via app.state.turns below.
    from app.core.turn_runner import TurnRegistry

    turns = TurnRegistry(session_manager)

    telegram_gateway = TelegramGateway(
        orchestrator=orchestrator,
        session_manager=session_manager,
        profiles=profiles,
        bots=telegram_bots,
        ws_manager=ws_manager,
        agent_proxy=agent_proxy,
        dispatcher=dispatcher,
        turns=turns,
    )
    telegram_poll_tasks = await telegram_bots.start(telegram_gateway)

    # ── 8. Inject into app.state ───────────────────────────────────────────────
    app.state.registry = registry
    app.state.agent_registry = agent_registry
    app.state.agent_proxy = agent_proxy
    # Permission asks relayed from external peers (the Forge's safety gate).
    app.state.pending_asks = pending_asks
    app.state.orchestrator = orchestrator
    app.state.memory_cache = memory_cache
    app.state.welcome_cache = welcome_cache
    app.state.ws_manager = ws_manager
    app.state.session_manager = session_manager
    app.state.profiles = profiles
    app.state.dispatcher = dispatcher
    app.state.telegram_bots = telegram_bots
    app.state.telegram_gateway = telegram_gateway

    # ── 8.5 Detached turn runner (BgOps) ───────────────────────────────────────
    # Runs chat turns in their own asyncio tasks, decoupled from the HTTP request,
    # so a client disconnect can never lose a turn and a client can re-attach to
    # a live stream. One instance on app.state (Rule 6). Created above (before the
    # gateway) so /break and steering have it from the first message; published
    # here.
    app.state.turns = turns

    # Background legionnaires report in when they finish. Wired HERE, not at
    # Tier-0 registration: the callback closes over the orchestrator, the turn
    # registry and the session manager, and none of those exist yet when the
    # Legion is registered first. A completed worker now starts a real push turn
    # on the agent that deployed it instead of leaving the result sitting in a
    # ticket until someone thinks to ask.
    from app.core.trigger_runner import make_dispatch_reporter, make_legion_reporter

    reporter_deps = {
        "profiles": profiles,
        "orchestrator": orchestrator,
        "turns": app.state.turns,
        "session_manager": session_manager,
        "telegram_bots": telegram_bots,
        "agent_proxy": agent_proxy,
        "ws_manager": ws_manager,
    }
    registry.set_legion_report_hook(make_legion_reporter(**reporter_deps))
    # Same dependency set, same reason: action='test' on manage_automations
    # needs to start a real trigger turn, and this is the first point in
    # startup where everything it needs actually exists.
    automations_skill.wire(**reporter_deps)
    # And the same for a BACKGROUND dispatch to another agent: when it lands, the
    # agent that sent it is woken with the answer and delivers it, instead of the
    # owner having to ask whether it finished.
    dispatcher.set_report_hook(make_dispatch_reporter(**reporter_deps))

    # ── 9. Child processes — the local sandbox + the Forge peer ────────────────
    # Both are best-effort: a missing dependency logs a warning and Speda keeps
    # running. The sandbox gives the run_command skill a computer without Docker;
    # the Forge peer is the standalone Optimus engine that connects back over the
    # agents WebSocket (in-process OptimusProfile is the fallback when offline).
    from app.services.forge_peer import ForgePeerLauncher
    from app.services.sandbox_launcher import SandboxLauncher

    sandbox_launcher = SandboxLauncher()
    forge_launcher = ForgePeerLauncher()
    await sandbox_launcher.start()
    await forge_launcher.start()
    app.state.sandbox_launcher = sandbox_launcher
    app.state.forge_launcher = forge_launcher

    # ── 10. Close out dispatches orphaned by the previous process ─────────────
    # A dispatch/legion ticket only ever runs in-process, so anything still
    # marked "running" at boot died with the last process. Left alone it shows
    # up in the comms tray as a task that has been working for weeks.
    from app.core.dispatch import sweep_stale_dispatches

    await sweep_stale_dispatches()

    # ── 11. Recover post-turn work the previous process did not finish ────────
    # Same reasoning as the dispatch sweep, one layer down: background_jobs left
    # "running" at boot died with the last process. Unlike dispatches these are
    # retryable, so they go back on the queue and a detached drain works through
    # them while the app serves requests. See app/services/task_queue.py.
    from app.services.task_queue import recover_on_startup

    reclaimed_jobs = await recover_on_startup()

    # ── 12. Re-apply containment if the Lockdown Protocol is engaged ──────────
    # Firewall rules live in the kernel, not on disk. A restart during an
    # incident would otherwise reopen the sealed ports while every client still
    # displayed LOCKDOWN ACTIVE. No-op when the flag is clear.
    from app.services.lockdown import reconcile_on_startup

    await reconcile_on_startup()

    logger.info(
        "startup_complete",
        extra={
            "tools_registered": len(registry.list_tools()),
            "api_key_set": settings.speda_api_key != "dev-key",
            "anthropic_key_set": settings.anthropic_api_key != "not-set",
            "jobs_reclaimed": reclaimed_jobs,
        },
    )

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("shutdown_begin")
    await app.state.turns.shutdown()
    await dispatcher.shutdown()
    await registry.legion_shutdown()
    await forge_launcher.stop()
    await sandbox_launcher.stop()
    for task in telegram_poll_tasks:
        task.cancel()
    await telegram_bots.aclose()
    await registry.shutdown_adapters()
    await close_db()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    # Interactive docs and the OpenAPI schema expose the full API surface, so
    # they are DISABLED outside DEBUG. On an internet-facing server they must
    # never be public (CLAUDE.md / endpoint-leak hardening).
    docs_enabled = settings.debug
    app = FastAPI(
        title=f"Igor — {AGENT_NAME} Backend",
        description="Igor, the agent backend core — identity defined in prompts/core/01_identity.md",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # ── CORS — locked down ──────────────────────────────────────────────────────
    # The API is header-authenticated (Bearer / X-API-Key), which browsers never
    # attach cross-origin automatically, so CSRF risk is low — but we still refuse
    # to advertise "*". Origins come from config; DEBUG additionally allows local
    # dev servers.
    #
    # The packaged desktop client IS a browser origin: the renderer is served
    # over the `app://bundle` custom scheme, registered `standard: true` in
    # packages/heartbreaker/src/main/index.ts precisely so it gets a real
    # (non-opaque) origin. That means its fetches are ordinary cross-origin
    # requests and are preflighted, so `app://bundle` must be allowed here or the
    # desktop app cannot talk to the server at all.
    from fastapi.middleware.cors import CORSMiddleware

    DESKTOP_ORIGIN = "app://bundle"
    origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    if DESKTOP_ORIGIN not in origins:
        origins.append(DESKTOP_ORIGIN)

    LOCAL_ORIGIN_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")
    allow_origin_regex = LOCAL_ORIGIN_RE.pattern if settings.debug else None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-N8N-Secret"],
    )

    # ── Global exception handler — never leak internals ─────────────────────────
    # Any unhandled exception is logged in full server-side and returned to the
    # caller as a generic 500. Prevents stack traces / paths / SQL from leaking
    # through endpoints. HTTPExceptions keep their intended status/detail.
    #
    # It carries the CORS header itself, which CORSMiddleware cannot do for it:
    # Starlette runs the error handler in ServerErrorMiddleware, OUTSIDE the whole
    # middleware stack, so a 500 leaves without Access-Control-Allow-Origin and a
    # browser client discards it unread — the desktop app then reports a genuine
    # server error as "couldn't reach the backend", which sends every diagnosis
    # after the wrong thing. A 500 must arrive as a 500.
    from fastapi.responses import JSONResponse as _JSONResponse

    def _cors_headers(request) -> dict[str, str]:  # noqa: ANN001
        origin = request.headers.get("origin")
        if not origin:
            return {}
        if origin in origins or (allow_origin_regex and LOCAL_ORIGIN_RE.match(origin)):
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
        return {}

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):  # noqa: ANN001
        logger.error(
            "unhandled_exception",
            extra={"path": request.url.path, "error": str(exc)},
            exc_info=exc,
        )
        return _JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
            headers=_cors_headers(request),
        )

    # Middleware (Starlette applies these in REVERSE registration order, so the
    # LAST added runs FIRST: security headers wrap everything, then auth gates
    # before any router logic).
    from app.middleware.auth import AuthMiddleware
    from app.middleware.security import SecurityHeadersMiddleware

    app.add_middleware(AuthMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # Routers
    from app.routers import admin, agents, automations, browser as browser_router, chat, health, trigger, import_chats, files, media, connections, mail, outlook, memory, navigation, aircraft, reminders, telegram, news, academic, web_watch, voice, config as config_router, hisar, lifeboat as lifeboat_router, doormat as doormat_router, octavius as octavius_router, skyfall as skyfall_router, legion as legion_router

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(legion_router.router)
    app.include_router(trigger.router)
    app.include_router(agents.router)
    app.include_router(admin.router)
    app.include_router(import_chats.router)
    app.include_router(files.router)
    app.include_router(media.router)
    app.include_router(connections.router)
    # Web portals live under /connections/portals — same family as the OAuth
    # connections above, different enough to keep in its own module.
    app.include_router(browser_router.router)
    app.include_router(automations.router)
    app.include_router(memory.router)
    app.include_router(telegram.router)
    app.include_router(news.router)
    app.include_router(academic.router)
    app.include_router(mail.router)
    app.include_router(outlook.router)
    app.include_router(web_watch.router)
    app.include_router(lifeboat_router.router)
    app.include_router(doormat_router.router)
    app.include_router(octavius_router.router)
    app.include_router(skyfall_router.router)
    app.include_router(reminders.router)
    app.include_router(navigation.router)
    app.include_router(aircraft.router)
    app.include_router(voice.router)
    app.include_router(config_router.router)
    app.include_router(hisar.router)

    return app


app = create_app()
