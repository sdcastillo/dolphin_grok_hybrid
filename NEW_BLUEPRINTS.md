# dolphin_grok_hybrid

A hybrid AI application combining **Dolphin Mistral** (local LLM via Ollama) with **Grok** capabilities, served via FastAPI.

This project includes a full-stack setup with chat, TTS, image generation, Supabase backend for logs, custom educational web content, and more.

## Features

- **FastAPI Backend**: OpenAI-compatible `/v1/chat/completions` endpoint, streaming support, rating-based prompts (G, PG, R, NC17).
- **Dolphin Mistral Integration**: Powered by local `dolphin-mistral:7b` model with custom system prompts for different censorship levels.
- **Grok/xAI Hybrid**: Integrations for image/video generation and other Grok features.
- **Supabase**: Persistent chat logs, TTS metadata, SAAM (Sexual Awareness Mindfulness) entries, trending data.
- **TTS**: Piper (ONNX) local text-to-speech with effects.
- **Image Gen**: Procedural Minecraft-style pixel art generation (instant CPU).
- **Video Studio**: Vector-space movie hybrid prompt crafter + real xAI Grok video generation endpoint. One-click "🎬 Video-ify" on any AI reply. Ties generated Minecraft images into video prompts. Clips saved + browsable.
- **Website/Hub**: Static educational pages in `static/hub/` covering topics like the Second Amendment and transgender athletes in sports, populated with real chat log examples from the app.
- **Additional**: SOAP endpoint, news trending fetcher, visitor logging, cloak mode for Tailscale.

## Project Structure

- `main.py`: Core FastAPI application.
- `static/hub/`: Educational HTML pages (e.g., second-amendment.html, trans-athletes-sports.html).
- `examples/`: Utility scripts like `openai_chat_history.py` (loads Supabase history for OpenAI calls).
- `scripts/`: Startup scripts (with/without Tailscale).
- `var-www/`: Additional HTML pages copied from /var/www/ (various project sites).
- `schema.sql`: Database schema for Supabase/Postgres.
- `requirements.txt`: Python dependencies.

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Run Ollama with the model:
   ```
   ollama run dolphin-mistral:7b
   ```

3. Set environment variables in `.env` (never commit this):
   - `OLLAMA_BASE_URL`
   - Supabase credentials
   - OpenAI / XAI keys if using hybrid features
   - etc.

4. Run the app:
   ```
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```

See `scripts/start_app.sh` for examples.

## Git History Note

This repo was initialized and files added using Grok Build tools. Some commits target the `static/hub` directory specifically.

## License

See LICENSE file for details. Base is GPL with additional restrictions: no cloning allowed without permission. Separate licenses apply to DATE, MODEL, WEBSITE content, and Image/Personal IP Brand Assets.

## Contributing

Pull requests welcome, but respect the licensing terms.

## Acknowledgments

- Ollama / Dolphin models
- Supabase
- xAI Grok
- Various open source components

For full details on chat logs and pages, explore the source and the running app.
