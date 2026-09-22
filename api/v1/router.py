from fastapi import APIRouter

from api.v1.reports import router as reports_router

# One aggregator per version: URL-path versioning (/api/v1/...), chosen
# over a header or media-type scheme because it's visible in every log line,
# every curl command, and Swagger itself, with no client-side plumbing
# needed to pin a version. A hypothetical /api/v2 would get its own sibling
# package and its own router here, without touching v1's.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(reports_router)

__all__ = ["api_router"]
