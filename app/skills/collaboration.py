import json
from pydantic import ValidationError
from app.profiles.registry import UnknownPersona
from app.skills.base import Skill
from app.schemas.coordination import Collaboration, Delegate, Decision
from app.services.coordination_budget import CoordinationError


class CollaborationSkill(Skill):
    name = "collaborate"
    read_only = False
    description = ("Ask another persona to contribute to this conversation. For a reply the owner can see, "
        "use invite when the colleague is absent and address when they already belong to the room. "
        "Address also gives you a follow-up turn after the colleague finishes. Use consult only for a "
        "deliberately private exchange whose answer you will summarize in your own reply. "
        "Write the actual question to your colleague, not an instruction for them to question the owner; "
        "finish your contribution without a dispatch announcement or an invented reply. "
        "Returns a queued-turn receipt or a denial. Only the lead may hand off leadership or dismiss.")
    input_schema = Collaboration.model_json_schema()

    def available(self, context):
        return context.coordination is not None and context.task_role != "worker"

    async def execute(self, args, context):
        try:
            request = Collaboration.model_validate(args)
        except ValidationError:
            return json.dumps({'status': 'denied', 'code': 'invalid_collaboration',
                'message': 'Include a concrete question or task for the colleague.'})
        try:
            return await context.coordination.collaborate(context, request)
        except UnknownPersona:
            code, message = 'unknown_persona', 'Use an agent ID from the collaborator roster.'
        except CoordinationError as exc:
            if exc.code not in {'not_member', 'private_exchange', 'self_address', 'reply_target',
                                'lead_only', 'lead', 'round_limit', 'no_progress'}:
                raise
            code, message = exc.code, str(exc)
        # Known request rejections have no committed effects. Record the
        # denial so the model can correct the request or finish its reply.
        return json.dumps({'status': 'denied', 'code': code, 'message': message})


class ProjectStatusSkill(Skill):
    name, read_only = "project_status", True
    memoize = False
    description = ('Read this conversation\'s bound project, persistent mission checkpoint, claims and execution results. '
        'Use it to continue engineering or security work across conversational turns and inspect checks before accepting an artifact. '
        'Do not use it for general reasoning that needs no repository execution. '
        'Returns source and artifact revisions, steering receipts and recorded checks without starting workers.')
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def available(self, context):
        return bool(context.project_id and context.coordination and context.coordination.projects)

    async def execute(self, args, context):
        from app.services.mission_control import MissionControl
        state = await context.coordination.projects.inspect(context.user_id, context.project_id)
        state['mission_control'] = await MissionControl(context.coordination).snapshot(context)
        from app.services.project_release import ProjectRelease
        state['release_targets'] = await ProjectRelease(context.coordination.projects).targets()
        project = context.extra.get('project_context') or {}
        if project.get('cloud_environment') and project.get('id'):
            cloud = await context.coordination.projects.cloud.status(context.user_id, context.agent_id, project['id'])
            state['github'] = {'checkpoints': [{key: entry.get(key) for key in ('task_id', 'status', 'url', 'error')}
                for entry in cloud['checkpoints'][:8]]}
        return json.dumps(state)


class StartGitHubProjectSkill(Skill):
    name, read_only = 'start_github_project', False
    requires_network = True
    description = ('Start a durable GitHub project from the current direct owner request. '
        'Use when the owner asks you to build a new prototype or to implement a change in a named GitHub repository. '
        'Choose prototype for a new private repository, or fork when the current owner message names the upstream owner/repository. '
        'Use existing project tools for a follow-up within a project, and answer questions about hypothetical work without starting a project. '
        'Returns a queued project ID. The new project gets its own chat and GitHub links after setup and checkpoint publishing finish.')
    input_schema = {'type': 'object', 'properties': {
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 255},
        'mode': {'enum': ['prototype', 'fork']},
        'repository': {'type': 'string', 'maxLength': 201}},
        'required': ['name', 'mode'], 'additionalProperties': False}

    def available(self, context):
        from app.services.coordination_budget import CoordinationError
        projects = context.coordination.projects if context.coordination else None
        room = context.extra.get('room_state') or {}
        if (context.agent_id != 'optimus' or context.triggered_by != 'user' or context.task_role != 'interactive'
                or context.project_id or not context.run_id or not projects or not projects.enabled
                or [member['agent_id'] for member in room.get('members', [])] != ['optimus']
                or (context.effective_policy and context.effective_policy.snapshot.budget_mode)):
            return False
        try:
            projects.cloud.github.credentials()
        except CoordinationError:
            return False
        return True

    async def execute(self, args, context):
        from app.services.coordination_budget import CoordinationError
        if not self.available(context):
            raise CoordinationError('cloud_owner_request', 'Start GitHub projects from a direct Optimus chat', 403)
        result = await context.coordination.projects.cloud.prepare_from_chat(context, **args)
        return json.dumps(result)


