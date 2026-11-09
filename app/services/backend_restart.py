"""Owner-requested process lifecycle, separate from every agent capability."""
import asyncio
import inspect
from sqlalchemy import select,func
from app.services.config_store import ConfigConflict,ConfigUnavailable
from app.services.coordination_budget import CoordinationError


class BackendRestart:
    def __init__(self,configuration,coordinator,shutdown=None):
        self.configuration,self.coordinator,self.shutdown=configuration,coordinator,shutdown
        self.requested=False

    def status(self):
        return {'available':self.shutdown is not None,'instance_id':self.configuration.instance_id,
            'state':'restarting' if self.requested else 'running',
            'detail':'Restart waits for a clean shutdown under the managed launcher.' if self.shutdown else 'Start with python -m app.supervisor to enable backend restart.'}

    async def request(self,revision,request_id):
        if not self.shutdown: raise CoordinationError('restart_unmanaged','Backend restart requires the managed launcher: python -m app.supervisor',503)
        async with self.coordinator.lifecycle_lock:
            if not self.requested:
                from app.models.coordination import CoordinationRun,ProjectTask
                from app.models.iacon import IaconWork
                from app.services.global_budget import GlobalCall
                async with self.coordinator.sessions() as db:
                    active=await db.scalar(select(func.count()).select_from(CoordinationRun).where(CoordinationRun.status.in_(['queued','running','waiting_tasks','waiting_room','cancel_requested'])))
                    active+=await db.scalar(select(func.count()).select_from(ProjectTask).where(ProjectTask.status.in_(['staging','running','reconciling'])))
                    active+=await db.scalar(select(func.count()).select_from(IaconWork).where(IaconWork.status=='running'))
                    active+=await db.scalar(select(func.count()).select_from(GlobalCall).where(GlobalCall.status=='started'))
                if active: raise CoordinationError('restart_busy','Backend has active work. Finish or stop it before restarting; saved configuration is retained.',409)
            try: entry=await asyncio.to_thread(self.configuration.store.restart,revision,request_id,self.configuration.instance_id)
            except ConfigConflict as exc: raise CoordinationError('config_conflict',str(exc),409) from None
            except ConfigUnavailable as exc: raise CoordinationError('config_unavailable',str(exc),503) from None
            if entry['instance_id']!=self.configuration.instance_id:
                return {'accepted':False,'status':'already_restarted','instance_id':self.configuration.instance_id,'revision':revision}
            self.requested=True
            self.coordinator.restarting=True
            return {'accepted':True,'status':'restarting','instance_id':self.configuration.instance_id,'revision':revision,'request_id':request_id}

    async def signal(self):
        if self.requested and self.shutdown:
            await self.coordinator.notify()
            result=self.shutdown()
            if inspect.isawaitable(result): await result
