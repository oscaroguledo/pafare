# Health check endpoints for system monitoring

from fastapi import APIRouter
from datetime import datetime
from core.utils.response import Response
from core.db import get_db_health


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/")
async def liveness_check():
    """Basic liveness check: 200 if the service is running, for load balancers/orchestrators."""
    return Response.success(data={
        "status": "alive",
        "timestamp": datetime.now().isoformat(),
        "service": "banwee-api"
    })


@router.get("/ready")
async def readiness_check():
    """Readiness check: verifies the DB is reachable, to decide if this instance gets traffic."""
    db_health = await get_db_health()
    is_ready = db_health.get("status") == "healthy"

    payload = {
        "status": "ready" if is_ready else "not_ready",
        "timestamp": datetime.now().isoformat(),
        "service": "banwee-api",
        "database": db_health,
    }

    if not is_ready:
        return Response(
            success=False,
            data=payload,
            message="Service not ready",
            status_code=503
        )

    return Response.success(data=payload)
