"""
manager_backend/app.py
Entry point dell'applicazione FastAPI per il backend di EzTicket Manager.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from manager_backend.api.router import api_v1_router
from manager_backend.auth.session import session_store
from manager_backend.config import backend_cfg

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("ezticket.manager.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gestione del ciclo di vita dell'applicazione."""
    log.info("Avvio EzTicket Manager Backend API (Port: %d)...", backend_cfg.port)
    session_store.purge_expired()
    yield
    log.info("Arresto EzTicket Manager Backend API...")


def create_app() -> FastAPI:
    """Factory per l'inizializzazione dell'app FastAPI."""
    app = FastAPI(
        title="EzTicket Manager API",
        version="0.1.0",
        description="Backend API di gestione e amministrazione per EzTicket",
        lifespan=lifespan,
    )

    # Middleware CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=backend_cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Registrazione router API v1
    app.include_router(api_v1_router)

    # Global Exception Handler per evitare leak di stack trace
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.error("Errore non gestito su %s: %s", request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "InternalServerError",
                "detail": "Si è verificato un errore interno del server.",
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        if str(exc) == "bot_offline":
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "error": "BotOffline",
                    "detail": "Il bot Discord non è attualmente online. Riprova tra poco.",
                },
            )
        log.error("Errore di validazione non gestito su %s: %s", request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "InternalServerError", "detail": "Si è verificato un errore interno del server."},
        )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "manager_backend.app:app",
        host=backend_cfg.host,
        port=backend_cfg.port,
        reload=False,
    )
