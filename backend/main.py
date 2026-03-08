from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as redis

from app.config import settings

from app.api.anomalies import router as anomalies_router
from app.api.data import router as data_router
from app.api.jobs import router as jobs_router
from app.api.summaries import router as summary_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the global Redis connection pool for dependency injection
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    app.state.redis = redis_client
    yield
    # Cleanup on shutdown
    await redis_client.aclose()

app = FastAPI(title="EnergyPulse API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(data_router)
app.include_router(anomalies_router)
app.include_router(jobs_router)
app.include_router(summary_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
