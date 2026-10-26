from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from studio_core.api import diagnostics, projects, routes, tools, ws
from studio_core.api.auth import OriginCheckMiddleware
from studio_core.api.errors import ApiError, OrjsonResponse, install_error_handlers


def mount_api(app: FastAPI, allowed_origins: list[str]) -> None:
    install_error_handlers(app)
    app.include_router(routes.public)
    app.include_router(routes.api)
    app.include_router(routes.blob_api)
    app.include_router(tools.router)
    app.include_router(projects.router)
    app.include_router(diagnostics.router)
    app.include_router(ws.router)
    app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["*"],
                       allow_headers=["*"], max_age=600)
    app.add_middleware(OriginCheckMiddleware, allowed_origins=allowed_origins)


__all__ = ["ApiError", "OrjsonResponse", "mount_api"]
