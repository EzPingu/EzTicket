"""
manager_backend/api/routes_health.py
Endpoint di controllo dello stato di salute del backend API.
"""
from __future__ import annotations

import time
from fastapi import APIRouter
from manager_backend.config import backend_cfg
from manager_backend.models.schemas import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Restituisce lo stato operativo del backend e il timestamp corrente."""
    return HealthResponse(
        status="ok",
        version=backend_cfg.manager_current_version,
        timestamp=int(time.time()),
    )
