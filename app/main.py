"""
Store Intelligence API - FastAPI entrypoint.
Production-aware: structured logging, trace IDs, graceful error handling.
"""

import time, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.database import init_db
from app.routers import events, metrics, funnel, anomalies, health, heatmap
from app.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        logger.info("event=startup db=ready")
    except Exception as e:
        logger.error(f"event=startup_error err={e}")
    yield
    logger.info("event=shutdown")


app = FastAPI(
    title="Store Intelligence API",
    description="Real-time retail analytics from CCTV - Purplle Tech Challenge 2026",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    store_id = request.path_params.get("store_id", "-")
    start = time.monotonic()
    request.state.trace_id = trace_id
    try:
        response = await call_next(request)
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.info(f"trace_id={trace_id} store_id={store_id} "
                    f"method={request.method} path={request.url.path} "
                    f"status={response.status_code} latency_ms={latency_ms}")
        response.headers["X-Trace-Id"] = trace_id
        return response
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.error(f"trace_id={trace_id} path={request.url.path} "
                     f"error={str(exc)} latency_ms={latency_ms}")
        return JSONResponse(status_code=500,
            content={"error": "internal_server_error", "trace_id": trace_id})


app.include_router(events.router,    prefix="/events", tags=["Events"])
app.include_router(metrics.router,   prefix="/stores", tags=["Metrics"])
app.include_router(funnel.router,    prefix="/stores", tags=["Funnel"])
app.include_router(heatmap.router,   prefix="/stores", tags=["Heatmap"])
app.include_router(anomalies.router, prefix="/stores", tags=["Anomalies"])
app.include_router(health.router,    prefix="",        tags=["Health"])


@app.get("/")
async def root():
    return {"service": "Store Intelligence API", "version": "1.0.0",
            "docs": "/docs", "challenge": "Purplle Tech Challenge 2026 Round 2"}
