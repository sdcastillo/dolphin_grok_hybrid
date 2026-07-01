# dolphin_grok_hybrid

**Dolphin Mistral + Grok Hybrid FastAPI Application**

A powerful hybrid AI server combining the local **Dolphin-Mistral 7B** model (via Ollama) with **Grok/xAI** features. Served as a FastAPI application with OpenAI-compatible endpoints, persistent storage via Supabase, voice synthesis, custom educational web pages, and more.

This is the source for the dolphin_grok_hybrid project hosted at https://github.com/sdcastillo/dolphin_grok_hybrid .

## Core Features

- **Chat System**: Rating-based prompts (G / PG / R / NC17) with math LaTeX support.
- **OpenAI Compatible API**: `/v1/chat/completions` for streaming and non-streaming.
- **Supabase Integration**: Chat logs, TTS audio logs, SAAM entries (STAR method for medical/stress), trending news.
- **TTS & Audio**: Qwen3 TTS with layered effects, storage in Supabase bucket.
- **Image Generation**: Custom procedural Minecraft pixel art generator.
- **Hybrid Grok Features**: Integration with xAI for additional capabilities like video/image gen.
- **Web Hub**: Educational static pages in `static/hub/` and `var-www/` including:
  - Second Amendment (with real chat log examples from G-rated to NC17 uncensored).
  - Trans Athletes in Sports (based on 6-3 SCOTUS ruling, includes chat log ID=352).
- **Additional Endpoints**: /tts, /imagine, /saam, SOAP service, visitor logging, trending.

## Directory Overview

- `main.py`: The main FastAPI app.
- `static/hub/`: Custom built educational HTML pages.
- `var-www/`: HTML pages from /var/www/ (various project sites like integritycare, aloha, etc.).
- `examples/`: Scripts e.g. `openai_chat_history.py` for using local Supabase history with OpenAI API.
- `scripts/`: Start scripts, Tailscale support.
- `schema.sql`: Postgres/Supabase schema.

## Local Development & Git Notes

- Run with `uvicorn main:app` after setting up Ollama and .env.
- The `static/hub` directory received a dedicated commit: "making first commit of directory for all files".
- All API keys (OPENAI, GITHUB_PAT, XAI, SUPABASE, HF etc.) are in .env and **strictly gitignored**.
- No secrets are pushed to this repo.

## License

See the LICENSE file for full terms. Base GPL v3 with "no cloning allowed" restriction and separate licenses for:
- DATE (historical/temporal content)
- MODEL (prompts, logic, hybrid config)
- WEBSITE (all HTML pages and web content)
- Image/Personal IP Brand Assets (photos, generated images, branding)

## Usage

1. Clone the repo.
2. `pip install -r requirements.txt`
3. Set up .env and Ollama.
4. `uvicorn main:app --port 8000`

For the full experience, visit the running app or explore the chat logs in Supabase.

Built with Grok Build tools.