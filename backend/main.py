import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from gateway import Gateway, ProvidersUnavailable, simulation_plan
from storage import Storage

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    session_id: Annotated[str, StringConstraints(min_length=1, max_length=128, strict=True)]
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000, strict=True)
    ]
    system_prompt: str | None = None


def env_list(name, default=""):
    return tuple(value.strip() for value in os.getenv(name, default).split(",") if value.strip())


def create_app():
    default_prompt = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant.")
    simulate = os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true"

    @asynccontextmanager
    async def lifespan(app):
        keys = env_list("LLM_API_KEYS")
        if len(set(keys)) < 2:
            raise RuntimeError("LLM_API_KEYS requires at least two distinct API keys")
        app.state.gateway = Gateway(
            os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash-lite"),
            keys,
            float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
            os.getenv("LLM_API_BASE") or None,
        )
        app.state.storage = Storage(os.getenv("DATABASE_URL", "sqlite:///app.db"))
        await run_in_threadpool(app.state.storage.initialize)
        app.state.session_locks = tuple(asyncio.Lock() for _ in range(256))
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(env_list("ALLOWED_ORIGINS", "http://localhost:3000")),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
    )

    @app.exception_handler(sqlite3.Error)
    async def database_error(_request, _error):
        logger.error("SQLite operation failed")
        return JSONResponse(status_code=503, content={"detail": "storage unavailable"})

    @app.exception_handler(Exception)
    async def unexpected_error(_request, error):
        logger.error("Unexpected request error (%s)", type(error).__name__)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health")
    async def health():
        await run_in_threadpool(app.state.storage.check)
        return {"status": "ok", "keys_configured": len(app.state.gateway.keys), "cache_enabled": True}

    @app.post("/chat")
    async def chat(
        request: ChatRequest,
        failure_header: Annotated[str | None, Header(alias="X-Simulate-Failure")] = None,
    ):
        start = perf_counter()
        storage = app.state.storage
        session_id = request.session_id
        lock = app.state.session_locks[hash(session_id) % len(app.state.session_locks)]
        async with lock:
            saved_prompt, history = await run_in_threadpool(storage.history, session_id)
            prompt = saved_prompt if saved_prompt is not None else default_prompt
            if request.system_prompt is not None:
                prompt = request.system_prompt

            messages = [
                {"role": "system", "content": prompt},
                *({"role": item["role"], "content": item["content"]} for item in history),
                {"role": "user", "content": request.message},
            ]
            simulated = simulation_plan(failure_header, simulate)
            try:
                answer, key_id, failures = await app.state.gateway.complete(messages, simulated)
            except ProvidersUnavailable:
                raise HTTPException(503, "all providers unavailable") from None

            metadata = {
                "resolution_source": "LIVE_INFERENCE" if key_id else "LLM_CACHE",
                "model": answer.model,
                "latency_ms": int((perf_counter() - start) * 1000),
                "input_tokens": answer.input_tokens,
                "output_tokens": answer.output_tokens,
                "key_id": key_id,
                "fallback_attempts": failures,
            }
            await run_in_threadpool(
                storage.save_turn, session_id, prompt, request.message, answer.reply, metadata
            )

        return {"session_id": session_id, "reply": answer.reply, "metadata": metadata}

    @app.get("/sessions/{session_id:path}/messages")
    async def session_messages(session_id: str):
        prompt, messages = await run_in_threadpool(app.state.storage.history, session_id)
        if prompt is None:
            raise HTTPException(404, "session not found")
        return {"session_id": session_id, "messages": messages}

    return app


app = create_app()
