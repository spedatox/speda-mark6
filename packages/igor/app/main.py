import logging
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
    profiles.register(WarRoomProfile())     # House Party command channel (SPEDA alias)
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
    from app.skills.save_file import SaveFileSkill
    from app.skills.memory import MemorySkill
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
    await registry.register_skill(SearchHistorySkill())
    await registry.register_skill(SemanticSearchSkill())
    await registry.register_skill(TTSSkill())
    await registry.register_skill(STTSkill())
    await registry.register_skill(NotificationsSkill())
    await registry.register_skill(SendTelegramMessageSkill(telegram_bots))
    await registry.register_skill(SendTelegramFileSkill(telegram_bots))
    await registry.register_skill(DocumentsSkill())
    await registry.register_skill(SaveFileSkill())
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
    await registry.register_skill(UseToolsetSkill())
    await registry.register_skill(AutomationsSkill())
    await registry.register_skill(DispatchAgentSkill(
        dispatcher,
        # Session-scope aliases (warroom) are not dispatch targets — keep them
        # out of the tool schema, matching AgentDispatcher.known_agents().
        [(p.agent_id, p.domain) for p in profiles.roster() if p.dispatch_target],
    ))
    await registry.register_skill(AgentChannelSkill())
    await registry.register_skill(DispatchStatusSkill())
    await registry.register_skill(HousePartySkill())
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
    from app.websocket.manager import WebSocketManager

    ws_manager = WebSocketManager()
    agent_registry = AgentRegistry(ws_manager)
    agent_proxy = ExternalAgentProxy(ws_manager)

    # ── 5. Session Manager ─────────────────────────────────────────────────────
    from app.core.session_manager import SessionManager

    session_manager = SessionManager()

    # ── 7. Orchestrator (reuses the client already injected into the registry) ──
    # Profiles were constructed at 2.5 — the dispatch skill's schema needed them.
    from app.core.orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator(registry, llm_client, profiles)

    # Late-bind the dispatch primitive now that the full engine exists.
    dispatcher.wire(
        orchestrator=orchestrator,
        profiles=profiles,
        session_manager=session_manager,
        ws_manager=ws_manager,
    )

    # ── 7.5 Telegram channel — gateway + ingress ───────────────────────────────
    # The gateway turns an inbound update into a normal orchestrator run; it needs
    # the full engine, so it is built here (after the orchestrator + proxy exist)
    # and ingress is started per settings.telegram_mode (webhook sets per-bot
    # webhooks; polling spawns one long-poll task per bot; off = outbound-only).
    from app.telegram.gateway import TelegramGateway

    telegram_gateway = TelegramGateway(
        orchestrator=orchestrator,
        session_manager=session_manager,
        profiles=profiles,
        bots=telegram_bots,
        ws_manager=ws_manager,
        agent_proxy=agent_proxy,
    )
    telegram_poll_tasks = await telegram_bots.start(telegram_gateway)

    # ── 8. Inject into app.state ───────────────────────────────────────────────
    app.state.registry = registry
    app.state.agent_registry = agent_registry
    app.state.agent_proxy = agent_proxy
    app.state.orchestrator = orchestrator
    app.state.ws_manager = ws_manager
    app.state.session_manager = session_manager
    app.state.profiles = profiles
    app.state.dispatcher = dispatcher
    app.state.telegram_bots = telegram_bots
    app.state.telegram_gateway = telegram_gateway

    # ── 8.5 Detached turn runner (BgOps) ───────────────────────────────────────
    # Runs chat turns in their own asyncio tasks, decoupled from the HTTP request,
    # so a client disconnect can never lose a turn and a client can re-attach to
    # a live stream. One instance on app.state (Rule 6).
    from app.core.turn_runner import TurnRegistry

    app.state.turns = TurnRegistry(session_manager)

    # ── 9. Child processes — the local sandbox + the Forge peer ────────────────
    # Both are best-effort: a missing dependency logs a warning and SPEDA keeps
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

    logger.info(
        "startup_complete",
        extra={
            "tools_registered": len(registry.list_tools()),
            "api_key_set": settings.speda_api_key != "dev-key",
            "anthropic_key_set": settings.anthropic_api_key != "not-set",
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

    # ── Global exception handler — never leak internals ─────────────────────────
    # Any unhandled exception is logged in full server-side and returned to the
    # caller as a generic 500. Prevents stack traces / paths / SQL from leaking
    # through endpoints. HTTPExceptions keep their intended status/detail.
    from fastapi.responses import JSONResponse as _JSONResponse

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):  # noqa: ANN001
        logger.error(
            "unhandled_exception",
            extra={"path": request.url.path, "error": str(exc)},
            exc_info=exc,
        )
        return _JSONResponse(status_code=500, content={"detail": "Internal server error"})

    # ── CORS — locked down ──────────────────────────────────────────────────────
    # The API is header-authenticated (Bearer / X-API-Key), which browsers never
    # attach cross-origin automatically, so CSRF risk is low — but we still refuse
    # to advertise "*". Origins come from config; DEBUG additionally allows local
    # dev servers. The packaged desktop client is not a browser origin and is
    # unaffected by CORS.
    from fastapi.middleware.cors import CORSMiddleware

    origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$" if settings.debug else None,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-N8N-Secret"],
    )

    # Middleware (Starlette applies these in REVERSE registration order, so the
    # LAST added runs FIRST: security headers wrap everything, then auth gates
    # before any router logic).
    from app.middleware.auth import AuthMiddleware
    from app.middleware.security import SecurityHeadersMiddleware

    app.add_middleware(AuthMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # Routers
    from app.routers import admin, agents, automations, chat, health, trigger, import_chats, files, connections, memory, telegram, news, config as config_router

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(trigger.router)
    app.include_router(agents.router)
    app.include_router(admin.router)
    app.include_router(import_chats.router)
    app.include_router(files.router)
    app.include_router(connections.router)
    app.include_router(automations.router)
    app.include_router(memory.router)
    app.include_router(telegram.router)
    app.include_router(news.router)
    app.include_router(config_router.router)

    return app


app = create_app()
