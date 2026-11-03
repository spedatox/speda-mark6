"""Run the orchestration contract against a disposable local PostgreSQL schema.

Opt in with SPEDA_TEST_POSTGRES_URL; this suite never accepts a remote host.
Each test creates and drops only its own randomly named schema.
"""
import os
from types import SimpleNamespace
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base
from app.models.user import User
from tests.conftest import context
from tests.test_coordination import (
    test_real_separate_personas_challenge_revise_and_reconnect,
    test_direct_chat_single_call_owner_scope_and_alias,
    test_unknown_paid_response_is_held_never_resent,
    test_shared_worker_budget_and_anonymous_prompt,
    test_consult_reserves_return_slot,
    test_parallel_duplicate_input_and_daily_cap,
    test_paid_response_replays_after_delivery_crash_without_another_charge,
    test_consult_returns_to_caller_without_changing_membership,
    test_parallel_model_reservations_never_exceed_root,
    test_cancellation_joins_stream_and_holds_uncertain_call,
    test_queued_owner_input_waits_and_does_not_leak_into_active_round,
    test_stop_reply_at_delivery_boundary_does_not_publish_completed_answer,
    test_cancelled_tool_with_completed_provider_call_requires_reconciliation,
)
from tests.test_projects import (
    test_isolated_execution_shared_budget_checkpoint_and_restart,
    test_uncertain_cell_holds_claim_and_blocks_replacement,
    test_worker_effect_journal_failure_prevents_effect,
    test_actual_forge_loop_uses_adapted_provider_and_receipts,
    test_dependent_review_uses_exact_worker_artifact,
    test_stopping_reply_preserves_delegated_work_and_unknown_charge,
)
from tests.test_client_memory import (
    test_editor_http_cas_revisions_restore_and_owner_scope,
    test_imported_projections_read_only_and_state_edit_uses_lifecycle,
    test_rename_cannot_launder_validity_and_restore_requires_live_evidence,
    test_corrected_import_does_not_leak_obsolete_prepared_text,
)
from tests.test_client_compat import (
    test_warroom_is_speda_led_coordinator_with_separate_history,
    test_original_chat_retry_reconnect_and_history,
    test_legacy_session_adopted_without_rewriting_originals,
)
from tests.test_automation_admission import (
    test_automations_share_coordination_budget_and_never_become_owner_evidence,
    test_paused_automation_intake_survives_restart_and_drains_once,
)
from tests.test_global_budget import (
    test_lifetime_budget_counts_prior_days_and_unknown_outcomes,
    test_lifetime_concurrency_and_pause_block_before_provider_dispatch,
    test_shared_pause_rejects_chat_before_admission_and_keeps_automation_intake,
    test_completed_client_retry_survives_later_budget_pause,
)


@pytest_asyncio.fixture
async def memory():
    dsn = os.getenv("SPEDA_TEST_POSTGRES_URL")
    if not dsn:
        pytest.skip("Disposable local PostgreSQL is not configured")
    url = make_url(dsn)
    if url.host not in {"127.0.0.1", "localhost", "::1"} or url.drivername != "postgresql+asyncpg":
        pytest.fail("PostgreSQL tests require an explicit local asyncpg test URL")
    schema = "speda_test_" + uuid4().hex
    admin = create_async_engine(dsn)
    async with admin.begin() as db:
        await db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        async with sessions() as db, db.begin():
            db.add_all([User(id=1, name="Owner", timezone="Europe/Istanbul"),
                        User(id=2, name="Other owner", timezone="UTC")])
        from app.services.memory_store import MemoryStore
        yield SimpleNamespace(engine=engine, sessions=sessions, context=context, store=MemoryStore(sessions))
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
