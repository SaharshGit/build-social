"""GET /health: is the API up, and can it reach the database?"""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.api.deps import get_db
from buildsocial.api.schemas import Health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
async def health(response: Response, db: AsyncSession = Depends(get_db)) -> Health:
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        # 503 = "service unavailable": monitoring tools treat this as down.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="degraded", database="unreachable")
    return Health(status="ok", database="ok")
