"""Optional bridge: include Jarvis Media router into JARVIS_APP main.

Usage in main.py (after app = FastAPI(...)):
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent / "apps" / "jarvis-media"))
    from server import router as media_router
    app.include_router(media_router)
"""
