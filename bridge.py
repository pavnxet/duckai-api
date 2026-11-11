# ==========================================================
#  duckai-api
#  https://github.com/pavnxet/duckai-api
# ==========================================================

import asyncio
import json
import logging
import os
import time
import uuid
from typing import AsyncGenerator, Optional, Dict, Any, Callable, Awaitable
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DuckAI-Bridge")

# Model aliases mapping standard OpenAI model names to Duck.ai models
MODEL_MAP = {
    # Default & GPT-6 Luna
    "gpt-6-luna": "gpt-6-luna",
    "gpt-6": "gpt-6-luna",
    "luna": "gpt-6-luna",
    "default": "gpt-6-luna",
    
    # GPT-5.4 mini / 4o mini aliases
    "gpt-5.4-mini": "gpt-5.4-mini",
    "gpt-4o-mini": "gpt-5.4-mini",
    "gpt-4o": "gpt-5.4-mini",
    "gpt-3.5-turbo": "gpt-5.4-mini",
    
    # Claude Haiku (Duck.ai now serves Haiku 5.5; 4.5 kept as a legacy alias)
    "claude-haiku-5.5": "claude-haiku-5.5",
    "claude-haiku-4.5": "claude-haiku-5.5",
    "claude-3-5-haiku": "claude-haiku-5.5",
    "claude-haiku": "claude-haiku-5.5",
    "claude": "claude-haiku-5.5",
    
    # Mistral
    "mistral-small-4": "mistral-small-4",
    "mistral": "mistral-small-4",
    
    # Open Source & Gemma
    "gpt-oss-120b": "gpt-oss-120b",
    "oss-120b": "gpt-oss-120b",
    "gemma-4-31b": "gemma-4-31b",
    "gemma": "gemma-4-31b",
}

MODEL_TESTIDS = {
    "gpt-6-luna": "model-picker-row-gpt-6-luna",
    "gpt-5.4-mini": "model-picker-row-gpt-5.4-mini",
    "claude-haiku-5.5": "model-picker-row-claude-haiku-5-5",
    "mistral-small-4": "model-picker-row-mistral-small-2603",
    "gpt-oss-120b": "model-picker-row-tinfoil/gpt-oss-120b",
    "gemma-4-31b": "model-picker-row-tinfoil/gemma4-31b"
}

# Labels Duck.ai renders on the model-picker button per model. The button text is
# abbreviated (e.g. "6 Luna", "gpt-oss"), so matching it is only used as a fast
# "already selected" shortcut - the authoritative selector is MODEL_TESTIDS.
MODEL_KEYWORDS = {
    "gpt-6-luna": ["Luna", "GPT-6"],
    "gpt-5.4-mini": ["5.4 mini", "GPT-5.4"],
    "claude-haiku-5.5": ["Haiku", "Claude"],
    "mistral-small-4": ["Mistral"],
    "gpt-oss-120b": ["oss 120B", "120B", "oss"],
    "gemma-4-31b": ["Gemma"]
}

MODEL_BUTTON_SELECTORS = [
    "[data-testid='model-picker-button']",
    "button[aria-label*='Model' i]",
]

AVAILABLE_MODELS = [
    {
        "id": "gpt-6-luna",
        "object": "model",
        "owned_by": "duckai",
        "permission": [],
        "description": "GPT-6 Luna - Best for everyday use and general queries."
    },
    {
        "id": "claude-haiku-5.5",
        "object": "model",
        "owned_by": "duckai",
        "permission": [],
        "description": "Claude Haiku 5.5 - Fast, concise reasoning and analysis. (claude-haiku-4.5 alias)"
    },
    {
        "id": "gpt-5.4-mini",
        "object": "model",
        "owned_by": "duckai",
        "permission": [],
        "description": "GPT-5.4 mini - Lightweight and fast."
    },
    {
        "id": "mistral-small-4",
        "object": "model",
        "owned_by": "duckai",
        "permission": [],
        "description": "Mistral Small 4 - Efficient multilingual model."
    },
    {
        "id": "gpt-oss-120b",
        "object": "model",
        "owned_by": "duckai",
        "permission": [],
        "description": "GPT-OSS 120B - Open-weights large model."
    },
    {
        "id": "gemma-4-31b",
        "object": "model",
        "owned_by": "duckai",
        "permission": [],
        "description": "Gemma 4 31B (BETA) - Google open model."
    }
]

