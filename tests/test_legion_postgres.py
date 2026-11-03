"""Native child-work contracts use random disposable local PostgreSQL schemas."""
from tests.test_coordination_postgres import memory
from tests.test_legion_integration import (
    test_foreground_worker_progress_results_and_history_are_separate_from_parent,
    test_detached_worker_outlives_reply_reconnects_and_keeps_original_budget,
    test_native_cwd_binding_is_stable_and_forge_task_stream_is_attributed,
    test_supervisor_collection_is_room_scoped_and_steering_is_not_owner_authority,
)
from tests.test_coordination_worker_queue import (
    test_known_child_slot_waits_before_admission_without_another_send,
    test_unknown_capacity_is_held_without_waiting_or_provider_retry,
)
from tests.test_worker_control import (
    test_worker_owner_isolation_stable_tickets_and_duplicate_admission,
    test_worker_event_cursor_replay_and_unknown_executor_are_honest,
    test_inbox_misbound_context_cannot_claim_or_ack_another_worker_input,
)
