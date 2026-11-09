"""One graceful ASGI child. Explicit restart exits 75 for its managed parent."""
import asyncio
import os
import uvicorn


class ManagedServer(uvicorn.Server):
    async def shutdown(self, sockets=None):
        coordinator = getattr(self.config.app.state, "coordinator", None)
        if coordinator is not None:
            coordinator.restarting = True
            await coordinator.notify()
        await super().shutdown(sockets=sockets)


async def serve():
    from app.config import settings
    from app.main import create_app
    app=create_app()
    server=ManagedServer(uvicorn.Config(app,host=settings.http_host,port=settings.http_port,
        log_level=settings.log_level.lower(),timeout_graceful_shutdown=30))
    if os.environ.get('SPEDA_MANAGED_PARENT')=='1':
        app.state.request_backend_shutdown=lambda:setattr(server,'should_exit',True)
    await server.serve()
    restart=getattr(app.state,'backend_restart',None)
    return 75 if restart and restart.requested else 0


if __name__=='__main__': raise SystemExit(asyncio.run(serve()))
