"""
manager_backend/api/router.py
Router API v1 di EzTicket Manager.
"""
from __future__ import annotations

from fastapi import APIRouter

from manager_backend.api.routes_auth import router as auth_router
from manager_backend.api.routes_guilds import router as guilds_router
from manager_backend.api.routes_health import router as health_router
from manager_backend.api.routes_tickets import router as tickets_router
from manager_backend.api.routes_history import router as history_router
from manager_backend.api.routes_applications import router as applications_router
from manager_backend.api.routes_dashboard import router as dashboard_router
from manager_backend.api.routes_statistics import router as statistics_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(health_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(guilds_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(statistics_router)
api_v1_router.include_router(tickets_router)
api_v1_router.include_router(history_router)
api_v1_router.include_router(applications_router)