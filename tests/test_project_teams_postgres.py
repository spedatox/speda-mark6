"""Run team attribution, assignment fences and control contracts on local PG."""
from tests.test_coordination_postgres import memory
from tests.test_project_teams import (
    test_named_teams_report_real_supervisor_and_separate_worker_role,
    test_team_authority_cannot_be_expanded_by_assignment_prose,
    test_speda_cross_team_admission_preserves_actual_delegator,
    test_assignments_are_binding_fenced_idempotent_and_workers_cannot_delegate,
    test_terminal_task_stop_does_not_fabricate_cancel_request,
    test_queued_task_cancellation_has_scoped_durable_attribution,
    test_project_worker_model_choice_uses_admitted_snapshot,
)
