"""FastAPI application factory and lifecycle."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.routes import router
from backend.app.core.config import get_settings
from backend.app.modules.companies import models as _company_models  # noqa: F401
from backend.app.modules.identity import models as _identity_models  # noqa: F401
from backend.app.modules.catalog import models as _catalog_models  # noqa: F401
from backend.app.modules.inventory import models as _inventory_models  # noqa: F401
from backend.app.modules.purchasing import models as _purchasing_models  # noqa: F401
from backend.app.modules.sales import models as _sales_models  # noqa: F401


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    if settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Company-ID", "Idempotency-Key"],
        )
    application.include_router(router, prefix="/api/v1")
    return application


app = create_app()
