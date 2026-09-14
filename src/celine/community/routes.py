"""Application router assembly."""

from fastapi import APIRouter

from celine.community.api import (
    alerts_router,
    engagement_router,
    exports_router,
    feedback_router,
    flexibility_router,
    member_emails_router,
    member_sends_router,
    members_router,
    objectives_router,
    operations_router,
    overview_router,
    user_router,
)


def create_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(user_router)
    router.include_router(overview_router)
    router.include_router(objectives_router)
    router.include_router(operations_router)
    router.include_router(flexibility_router)
    router.include_router(engagement_router)
    router.include_router(alerts_router)
    router.include_router(members_router)
    router.include_router(member_sends_router)
    router.include_router(member_emails_router)
    router.include_router(exports_router)
    router.include_router(feedback_router)
    return router