class DuckAIBridge:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        
        # Generation-scoped tracking (prevents C1 cross-request token contamination)
        self.active_generation_id: Optional[str] = None
        self.active_queue: Optional[asyncio.Queue] = None
        self._is_ready = False

        # Automated Memory Recycling configuration
        self.request_count = 0
        self.max_requests_before_recycle = int(os.environ.get("RECYCLE_AFTER_REQUESTS", "100"))
        self.last_recycle_time = time.time()
        self.recycle_interval_seconds = int(os.environ.get("RECYCLE_INTERVAL_HOURS", "24")) * 3600

        # Liveness tracking (exposed through /health). A stale page used to keep
        # reporting healthy while every completion failed.
        self.consecutive_failures = 0
        self.last_error: Optional[str] = None
        self.last_error_at: Optional[float] = None
        self.last_success_at: Optional[float] = None

    async def _setup_page(self, page: Page):
        """Attaches python SSE bridge to page, navigates, and verifies readiness."""
        async def on_bridge_message(gen_id: str, raw_json: str):
            if not self.active_generation_id or gen_id != self.active_generation_id:
                return

            if not self.active_queue:
                return

            if raw_json == "[DONE]":
                await self.active_queue.put(None)
                return

            if not raw_json or raw_json.startswith("["):
                return

            try:
                data = json.loads(raw_json)
                action = data.get("action")
                if action == "success" and "message" in data:
                    token = data["message"]
                    await self.active_queue.put(token)
                elif action == "error" or data.get("type", "").startswith("ERR_"):
                    err_type = data.get("type", "ERR_UNKNOWN")
                    err_msg = data.get("message", f"DuckAI Error: {err_type}")
                    logger.error(f"DuckAI returned error payload: {err_type} - {err_msg}")
                    await self.active_queue.put(RuntimeError(f"{err_type}: {err_msg}"))
            except Exception as e:
                logger.debug(f"Frame parsing notice: {e}")

        await page.expose_function("py_duckai_sse", on_bridge_message)

        logger.info("Navigating to https://duck.ai/ ...")
        await page.goto("https://duck.ai/", wait_until="networkidle")
        await page.wait_for_selector("textarea", timeout=20000)

    async def recycle_page(self):
        """Recycles the browser page to flush memory, accumulated DOM trees, and JS heap."""
        logger.info(f"Recycling DuckAI page (handled {self.request_count} requests)...")
        try:
            if self.page and not self.page.is_closed():
                await self.page.close()
            
            self.page = await self.context.new_page()
            await self._setup_page(self.page)
            self.request_count = 0
            self.last_recycle_time = time.time()
            logger.info("DuckAI browser page successfully recycled and ready!")
        except Exception as e:
            logger.error(f"Error during page recycling: {e}")
            self._is_ready = False
            await self.initialize()

    async def initialize(self):
        async with self._init_lock:
            if self._is_ready and self.page and not self.page.is_closed():
                return

            logger.info(f"Initializing DuckAI Browser Bridge (headless={self.headless})...")
            
            # Clean up previous browser instance if re-initializing
            if self.browser:
                try:
                    await self.browser.close()
                except Exception:
                    pass

            if not self.playwright:
                self.playwright = await async_playwright().start()
            
            # Chrome path on Windows fallback to Edge if needed
            chrome_path = "C:/Program Files/Google/Chrome/Application/chrome.exe"
            edge_path = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
            executable_path = chrome_path if os.path.exists(chrome_path) else edge_path

            self.browser = await self.playwright.chromium.launch(
                executable_path=executable_path,
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )

            self.context = await self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )

            # Anti-detection: mask navigator.webdriver
            await self.context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

            # Intercept fetch for /duckchat/v1/chat on context level to stream tokens tagged with generation ID
            await self.context.add_init_script("""
                const origFetch = window.fetch;
                window.fetch = async function(...args) {
                    const res = await origFetch.apply(this, args);
                    const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url ? args[0].url : '');
                    if (url.includes('duckchat/v1/chat')) {
                        const currentGenId = window.__DUCKAI_CURRENT_GEN_ID__ || 'unknown';
                        const cloned = res.clone();
                        (async () => {
                            const reader = cloned.body.getReader();
                            const decoder = new TextDecoder();
                            let buf = '';
                            while (true) {
                                const { done, value } = await reader.read();
                                if (done) break;
                                buf += decoder.decode(value, { stream: true });
                                const lines = buf.split('\\n');
                                buf = lines.pop();
                                for (const line of lines) {
                                    if (line.startsWith('data: ')) {
                                        const payload = line.slice(6).trim();
                                        if (payload.startsWith('[DONE]')) {
                                            window.py_duckai_sse(currentGenId, '[DONE]');
                                        } else if (payload) {
                                            window.py_duckai_sse(currentGenId, payload);
                                        }
                                    }
                                }
                            }
                        })().catch(err => console.error('Fetch stream reader error:', err));
                    }
                    return res;
                };
            """)

            self.page = await self.context.new_page()
            await self._setup_page(self.page)
            self._is_ready = True
            logger.info("DuckAI Browser Bridge ready!")

    async def switch_model(self, target_model: str):
        """Switches the active model in DuckAI's UI using direct testids (Fixes H7)."""
        model_key = MODEL_MAP.get(target_model.lower())
        if not model_key:
            raise ValueError(f"Requested model '{target_model}' is not supported.")

        testid = MODEL_TESTIDS.get(model_key)
        if not testid:
            raise ValueError(f"No testid mapped for model '{model_key}'.")

        try:
            await self._apply_model(model_key, testid)
        except Exception as e:
            # A desynced page (stale DOM, leftover overlay, renamed button label) used to
            # brick the bridge permanently. Recycle once and retry instead of failing forever.
            logger.warning(f"Model switch to {model_key} failed ({e}); recycling page and retrying once...")
            await self.recycle_page()
            await self.new_chat()
            await self._apply_model(model_key, testid)

    async def _find_model_button(self):
        """Locates the model picker button by testid, falling back to a keyword scan."""
        for selector in MODEL_BUTTON_SELECTORS:
            btn = await self.page.query_selector(selector)
            if btn:
                return btn

        for b in await self.page.query_selector_all("button"):
            try:
                txt = (await b.inner_text()).strip()
            except Exception:
                continue
            if any(w in txt for w in ["Luna", "mini", "Haiku", "Mistral", "oss", "Gemma"]):
                return b
        return None

    async def _apply_model(self, model_key: str, testid: str):
        """Opens the model dropdown and clicks the row for model_key."""
        model_btn = await self._find_model_button()
        if not model_btn:
            raise RuntimeError("Model selector button not found in DuckAI UI.")

        target_words = MODEL_KEYWORDS.get(model_key, [])
        current_txt = (await model_btn.inner_text()).strip()
        if any(w.lower() in current_txt.lower() for w in target_words):
            return  # Model is already active

        # Open dropdown
        await model_btn.click()
        await asyncio.sleep(0.4)

        # Click target row
        row = await self.page.query_selector(f"[data-testid='{testid}']")
        if row:
            await row.click()
            await asyncio.sleep(0.6)
            logger.info(f"Switched model to {model_key} (testid: {testid})")
        else:
            await self.page.mouse.click(10, 10)
            raise ValueError(f"Model row '{testid}' was not found in DuckAI dropdown.")

    async def new_chat(self):
        """Resets the conversation and ensures textarea is empty and verified (Fixes H4)."""
        try:
            new_btn = await self.page.query_selector("button:has-text('New Chat'), button[aria-label*='New Chat']")
            if new_btn:
                await new_btn.click()
                await asyncio.sleep(0.5)
            # Ensure textarea is clear
            textarea = await self.page.wait_for_selector("textarea", timeout=5000)
            if textarea:
                await textarea.fill("")
        except Exception as e:
            logger.warning(f"Notice on new_chat reset: {e}")

    def is_ready(self) -> bool:
        """True only when the bridge is initialized and its page is still open."""
        return bool(self._is_ready and self.page is not None and not self.page.is_closed())

    async def probe(self) -> bool:
        """Live check: can the page still run a chat (input + model picker present)?"""
        if not self.is_ready():
            return False
        try:
            async with asyncio.timeout(5.0):
                if await self.page.query_selector("textarea") is None:
                    return False
                for selector in MODEL_BUTTON_SELECTORS:
                    if await self.page.query_selector(selector):
                        return True
                return False
        except Exception as e:
            logger.warning(f"Health probe failed: {e}")
            return False

    def note_success(self):
        """Records a healthy completion (clears degraded state)."""
        self.consecutive_failures = 0
        self.last_error = None
        self.last_error_at = None
        self.last_success_at = time.time()

    def note_failure(self, error: BaseException):
        """Records a bridge failure so /health stops claiming everything is fine."""
        self.consecutive_failures += 1
        self.last_error = str(error)[:200]
        self.last_error_at = time.time()

    async def stop_generation(self):
        """Clicks Stop generating if active and clears current generation (Fixes H6, N2)."""
        try:
            self.active_generation_id = None
            stop_btn = await self.page.query_selector("button[aria-label*='Stop generating'], button:has-text('Stop generating')")
            if stop_btn:
                await stop_btn.click()
                logger.info("Clicked 'Stop generating' button on DuckAI.")
        except Exception as e:
            logger.debug(f"Stop generation notice: {e}")

    async def ask_stream(
        self,
        prompt: str,
        model: str = "gpt-6-luna",
        is_aborted: Optional[Callable[[], Awaitable[bool]]] = None
    ) -> AsyncGenerator[str, None]:
        """Submits prompt to DuckAI and yields token chunks asynchronously with generation safety."""
        # Safe lock acquisition without cancellation leak race (Fixes N1)
        acquired = False
        try:
            try:
                async with asyncio.timeout(30.0):
                    await self.lock.acquire()
                    acquired = True
            except TimeoutError:
                raise TimeoutError("Server busy: Bridge queue timeout waiting for lock.")

            # Automated Memory Recycling check
            self.request_count += 1
            if (self.request_count > self.max_requests_before_recycle or
                time.time() - self.last_recycle_time >= self.recycle_interval_seconds):
                await self.recycle_page()

            if not self._is_ready or not self.page or self.page.is_closed():
                await self.initialize()

            # Start fresh chat first
            await self.new_chat()

            # Switch model AFTER new_chat so selection is preserved
            await self.switch_model(model)

            # Assign fresh unique generation ID (Fixes C1)
            gen_id = str(uuid.uuid4())
            self.active_generation_id = gen_id
            await self.page.evaluate(f"window.__DUCKAI_CURRENT_GEN_ID__ = '{gen_id}';")

            queue = asyncio.Queue()
            self.active_queue = queue

            try:
                # Find textarea and submit
                textarea = await self.page.wait_for_selector("textarea", timeout=10000)
                await textarea.fill(prompt)

                submit_btn = await self.page.query_selector("button[type='submit'], button[aria-label*='Ask'], button[aria-label*='Send']")
                if submit_btn:
                    await submit_btn.click()
                else:
                    await textarea.press("Enter")

                has_received_token = False

                # Stream tokens from queue
                while True:
                    # Check if client disconnected mid-stream (Fixes H6)
                    if is_aborted and await is_aborted():
                        logger.info(f"Client disconnected during generation {gen_id}. Halting stream.")
                        await self.stop_generation()
                        break

                    # Split timeout: 35s for first token (reasoning/search lag), 15s between subsequent tokens (Fixes H3)
                    wait_timeout = 15.0 if has_received_token else 35.0

                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=wait_timeout)
                        if item is None:
                            break  # [DONE] received from DuckAI
                        if isinstance(item, Exception):
                            # Surface DuckAI error frame (Fixes H2)
                            raise item
                        has_received_token = True
                        yield item
                    except asyncio.TimeoutError:
                        logger.warning(f"Generation {gen_id} timed out waiting for token chunk.")
                        break
            except (asyncio.CancelledError, GeneratorExit):
                # Cancellation-aware cleanup on disconnect (Fixes N2)
                logger.info(f"Stream cancelled for generation {gen_id}. Stopping browser...")
                await self.stop_generation()
                raise
            finally:
                self.active_generation_id = None
                self.active_queue = None

            # Reached only when the generation finished without raising.
            self.note_success()
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as e:
            # Queue contention is not a bridge fault; everything else is worth reporting.
            if "Bridge queue timeout" not in str(e):
                self.note_failure(e)
            raise
        finally:
            if acquired:
                self.lock.release()

    async def close(self):
        logger.info("Closing DuckAI bridge...")
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self._is_ready = False