class ProjectDelegateSkill(Skill):
    name, read_only = "project_delegate", False
    description = ("Assign a bounded project task with explicit acceptance criteria to an anonymous "
        "Autobot, Decepticon or reviewer. Uses this room's bound project and shared budget. "
        "Execution starts asynchronously while the conversation continues; admission is not completion. "
        "Use legion_inspect and legion_control for progress and steering, and project_review to assess results. "
        "Dependencies may be this supervisor's existing tasks from any chat in the same folder project.")
    input_schema = Delegate.model_json_schema()

    def available(self, context):
        return bool(context.project_id and context.coordination and context.coordination.projects
                    and context.coordination.projects.enabled and context.task_role != "worker")

    async def execute(self, args, context):
        return json.dumps(await context.coordination.projects.delegate(context, Delegate.model_validate(args)))


class ProjectReadSkill(ProjectStatusSkill):
    name, read_only = 'project_read', True
    description = ('Read files or list the current folder project inventory without starting a coding worker. '
        'Use it to inspect existing code, README and AGENTS.md instructions before planning changes or resuming work. '
        'An optional task_id reads an exact retained completed artifact belonging to this supervisor and project. '
        'Continue a partial read with next_line and next_column as start_line and start_column. '
        'Continue an inventory listing with next_file_offset as file_offset. '
        'It refuses secret environment files, metadata, links, binary content and paths outside the project.')
    input_schema = {'type':'object', 'properties': {
        'path':{'type':'string', 'maxLength':2000, 'default':''},
        'start_line':{'type':'integer', 'minimum':1, 'default':1},
        'start_column':{'type':'integer', 'minimum':1, 'default':1},
        'max_lines':{'type':'integer', 'minimum':1, 'maximum':500, 'default':200},
        'file_offset':{'type':'integer', 'minimum':0, 'default':0},
        'task_id':{'type':'string', 'pattern':'^[a-f0-9]{32}$'}}, 'additionalProperties':False}

    async def execute(self, args, context):
        from app.services.project_workspace import ProjectWorkspace
        from pydantic import BaseModel, Field
        class Read(BaseModel):
            path: str = Field(default='', max_length=2000)
            start_line: int = Field(default=1, ge=1)
            start_column: int = Field(default=1, ge=1)
            max_lines: int = Field(default=200, ge=1, le=500)
            file_offset: int = Field(default=0, ge=0)
            task_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
        body = Read.model_validate(args)
        return json.dumps(await ProjectWorkspace(context.coordination).read(context, **body.model_dump()))


