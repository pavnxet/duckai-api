import asyncio
import json
import logging
import os
import time
import uuid
from typing import List, Optional, Union, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bridge import DuckAIBridge, AVAILABLE_MODELS, MODEL_MAP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DuckAI-Server")

# Configuration
PORT = int(os.environ.get("PORT", 3006))
HOST = os.environ.get("HOST", "0.0.0.0")
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("true", "1", "yes")

app = FastAPI(
    title="DuckAI OpenAI-Compatible API",
    description="Reverse-engineered Duck.ai OpenAI-compatible API proxy server.",
    version="1.0.0"
)

# Enable CORS for third-party web apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bridge = DuckAIBridge(headless=HEADLESS)

@app.on_event("startup")
async def startup_event():
    logger.info("Starting up DuckAI API Proxy...")
    asyncio.create_task(bridge.initialize())

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down DuckAI API Proxy...")
    await bridge.close()

# Pydantic Request Models
class Message(BaseModel):
    role: str
    content: Union[str, List[Any]]

class ChatCompletionRequest(BaseModel):
    model: str = "gpt-6-luna"
    messages: List[Message]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None

# Static UI Route
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>DuckAI API Running. Go to /docs for API schema.</h1>"

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "duckai-api",
        "bridge_ready": bridge._is_ready,
        "models_count": len(AVAILABLE_MODELS)
    }

@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": AVAILABLE_MODELS
    }

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    if not req.messages:
        raise HTTPException(status_code=400, detail="Messages array cannot be empty.")

    # Format messages into conversation prompt
    if len(req.messages) == 1:
        user_content = req.messages[0].content
        prompt = user_content if isinstance(user_content, str) else json.dumps(user_content)
    else:
        # Multi-turn context synthesis
        dialogue = []
        for msg in req.messages:
            content_str = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
            role_prefix = "User" if msg.role == "user" else "Assistant" if msg.role == "assistant" else "System"
            dialogue.append(f"{role_prefix}: {content_str}")
        dialogue.append("Assistant:")
        prompt = "\n".join(dialogue)

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_time = int(time.time())
    target_model = MODEL_MAP.get(req.model.lower(), "gpt-6-luna")

    # Streaming Mode
    if req.stream:
        async def event_generator():
            try:
                # First chunk with role
                first_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": target_model,
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(first_chunk)}\n\n"

                # Stream token deltas
                async for token in bridge.ask_stream(prompt, target_model):
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": target_model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": token},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"

                # Final stop chunk
                stop_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": target_model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop"
                    }]
                }
                yield f"data: {json.dumps(stop_chunk)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"Error in stream generation: {e}")
                err_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": target_model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": f"\n\n[Error: {str(e)}]"},
                        "finish_reason": "error"
                    }]
                }
                yield f"data: {json.dumps(err_chunk)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    # Non-Streaming Mode
    try:
        full_tokens = []
        async for token in bridge.ask_stream(prompt, target_model):
            full_tokens.append(token)
        full_text = "".join(full_tokens)

        prompt_len = max(1, len(prompt) // 4)
        comp_len = max(1, len(full_text) // 4)

        return JSONResponse({
            "id": completion_id,
            "object": "chat.completion",
            "created": created_time,
            "model": target_model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_text
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": prompt_len,
                "completion_tokens": comp_len,
                "total_tokens": prompt_len + comp_len
            }
        })
    except Exception as e:
        logger.error(f"Error in non-streaming completion: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on http://{HOST}:{PORT}")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)
