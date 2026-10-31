# Jarvis Media

Minimalist local music + guitar-video player with a small HTTP API for Grok Bot / Dolphin Pod embeds.

## Run

```bash
cd /home/sandcastle/JARVIS_APP/apps/jarvis-media
python3 server.py
```

Opens on port **8010** by default.

- Player: `http://127.0.0.1:8010/`
- Library: `http://127.0.0.1:8010/api/media/library`
- Pick for DC / epic rock: `http://127.0.0.1:8010/api/media/pick?tag=dc`

## Drop files

- Audio: `JARVIS_APP/data/media-library/audio/`
- Video: `JARVIS_APP/data/media-library/video/`
- Or edit `config.json` `library_roots`

Add a Panama cover as `panama.mp3` (or any filename containing panama) and tag `dc` will prefer it once listed in `config.json` pick_defaults.
