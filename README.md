# 🦆 DuckAI - OpenAI Compatible API Proxy & GUI

A high-performance reverse-engineered API proxy server and interactive Web GUI for **Duck.ai** (DuckDuckGo AI Chat). Provides an OpenAI-compatible `/v1/chat/completions` API with streaming Server-Sent Events (SSE) support and zero account/API-key requirements.

---

## ✨ Features

- **Standard OpenAI API Compatibility**: Plug directly into Chatbox, Cherry Studio, NextChat, Open WebUI, Cursor, or any standard OpenAI SDK.
- **Real-Time Token Streaming**: Low latency SSE token delivery.
- **Built-in Web Chat GUI**: Beautiful dark-mode ChatGPT-style web UI accessible at `http://localhost:3006`.
- **Anti-Bot Defense Bypass**: Automatically evaluates DuckAI's browser fingerprinting challenges (`x-vqd-hash-1`) with no visual captchas.
- **Multiple Models**:
  - `gpt-6-luna` (Default flagship)
  - `claude-haiku-4.5`
  - `gpt-5.4-mini` (or `gpt-4o-mini`)
  - `mistral-small-4`
  - `gpt-oss-120b`
  - `gemma-4-31b`

---

## 🚀 Quick Start

### 1. Installation

Ensure Python 3.10+ is installed:

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Start the Server

```bash
python server.py
```

The server starts at `http://localhost:3006`.
- **Web Chat GUI**: Visit `http://localhost:3006` in your browser.
- **API Documentation**: Visit `http://localhost:3006/docs`.

---

## 🛠️ Connecting External Clients

### Chatbox / Cherry Studio / NextChat
- **API Host / Base URL**: `http://localhost:3006/v1` (or `http://localhost:3006`)
- **API Key**: `sk-duckai-proxy` (any non-empty string)
- **Model**: `gpt-6-luna`, `claude-haiku-4.5`, or `gpt-5.4-mini`

### Python OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:3006/v1",
    api_key="duckai"
)

response = client.chat.completions.create(
    model="gpt-6-luna",
    messages=[{"role": "user", "content": "Explain quantum computing in one sentence."}],
    stream=True
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

### cURL

```bash
curl -X POST http://localhost:3006/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-6-luna",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `3006` | Server HTTP port |
| `HOST` | `0.0.0.0` | Server bind host |
| `HEADLESS` | `true` | Set to `false` to display the Chrome browser window on screen |

---

## 🛡️ License & Disclaimer

Intended for educational, research, and interoperability purposes.
