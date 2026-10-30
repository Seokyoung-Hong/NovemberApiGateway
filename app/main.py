"""게이트웨이 앱 조립. 실행: uvicorn app.main:app --port 8000"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.detector.router import router as detector_router, worker_loop
from app.gateway.router import router as gateway_router


@asynccontextmanager
async def lifespan(_):
    init_db()
    task = asyncio.create_task(worker_loop())
    yield
    task.cancel()


app = FastAPI(title="NovemberAiGateway", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(gateway_router)
app.include_router(detector_router)
