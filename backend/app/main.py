import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError  # noqa: F401  (default 422 shape kept)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import llm
from .config import load_settings
from .db import Database
from .schemas import ChatRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


def create_app() -> FastAPI:
    settings = load_settings()
    state = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        state["db"] = Database(settings.database_url)  # schema auto-created
        state["cache"] = llm.setup_cache(settings)
        if len(settings.api_keys) < 2:
            log.warning("only %d API key(s) configured; failover needs at least 2",
                        len(settings.api_keys))
        yield

    app = FastAPI(title="LLM Chatbot Gateway", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health")
    async def health():
        try:
            await asyncio.to_thread(state["db"].ping)
        except Exception:
            raise HTTPException(503, "database unreachable")
        return {"status": "ok", "keys_configured": len(settings.api_keys),
                "cache_enabled": llm_cache_enabled()}

    def llm_cache_enabled() -> bool:
        import litellm
        return litellm.cache is not None

    @app.post("/chat")
    async def chat(req: ChatRequest, request: Request):
        started = time.perf_counter()
        db: Database = state["db"]

        stored_prompt = await asyncio.to_thread(db.get_system_prompt, req.session_id)
        system_prompt = req.system_prompt or stored_prompt or settings.default_system_prompt
        history = await asyncio.to_thread(
            db.get_history, req.session_id, settings.max_history_messages)
        messages = [{"role": "system", "content": system_prompt},
                    *history,
                    {"role": "user", "content": req.message}]

        simulate = {}
        if settings.enable_failure_simulation:
            simulate = llm.parse_simulation(request.headers.get("X-Simulate-Failure"))

        try:
            result = await llm.complete(settings, messages, simulate)
        except llm.AllProvidersUnavailable:
            raise HTTPException(503, "all providers unavailable")

        latency_ms = int((time.perf_counter() - started) * 1000)
        metadata = {
            "resolution_source": result.resolution_source,
            "model": result.model,
            "latency_ms": latency_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "key_id": result.key_id,
            "fallback_attempts": result.fallback_attempts,
        }
        await asyncio.to_thread(
            db.save_exchange, req.session_id, req.message, result.reply, metadata, req.system_prompt)
        return {"session_id": req.session_id, "reply": result.reply, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: str):
        messages = await asyncio.to_thread(state["db"].get_messages, session_id)
        if messages is None:
            raise HTTPException(404, "session not found")
        return {"session_id": session_id, "messages": messages}

    return app


app = create_app()
