# ==========================================================
#  duckai-api
#  https://github.com/pavnxet/duckai-api
# ==========================================================

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional, Union, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Depends
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
API_KEY = os.environ.get("API_KEY", "").strip()

bridge = DuckAIBridge(headless=HEADLESS)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("DuckAI Server starting up...")
    asyncio.create_task(bridge.initialize())
    yield
    logger.info("DuckAI Server shutting down...")
    await bridge.close()

app = FastAPI(
    title="DuckAI OpenAI-Compatible API",
    description="Hardened reverse-engineered Duck.ai OpenAI-compatible API proxy server.",
    version="1.2.0",
    lifespan=lifespan
)

# Compliant CORS configuration (wildcard origins with credentials=False)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def verify_api_key(request: Request):
    """Enforces constant-time API Key verification via Authorization header only (Fixes H1, N4)."""
    if not API_KEY:
        return
    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Missing 'Bearer <API_KEY>' in Authorization header."
        )
    token = auth_header[7:].strip()
    if not hmac.compare_digest(token, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid API key."
        )

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

def extract_text_content(content: Union[str, List[Any]]) -> str:
    """Safely extracts text strings from multi-part content arrays (Fixes M1)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    parts.append(str(item["text"]))
                elif "content" in item and isinstance(item["content"], str):
                    parts.append(str(item["content"]))
        return "\n".join(parts) if parts else json.dumps(content)
    return str(content)

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
        "auth_enabled": bool(API_KEY),
        "models_count": len(AVAILABLE_MODELS),
        "requests_processed": bridge.request_count,
        "max_requests_before_recycle": bridge.max_requests_before_recycle,
        "memory_recycling": True
    }

@app.get("/v1/models", dependencies=[Depends(verify_api_key)])
async def list_models():
    return {
        "object": "list",
        "data": AVAILABLE_MODELS
    }

@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(req: ChatCompletionRequest, request: Request):
    if not req.messages:
        raise HTTPException(status_code=400, detail="Messages array cannot be empty.")

    # Format messages into conversation prompt with system instruction awareness
    dialogue = []
    for msg in req.messages:
        text = extract_text_content(msg.content).strip()
        if not text:
            continue
        if msg.role == "system":
            dialogue.append(f"[System Instruction: {text}]")
        elif msg.role == "user":
            dialogue.append(f"User: {text}")
        elif msg.role == "assistant":
            dialogue.append(f"Assistant: {text}")
        else:
            dialogue.append(f"{msg.role.capitalize()}: {text}")

    if len(dialogue) == 1 and dialogue[0].startswith("User: "):
        # Single user prompt - strip prefix for cleanest model response
        prompt = dialogue[0][6:]
    else:
        dialogue.append("Assistant:")
        prompt = "\n\n".join(dialogue)

    model_key = req.model.lower()
    if model_key not in MODEL_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{req.model}' is not supported. Available models: {[m['id'] for m in AVAILABLE_MODELS]}"
        )
    target_model = MODEL_MAP[model_key]
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_time = int(time.time())

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

                # Stream token deltas with client disconnect checking (Fixes H6, N2)
                async for token in bridge.ask_stream(prompt, target_model, is_aborted=request.is_disconnected):
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
            except (asyncio.CancelledError, GeneratorExit):
                logger.info(f"Client disconnected; terminated stream {completion_id}.")
                return
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
        async for token in bridge.ask_stream(prompt, target_model, is_aborted=request.is_disconnected):
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
    except TimeoutError as te:
        logger.error(f"Bridge busy/timeout: {te}")
        raise HTTPException(status_code=503, detail="Server busy: Bridge queue timeout.")
    except ValueError as ve:
        logger.warning(f"Invalid model or request: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error in non-streaming completion: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on http://{HOST}:{PORT}")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)
