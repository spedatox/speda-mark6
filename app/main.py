import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import configure_logging, settings

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

    # ── 2. Capability Registry ─────────────────────────────────────────────────
    from app.core.registry import CapabilityRegistry

    registry = CapabilityRegistry()

    # Tier 0 — Task tool (SDK built-in, MUST be registered first)
    registry.register_task_tool()

    # Tier 1 — Python Skills
    from app.skills.documents import DocumentsSkill
    from app.skills.notifications import NotificationsSkill
    from app.skills.stt import STTSkill
    from app.skills.system import SystemSkill
    from app.skills.tts import TTSSkill

    await registry.register_skill(TTSSkill())
    await registry.register_skill(STTSkill())
    await registry.register_skill(NotificationsSkill())
    await registry.register_skill(DocumentsSkill())
    await registry.register_skill(SystemSkill())

    # Tier 2 — MCP Servers
    from app.mcp.servers import register_all_mcp_servers

    await register_all_mcp_servers(registry)

    # Tier 3 — OSS Adapters
    from app.adapters.gpt_researcher import GptResearcherAdapter
    from app.adapters.shannon import ShannonAdapter

    await registry.register_adapter(GptResearcherAdapter())
    await registry.register_adapter(ShannonAdapter())

    # ── 3. Health checks — non-fatal (degraded = logged, not fatal) ────────────
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

    # ── 6. Profile ─────────────────────────────────────────────────────────────
    from app.profiles.speda import SPEDAProfile

    profile = SPEDAProfile()

    # ── 7. Anthropic Client + Orchestrator ─────────────────────────────────────
    from app.core.orchestrator import AgentOrchestrator
    from app.services.anthropic_client import AnthropicClient

    orchestrator = AgentOrchestrator(registry, AnthropicClient(), profile)

    # ── 8. Inject into app.state ───────────────────────────────────────────────
    app.state.registry = registry
    app.state.agent_registry = agent_registry
    app.state.orchestrator = orchestrator
    app.state.ws_manager = ws_manager
    app.state.session_manager = session_manager
    app.state.profile = profile

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
    app = FastAPI(
        title="SPEDA Mark VI",
        description="Specialized Personal Executive Digital Assistant — backend core",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Middleware (applied in reverse order — auth runs first)
    from app.middleware.auth import APIKeyMiddleware

    app.add_middleware(APIKeyMiddleware)

    # Routers
    from app.routers import admin, agents, chat, health, trigger

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(trigger.router)
    app.include_router(agents.router)
    app.include_router(admin.router)

    return app


app = create_app()
