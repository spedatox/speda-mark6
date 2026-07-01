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

    # ── 2. LLM Client (created early — registry needs it for sub-agents) ────────
    from app.services.llm_client import LLMClient

    llm_client = LLMClient()

    # ── 3. Capability Registry ─────────────────────────────────────────────────
    from app.core.registry import CapabilityRegistry

    registry = CapabilityRegistry(client=llm_client)

    # Tier 0 — Task tool (SDK built-in, MUST be registered first)
    registry.register_task_tool()

    # Tier 1 — Python Skills
    # read_skill is the progressive-disclosure meta-tool (registered first so it's
    # always available when Claude wants to load full SKILL.md instructions).
    from app.skills.automations import AutomationsSkill
    from app.skills.budget import BudgetModeSkill
    from app.skills.sandbox import RunCommandSkill, DeliverFileSkill
    from app.skills.toolsets import UseToolsetSkill
    from app.skills.documents import DocumentsSkill
    from app.skills.save_file import SaveFileSkill
    from app.skills.memory import MemorySkill
    from app.skills.notifications import NotificationsSkill
    from app.skills.osint import OSINT_SKILLS
    from app.skills.read_skill import ReadSkillSkill
    from app.skills.search_history import SearchHistorySkill
    from app.skills.stt import STTSkill
    from app.skills.system import SystemSkill
    from app.skills.tts import TTSSkill

    await registry.register_skill(ReadSkillSkill())
    await registry.register_skill(MemorySkill())
    await registry.register_skill(SearchHistorySkill())
    await registry.register_skill(TTSSkill())
    await registry.register_skill(STTSkill())
    await registry.register_skill(NotificationsSkill())
    await registry.register_skill(DocumentsSkill())
    await registry.register_skill(SaveFileSkill())
    await registry.register_skill(SystemSkill())
    await registry.register_skill(BudgetModeSkill())
    await registry.register_skill(RunCommandSkill())
    await registry.register_skill(DeliverFileSkill())
    await registry.register_skill(UseToolsetSkill())
    await registry.register_skill(AutomationsSkill())

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

    # ── 4. WebSocket Manager + Agent Registry ──────────────────────────────────
    from app.core.agent_registry import AgentRegistry
    from app.websocket.manager import WebSocketManager

    ws_manager = WebSocketManager()
    agent_registry = AgentRegistry(ws_manager)

    # ── 5. Session Manager ─────────────────────────────────────────────────────
    from app.core.session_manager import SessionManager

    session_manager = SessionManager()

    # ── 6. Profiles (multi-tenant) ─────────────────────────────────────────────
    # One ProfileRegistry holds every enabled in-process agent, addressed by
    # agent_id. Phase 1: SPEDA only — the Superior Six profiles are authored in
    from app.profiles.registry import ProfileRegistry
    from app.profiles.atomix import AtomixProfile
    from app.profiles.centurion import CenturionProfile
    from app.profiles.nightcrawler import NightCrawlerProfile
    from app.profiles.optimus import OptimusProfile
    from app.profiles.sentinel import SentinelProfile
    from app.profiles.speda import SPEDAProfile
    from app.profiles.ultron import UltronProfile

    profiles = ProfileRegistry()
    profiles.register(SPEDAProfile())       # orchestrator
    profiles.register(UltronProfile())      # academic research
    profiles.register(AtomixProfile())      # personal health
    profiles.register(SentinelProfile())    # finance
    profiles.register(NightCrawlerProfile())  # OSINT / web surveillance
    profiles.register(CenturionProfile())   # cyber security
    profiles.register(OptimusProfile())     # systems / code / infrastructure

    # ── 7. Orchestrator (reuses the client already injected into the registry) ──
    from app.core.orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator(registry, llm_client, profiles)

    # ── 7.5 Proactive delivery + automation engine clients ─────────────────────
    from app.services.telegram import TelegramClient

    telegram = TelegramClient()

    # ── 8. Inject into app.state ───────────────────────────────────────────────
    app.state.registry = registry
    app.state.agent_registry = agent_registry
    app.state.orchestrator = orchestrator
    app.state.ws_manager = ws_manager
    app.state.session_manager = session_manager
    app.state.profiles = profiles
    app.state.telegram = telegram

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
    await registry.shutdown_adapters()
    await close_db()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    # Interactive docs and the OpenAPI schema expose the full API surface, so
    # they are DISABLED outside DEBUG. On an internet-facing server they must
    # never be public (CLAUDE.md / endpoint-leak hardening).
    docs_enabled = settings.debug
    app = FastAPI(
        title=f"{AGENT_NAME} Backend",
        description="Agent backend core — identity defined in prompts/core/01_identity.md",
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
    if settings.debug:
        origins += ["http://localhost:5173", "http://127.0.0.1:5173"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
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
    from app.routers import admin, agents, automations, chat, health, trigger, import_chats, files, connections, memory

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

    return app


app = create_app()
