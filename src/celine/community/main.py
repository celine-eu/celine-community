"""FastAPI application for the Community Manager BFF."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from celine.community.routes import create_api_router
from celine.community.security.middleware import SecurityHeadersMiddleware
from celine.community.settings import settings

TAGS = [
    {"name": "user", "description": "Authenticated manager profile."},
    {"name": "overview", "description": "REC aggregate performance."},
    {"name": "operations", "description": "Device and data-flow health."},
    {"name": "flexibility", "description": "Flexibility and observed pathway."},
    {"name": "engagement", "description": "Gamification and read-only nudging."},
    {"name": "feedback", "description": "Authenticated manager feedback."},
    {"name": "alerts", "description": "Audited manager alert workflow."},
    {"name": "exports", "description": "Authorized technical table exports."},
]


def stable_operation_id(route: APIRoute) -> str:
    return route.name


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s %(message)s")
    logger = logging.getLogger(__name__)
    if settings.dev_auth_enabled:
        logger.warning(
            "Development authentication is enabled for community %s",
            settings.dev_community_key,
        )
    app = FastAPI(
        title="CELINE Community Manager BFF",
        description="Single-REC backend-for-frontend for the CELINE Manager Dashboard",
        version="1.0.0",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_tags=TAGS,
        generate_unique_id_function=stable_operation_id,
        contact={"name": "CELINE", "url": "https://celine-eu.github.io"},
        license_info={"name": "Apache-2.0"},
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(create_api_router())
    return app


app = create_app()
