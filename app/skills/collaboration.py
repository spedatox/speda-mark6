import json
from app.skills.base import Skill
from app.schemas.coordination import Collaboration, Delegate, Decision


class CollaborationSkill(Skill):
    name = "collaborate"
    read_only = False
    description = ("Talk directly with another persona in this shared conversation: invite them, address a "
        "question to them, or consult privately. Use address for a public peer exchange; the participant "
        "answers you and you automatically receive a turn to respond after their exchanges finish. "
        "Write the actual question to your colleague, not an instruction for them to question the owner; "
        "finish your contribution without a dispatch announcement or an invented reply. "
        "Invite adds a participant without requiring a follow-up; consult keeps their answer private. "
        "Returns a queued-turn receipt, not their answer. Only the lead may hand off leadership or dismiss.")
    input_schema = Collaboration.model_json_schema()

    def available(self, context):
        return context.coordination is not None and context.task_role != "worker"

    async def execute(self, args, context):
        return await context.coordination.collaborate(context, args)


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
        return json.dumps(state)


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
        'It refuses secret environment files, metadata, links, binary content and paths outside the project; returns bounded numbered lines and a content revision.')
    input_schema = {'type':'object', 'properties': {
        'path':{'type':'string', 'maxLength':2000, 'default':''},
        'start_line':{'type':'integer', 'minimum':1, 'maximum':1000000, 'default':1},
        'max_lines':{'type':'integer', 'minimum':1, 'maximum':500, 'default':200},
        'task_id':{'type':'string', 'pattern':'^[a-f0-9]{32}$'}}, 'additionalProperties':False}

    async def execute(self, args, context):
        from app.services.project_workspace import ProjectWorkspace
        from pydantic import BaseModel, Field
        class Read(BaseModel):
            path: str = Field(default='', max_length=2000)
            start_line: int = Field(default=1, ge=1, le=1000000)
            max_lines: int = Field(default=200, ge=1, le=500)
            task_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
        body = Read.model_validate(args)
        return json.dumps(await ProjectWorkspace(context.coordination).read(context, **body.model_dump()))


class ProjectCheckpointSkill(ProjectStatusSkill):
    name, read_only = 'project_checkpoint', False
    description = ('Save a versioned mission objective, acceptance criteria, progress, next steps, blockers and checks in the existing Workshop checkpoint. '
        'Use project_status to read the current Workshop revision, then keep the plan current across engineering or security turns. '
        'Do not overwrite a newer checkpoint, claim unperformed checks or treat this planning record as owner authorization to publish. '
        'Returns the canonical Workshop checkpoint receipt; active or unreconciled work prevents declaring the mission complete.')
    from app.schemas.coordination import Checkpoint
    input_schema = Checkpoint.model_json_schema()

    async def execute(self, args, context):
        from app.schemas.coordination import Checkpoint
        from app.services.coordination_budget import CoordinationError
        await context.coordination.ensure_active(context)
        body = Checkpoint.model_validate(args)
        if body.status == 'complete':
            from sqlalchemy import select
            from app.models.coordination import ProjectTask, WorkerReview
            async with context.coordination.sessions() as db:
                tasks = (await db.execute(select(ProjectTask, WorkerReview).outerjoin(WorkerReview,
                    WorkerReview.task_id == ProjectTask.id).where(ProjectTask.project_id == context.project_id))).all()
            if any(t.status != 'succeeded' or not r or r.verdict != 'accepted'
                    or r.artifact_revision != (t.result or {}).get('revision') for t, r in tasks):
                raise CoordinationError('mission_incomplete', 'Outstanding work or artifact acceptance prevents mission completion')
        result = await context.coordination.projects.checkpoint(context.user_id, context.project_id, body)
        await context.coordination.emit(context.run_id, 'mission_checkpoint', context.agent_id,
            {'project_id': context.project_id, 'status': body.status, 'objective': body.objective,
             'authority': 'Supervisor planning record, not owner authorization'}, context.step_id)
        return json.dumps(result)


class DecisionSkill(Skill):
    name, read_only = "record_decision", False
    description = ("Record a room proposal, evidence event numbers, objections and disposition. "
        "Updates require the current version. Agent recommendations are distinct from owner acceptance. "
        "Preserve unresolved disagreement; this operational record is not a personal memory write.")
    input_schema = Decision.model_json_schema()

    def available(self, context):
        return bool(context.coordination and context.room_id and context.task_role != "worker")

    async def execute(self, args, context):
        return json.dumps(await context.coordination.record_decision(context.user_id, context.room_id,
            Decision.model_validate(args), context=context))


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