class ProjectCheckpointSkill(ProjectStatusSkill):
    name, read_only = 'project_checkpoint', False
    description = ('Save a versioned mission objective, acceptance criteria, progress, next steps, blockers and checks in the existing Workshop checkpoint. '
        'Use project_status to read the current Workshop revision, then keep the plan current across engineering or security turns. '
        'Do not overwrite a newer checkpoint, claim unperformed checks or treat this planning record as owner authorization to publish. '
        'Use active for ongoing maintenance, even when the incident itself has been resolved. '
        'Returns the saved checkpoint or the specific reasons it could not be saved, so you can correct the request or explain what remains.')
    from app.schemas.coordination import Checkpoint
    input_schema = Checkpoint.model_json_schema()

    async def execute(self, args, context):
        from app.schemas.coordination import Checkpoint
        await context.coordination.ensure_active(context)
        body = Checkpoint.model_validate(args)
        projects = context.coordination.projects
        if body.status == 'complete':
            blockers = await projects.checkpoint_blockers(context.user_id, context.project_id)
            if body.next_steps or body.blockers:
                blockers.insert(0, 'The submitted checkpoint still lists next steps or blockers.')
            if blockers:
                details = '\n'.join(f'- {reason}' for reason in blockers[:12])
                if len(blockers) > 12:
                    details += f'\n- {len(blockers) - 12} more blockers. Inspect project_status.'
                return (
                    'Error: Mission completion blocked. Checkpoint was not saved.\n'
                    f'{details}\nSave verified progress with status active while work remains.'
                )
        try:
            result = await projects.checkpoint(context.user_id, context.project_id, body)
        except CoordinationError as exc:
            if exc.code != 'checkpoint_conflict':
                raise
            return f'Error: {exc}. Checkpoint was not saved. Read project_status before retrying.'
        await context.coordination.emit(context.run_id, 'mission_checkpoint', context.agent_id,
            {'project_id': context.project_id, 'status': body.status, 'objective': body.objective,
             'authority': 'Supervisor planning record, not owner authorization'}, context.step_id)
        return json.dumps(result)


class DecisionSkill(Skill):
    name, read_only = "record_decision", False
    description = ("Record a room proposal, evidence event numbers, objections and disposition. "
        "Use recommended to record your recommendation. The tool saves an agent's accepted input as recommended. "
        "Updates require the current version, and only the owner can update an accepted decision. "
        "Preserve unresolved disagreement. Returns the saved room decision or a denial.")
    input_schema = Decision.model_json_schema()

    def available(self, context):
        return bool(context.coordination and context.room_id and context.task_role != "worker")

    async def execute(self, args, context):
        decision = Decision.model_validate(args)
        if decision.disposition == "accepted":
            decision.disposition = "recommended"
        try:
            result = await context.coordination.record_decision(
                context.user_id, context.room_id, decision, context=context)
        except CoordinationError as exc:
            if exc.code not in {"owner_decision", "decision_version", "decision_evidence"}:
                raise
            return json.dumps({"status": "denied", "code": exc.code, "message": str(exc)})
        return json.dumps(result)


class ProjectReviewSkill(ProjectStatusSkill):
    name, read_only = 'project_review', False
    description = ('Record the assigned supervisor\'s assessment of an exact completed FORGE artifact revision. '
        'Use acceptance criteria, actual file evidence and recorded checks to accept, request changes or block it. '
        'Do not equate a worker\'s success report with verified application behavior or owner permission to deploy. '
        'Returns a versioned assessment; consequential publication remains a separate owner approval.')
    from app.schemas.coordination import ArtifactReview
    input_schema = ArtifactReview.model_json_schema()

    async def execute(self, args, context):
        from app.schemas.coordination import ArtifactReview
        from app.services.mission_control import MissionControl
        return json.dumps(await MissionControl(context.coordination).review(context, ArtifactReview.model_validate(args)))


class ProjectReleaseSkill(ProjectStatusSkill):
    name, read_only = 'project_release', False
    description = ('Prepare publication of a supervisor-accepted artifact using a configured trusted deployment target. '
        'Use only after reviewing the exact revision and its acceptance evidence. '
        'This cannot execute commands, approve its own plan, choose credentials or enable network access for workers. '
        'Returns retained release identity, file changes, deployment and health-check argv, rollback instructions and an owner approval hash.')
    from app.schemas.coordination import PrepareRelease
    input_schema = PrepareRelease.model_json_schema()

    async def execute(self, args, context):
        from app.schemas.coordination import PrepareRelease
        from app.services.project_release import ProjectRelease
        return json.dumps(await ProjectRelease(context.coordination.projects).prepare(context, PrepareRelease.model_validate(args)))
