import asyncio
import json
import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent import get_agent_info, run_agent, stream_agent
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from app.logging_config import setup_logging

setup_logging()
logger = logging.getLogger("haa.api")

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"

app = FastAPI(title="Harmony Auto App Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    thinking: str = ""
    model: str


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    ollama_ok = False
    ollama_error = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            ollama_ok = response.status_code == 200
    except Exception as exc:
        ollama_error = str(exc)

    return {
        "status": "ok" if ollama_ok else "degraded",
        "ollama": {
            "reachable": ollama_ok,
            "base_url": OLLAMA_BASE_URL,
            "model": OLLAMA_MODEL,
            "error": ollama_error,
        },
        "agent": get_agent_info(),
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    logger.info("收到 /api/chat 请求, messages=%s", len(request.messages))
    try:
        result = run_agent([message.model_dump() for message in request.messages])
    except Exception as exc:
        logger.exception("Agent 调用失败")
        raise HTTPException(status_code=500, detail=f"Agent 调用失败: {exc}") from exc

    logger.info(
        "完成 /api/chat 请求, reply_len=%s, thinking_len=%s",
        len(result["reply"]),
        len(result["thinking"]),
    )
    return ChatResponse(
        reply=result["reply"],
        thinking=result["thinking"],
        model=OLLAMA_MODEL,
    )


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    logger.info("收到 /api/chat/stream 请求, messages=%s", len(request.messages))

    async def event_generator():
        try:
            for event in stream_agent([message.model_dump() for message in request.messages]):
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                await asyncio.sleep(0)
            yield "data: [DONE]\n\n"
            logger.info("完成 /api/chat/stream 请求")
        except Exception as exc:
            logger.exception("流式 Agent 调用失败")
            payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
