"""Community BFF API routers."""

from celine.community.api.alerts import router as alerts_router
from celine.community.api.engagement import router as engagement_router
from celine.community.api.exports import router as exports_router
from celine.community.api.feedback import router as feedback_router
from celine.community.api.flexibility import router as flexibility_router
from celine.community.api.member_emails import router as member_emails_router
from celine.community.api.member_sends import router as member_sends_router
from celine.community.api.members import router as members_router
from celine.community.api.objectives import router as objectives_router
from celine.community.api.operations import router as operations_router
from celine.community.api.overview import router as overview_router
from celine.community.api.user import router as user_router

__all__ = [
    "alerts_router",
    "engagement_router",
    "exports_router",
    "feedback_router",
    "flexibility_router",
    "member_emails_router",
    "member_sends_router",
    "members_router",
    "objectives_router",
    "operations_router",
    "overview_router",
    "user_router",
]
