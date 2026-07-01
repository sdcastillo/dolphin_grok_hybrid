# dolphin_grok_hybrid

**Dolphin Mistral FastAPI hybrid with Grok capabilities.**

A FastAPI application featuring:

- Local **Dolphin-Mistral 7B** via Ollama
- OpenAI-compatible `/v1/chat/completions` endpoint
- **Grok** (xAI) integration for hybrid features
- Supabase for chat logs, SAAM, TTS metadata
- Custom educational web pages (in `static/hub/`):
  - Second Amendment (with G/R/NC17 chat log examples from Supabase)
  - Transgender Athletes in Sports (SCOTUS ruling + chat log ID 352)
- TTS with Qwen3
- Image gen, trending news, SOAP endpoint, etc.

## Setup

```bash
pip install -r requirements.txt
# Start Ollama with dolphin-mistral:7b
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Key Files
- `main.py` - FastAPI app
- `examples/openai_chat_history.py` - Load Supabase history + OpenAI
- `static/hub/second-amendment.html`
- `static/hub/trans-athletes-sports.html`
- `schema.sql` - DB schema

See local development for full source (some files use MCP GitHub tools for remote updates due to PAT scoping).

## GitHub Note
This repo was created and seeded using Grok + GitHub MCP integration.