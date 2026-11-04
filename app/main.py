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
    from app.database import AsyncSessionLocal
    from app.models.user import User
    async with AsyncSessionLocal() as db, db.begin():
        owner=await db.get(User,1)
        if owner is None:
            db.add(User(id=1, name="Owner", timezone=settings.owner_timezone))
        else:
            from app.services.config_store import managed_settings
            if 'owner_timezone' in managed_settings(): owner.timezone=settings.owner_timezone
    logger.info("startup_db_ready")

    # ── 2. Capability Registry ─────────────────────────────────────────────────
    from app.core.registry import CapabilityRegistry

    from app.database import AsyncSessionLocal
    registry = CapabilityRegistry(AsyncSessionLocal)

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
    from app.skills.budget import BudgetModeSkill
    budget_mode_skill = BudgetModeSkill()
    await registry.register_skill(budget_mode_skill)
    from app.integration import domain_skills
    from app.skills.tool_search import ToolSearchSkill
    for skill in domain_skills():
        await registry.register_skill(skill)
    await registry.register_skill(ToolSearchSkill(registry))
    from app.skills.mcp_data import MCPDataSkill
    await registry.register_skill(MCPDataSkill(registry))

    from app.database import AsyncSessionLocal
    from app.services.memory_store import MemoryStore
    from app.services.memory_runtime import MemoryRuntime
    from app.skills.memory_lookup import MemoryLookupSkill

    memory_store = MemoryStore(AsyncSessionLocal)
    memory_runtime = MemoryRuntime(memory_store)

    # Tier 2 — MCP Servers
    from app.mcp.servers import register_all_mcp_servers
    from app.services.connections import ConnectionManager, clone_connection
    from app.services.oauth import OAuthService
    connections = ConnectionManager(registry)
    oauth = OAuthService(AsyncSessionLocal, connections)

    if settings.external_integrations_enabled:
        await register_all_mcp_servers(registry, tokens=oauth.tokens,
            active=connections.store.read().get('active', {}))
    connections.reserved.update(getattr(registry, '_configured_mcp', {}))
    connections.errors.update(getattr(registry, '_mcp_startup_errors', {}))
    connections.builtin_factories.update({name: (lambda c=client: clone_connection(c))
        for name, client in getattr(registry, '_configured_mcp', {}).items()})
    await connections.startup()

    # Tier 3 — OSS Adapters
    from app.adapters.gpt_researcher import GptResearcherAdapter
    from app.adapters.shannon import ShannonAdapter

    if settings.external_integrations_enabled:
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
    from app.profiles.registry import ProfileRegistry
    profiles = ProfileRegistry()

    # ── 7. Anthropic Client + Orchestrator ─────────────────────────────────────
    from app.core.orchestrator import AgentOrchestrator
    from app.services.model_policy import ModelPolicy
    from app.services.llm_client import LLMClient

    model_policy = ModelPolicy(AsyncSessionLocal, profile, settings)
    budget_mode_skill.policy = model_policy
    from app.services.global_budget import GlobalBudget
    global_budget = GlobalBudget(AsyncSessionLocal)
    client = LLMClient(model_policy, settings, spending=global_budget)
    orchestrator = AgentOrchestrator(registry, client, profile, memory_runtime, model_policy,
        profiles=profiles)
    from app.services.task_runner import TaskRunner
    registry._task_runner = TaskRunner(orchestrator, model_policy)
    from app.services.chat_turns import ChatTurns
    from app.services.iacon_catalogue import Catalogue
    from app.services.iacon_budget import MaintenanceBudget
    from app.services.iacon_worker import Orion
    from app.services.iacon_telegram import TelegramFollowups
    from app.services.hisar_library_client import client_from_settings
    from app.services.library_indexer import LibraryIndexer
    from app.services.library import LibraryService
    from app.services.library_controls import LibraryControls
    from app.skills.iacon import IaconProposeSkill
    catalogue = Catalogue(memory_store)
    budget = MaintenanceBudget(AsyncSessionLocal, client)
    preparer = Orion(catalogue, budget, model_policy, orchestrator.build_iacon_prompt,
                    enabled=settings.memory_capture_enabled)
    telegram = TelegramFollowups(catalogue, budget, settings)
    indexer = LibraryIndexer(client_from_settings(settings), AsyncSessionLocal,
        max_pages=settings.library_max_pages, enqueue=preparer.enqueue)
    library = LibraryService(indexer, n8n_secret=settings.n8n_secret)
    preparer.library_indexer = indexer
    lookup = MemoryLookupSkill(memory_runtime, library, catalogue)
    await registry.register_skill(lookup)
    from app.skills.search_history import SearchHistorySkill
    await registry.register_skill(SearchHistorySkill(lookup))
    await registry.register_skill(IaconProposeSkill(preparer))
    chat_turns = ChatTurns(session_manager, profile, orchestrator, preparer, AsyncSessionLocal)
    from app.services.coordination import Coordinator
    from app.services.coordination_budget import CoordinationBudget
    from app.skills.collaboration import CollaborationSkill, ProjectStatusSkill, ProjectDelegateSkill, DecisionSkill, ProjectReviewSkill, ProjectReleaseSkill, ProjectCheckpointSkill
    coordination_budget = CoordinationBudget(AsyncSessionLocal, client)
    coordinator = Coordinator(AsyncSessionLocal, profiles, orchestrator, model_policy,
        coordination_budget, preparer=preparer)
    from app.services.projects import Projects
    projects = Projects(AsyncSessionLocal, coordinator, settings)
    coordinator.projects = projects
    await projects.cloud.recover()
    from app.services.project_release import ProjectRelease
    await ProjectRelease(projects).recover()
    from app.services.worker_control import WorkerControl
    worker_control = WorkerControl(coordinator)
    coordinator.workers = worker_control
    registry._task_runner.activity = worker_control
    from app.skills.legion import LegionInspectSkill,LegionControlSkill,LegionStatusSkill
    await registry.register_skill(LegionInspectSkill())
    await registry.register_skill(LegionControlSkill())
    await registry.register_skill(LegionStatusSkill())
    await registry.register_skill(CollaborationSkill())
    await registry.register_skill(DecisionSkill())
    await registry.register_skill(ProjectStatusSkill())
    from app.skills.collaboration import ProjectReadSkill
    await registry.register_skill(ProjectReadSkill())
    await registry.register_skill(ProjectDelegateSkill())
    await registry.register_skill(ProjectReviewSkill())
    await registry.register_skill(ProjectReleaseSkill())
    await registry.register_skill(ProjectCheckpointSkill())

    # ── 8. Inject into app.state ───────────────────────────────────────────────
    app.state.registry = registry
    app.state.worker_control = worker_control
    app.state.connections = connections
    app.state.oauth = oauth
    app.state.agent_registry = agent_registry
    app.state.orchestrator = orchestrator
    app.state.ws_manager = ws_manager
    app.state.session_manager = session_manager
    app.state.profile = profile
    app.state.memory_runtime = memory_runtime
    app.state.memory_store = memory_store
    app.state.memory_preparer = preparer
    app.state.chat_turns = chat_turns
    from app.services.client_compat import ClientCompatibility
    app.state.client_compat = ClientCompatibility(coordinator)
    from app.services.automation_admission import AutomationAdmissions
    app.state.automation_admissions = AutomationAdmissions(coordinator)
    from app.services.automations import Automations
    app.state.automations = Automations(coordinator, app.state.automation_admissions, settings)
    coordinator.automations = app.state.automations
    from app.services.delivery import Deliveries
    from app.services.telegram_gateway import TelegramGateway
    app.state.deliveries = Deliveries(AsyncSessionLocal, settings, profiles)
    coordinator.deliveries = app.state.deliveries
    app.state.telegram_gateway = TelegramGateway(coordinator, app.state.deliveries, settings)
    from app.services.voice import Voice
    app.state.voice = Voice(AsyncSessionLocal, profiles, global_budget, settings)
    coordinator.voice = app.state.voice
    from app.services.reminders import Reminders
    app.state.reminders = Reminders(coordinator, app.state.deliveries)
    coordinator.reminders = app.state.reminders
    from app.services.mail_watch import MailWatches
    app.state.mail_watches = MailWatches(coordinator, oauth, registry)
    coordinator.mail_watches = app.state.mail_watches
    from app.services.configuration import Configuration,PolicyConfiguration
    from app.services.backend_restart import BackendRestart
    app.state.configuration=Configuration(settings,instance_id=app.state.backend_instance_id)
    app.state.policy_configuration=PolicyConfiguration(model_policy,coordination_budget,global_budget,budget)
    from app.services.behavior_config import BehaviorConfiguration
    app.state.behavior_configuration=BehaviorConfiguration(coordinator,registry,app.state.policy_configuration,app.state.configuration)
    app.state.backend_restart=BackendRestart(app.state.configuration,coordinator,getattr(app.state,'request_backend_shutdown',None))
    global_budget.lifecycle=coordinator
    app.state.model_policy = model_policy
    app.state.llm_client = client
    from app.services.model_catalog import ModelCatalog
    app.state.model_catalog = ModelCatalog(client)
    app.state.global_budget = global_budget
    app.state.iacon = catalogue
    from app.services.client_memory import ClientMemory
    app.state.client_memory = ClientMemory(catalogue)
    app.state.iacon_budget = budget
    app.state.orion = preparer
    app.state.iacon_telegram = telegram
    app.state.library = library
    app.state.library_controls = LibraryControls(indexer, budget)
    app.state.profiles = profiles
    app.state.coordinator = coordinator
    app.state.coordination_budget = coordination_budget
    app.state.projects = projects
    from app.services.solus.http import SolusHTTP
    app.state.solus_http = SolusHTTP(AsyncSessionLocal)
    from app.services.hisar_workspaces import HisarWorkspaceService
    app.state.hisar_workspaces = HisarWorkspaceService(settings.project_workspace_root,
        registry._skills['hisar'], configured=bool(settings.hisar_machine_token))
    import asyncio
    recovery = asyncio.create_task(chat_turns.recover())
    if settings.orchestration_rooms_enabled:
        await coordinator.recover()

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
    # Let finite startup recovery close its database sessions before cancelling.
    # Cancelling an SQLite connection while its worker is opening it can leave
    # that worker trying to settle a future after the application loop closes.
    try:
        await asyncio.wait_for(asyncio.shield(recovery),timeout=5)
    except TimeoutError:
        recovery.cancel()
        await asyncio.gather(recovery,return_exceptions=True)
    except Exception:
        logger.warning('startup_recovery_failed',exc_info=False)
    await chat_turns.shutdown()
    await projects.cloud.shutdown()
    await coordinator.shutdown()
    await telegram.close()
    await indexer.aclose()
    await client.close()
    await registry.shutdown_adapters()
    await close_db()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="SPEDA Mark VI",
        description="Specialized Personal Executive Digital Assistant — backend core",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    from uuid import uuid4
    app.state.backend_instance_id=uuid4().hex
    @app.middleware('http')
    async def restarting_gate(request,call_next):
        restart=getattr(request.app.state,'backend_restart',None)
        if restart and restart.requested and request.method not in {'GET','HEAD','OPTIONS'} and request.url.path!='/config/restart':
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503,content={'code':'backend_restarting','detail':'Backend is restarting; retry after it is ready.'})
        return await call_next(request)
    # Middleware (applied in reverse order — auth runs first)
    from app.middleware.auth import APIKeyMiddleware

    app.add_middleware(APIKeyMiddleware)
    from fastapi.middleware.cors import CORSMiddleware
    origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    app.add_middleware(CORSMiddleware,
        allow_origins=list(dict.fromkeys(["app://bundle", *origins])),
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$" if settings.debug else None,
        allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-N8N-Secret", "X-Chat-Format"])

    # Routers
    from app.routers import admin, agents, chat, health, trigger, models, iacon, library

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(trigger.router)
    app.include_router(agents.router)
    app.include_router(admin.router)
    from app.routers.config import router as configuration_router
    app.include_router(configuration_router)
    app.include_router(models.router)
    app.include_router(iacon.router)
    app.include_router(library.router)
    from app.integration import include_routes
    include_routes(app)
    from app.routers.client_compat import router as client_compat_router
    app.include_router(client_compat_router)
    from app.routers.project_workspace import router as project_workspace_router
    app.include_router(project_workspace_router)
    from app.routers.cloud_projects import router as cloud_projects_router
    app.include_router(cloud_projects_router)
    from app.routers.mcp import router as mcp_router
    app.include_router(mcp_router)
    from app.routers.connections import router as connections_router
    app.include_router(connections_router)
    from app.routers.legion import router as legion_router
    app.include_router(legion_router)
    from app.routers.solus import router as solus_router
    app.include_router(solus_router)
    from app.routers.client_agent_settings import router as client_agent_settings_router
    app.include_router(client_agent_settings_router)
    from app.routers.client_memory import router as client_memory_router
    app.include_router(client_memory_router)
    if settings.orchestration_rooms_enabled:
        from app.routers.coordination import router as coordination_router
        app.include_router(coordination_router)
        from pathlib import Path
        from fastapi.responses import FileResponse
        @app.get("/rooms-ui", include_in_schema=False)
        async def rooms_ui():
            # Public shell only: all room/project data and operations require the API key.
            return FileResponse(Path(__file__).parent / "static" / "rooms.html",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    from app.services.coordination_budget import CoordinationError
    from fastapi.responses import JSONResponse
    @app.exception_handler(CoordinationError)
    async def coordination_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "detail": str(exc)})
    from app.profiles.registry import UnknownPersona
    @app.exception_handler(UnknownPersona)
    async def persona_error(request, exc):
        return JSONResponse(status_code=422, content={"code": "unknown_persona", "detail": str(exc)})

    return app


app = create_app()
