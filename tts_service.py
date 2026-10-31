"""TTS service using Piper (lightweight onnx voices) with optional effects.
Falls back gracefully if model missing. Primary model: en_US-lessac-low.onnx"""

from __future__ import annotations

import asyncio
import os
from typing import NamedTuple
import re
import threading
import time
from pathlib import Path

import numpy as np

from audio_postprocess import TTS_EFFECTS_ENABLED, encode_audio

_CPU_THREADS = int(os.getenv("TTS_NUM_THREADS", str(os.cpu_count() or 4)))
_APP_DIR = Path(__file__).resolve().parent


def _default_voice_paths() -> tuple[str, str]:
    env_model = os.getenv("TTS_MODEL_PATH")
    env_config = os.getenv("TTS_CONFIG_PATH")
    candidates = [
        _APP_DIR / "voices" / "en_US-lessac-low.onnx",
        _APP_DIR / "en_US-amy-low.onnx",
        Path("/root/voices/en_US-lessac-low.onnx"),
    ]
    model = Path(env_model) if env_model else next((p for p in candidates if p.is_file()), candidates[0])
    if env_config:
        config = Path(env_config)
    else:
        sibling = Path(str(model) + ".json")
        config = sibling if sibling.is_file() else Path(str(model) + ".json")
    return str(model), str(config)


DEFAULT_PIPER_MODEL, DEFAULT_PIPER_CONFIG = _default_voice_paths()
# Keep for compatibility, but Piper doesn't use reference in same way
DEFAULT_REF_AUDIO = os.getenv(
    "TTS_REF_AUDIO",
    "/root/models/tts/reference.wav",
)
TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
TTS_WARMUP_ON_STARTUP = os.getenv("TTS_WARMUP_ON_STARTUP", "false").lower() in {
    "1", "true", "yes", "on",
}
TTS_MAX_WORDS = int(os.getenv("TTS_MAX_WORDS", "0"))
TTS_MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "0"))
TTS_DO_SAMPLE = os.getenv("TTS_DO_SAMPLE", "").lower() in {"1", "true", "yes", "on"}
TTS_X_VECTOR_ONLY = os.getenv("TTS_X_VECTOR_ONLY", "").lower() in {
    "1", "true", "yes", "on", "false", "0", "no", "off",
}

_piper_voice = None
_piper_lock = threading.Lock()
_load_error: str | None = None


class SynthesisResult(NamedTuple):
    response_bytes: bytes
    sample_rate: int
    media_type: str
    mp3_bytes: bytes


def _resolve_device() -> str:
    # Piper runs on CPU by default via onnxruntime
    return "cpu"


def _truncate_for_tts(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return text

    if TTS_MAX_CHARS > 0 and len(text) > TTS_MAX_CHARS:
        text = text[:TTS_MAX_CHARS].rsplit(" ", 1)[0].strip()
        if text and text[-1] not in ".!?":
            text += "."

    if TTS_MAX_WORDS > 0:
        words = text.split()
        if len(words) > TTS_MAX_WORDS:
            text = " ".join(words[:TTS_MAX_WORDS])
            if text[-1] not in ".!?":
                text += "."

    return text


def _get_piper_voice():
    global _piper_voice, _load_error
    if _piper_voice is not None:
        return _piper_voice
    if _load_error is not None:
        raise RuntimeError(_load_error)

    with _piper_lock:
        if _piper_voice is not None:
            return _piper_voice
        if _load_error is not None:
            raise RuntimeError(_load_error)

        model_path = Path(DEFAULT_PIPER_MODEL)
        config_path = Path(DEFAULT_PIPER_CONFIG)
        if not model_path.exists():
            _load_error = f"Voice model not found: {model_path}"
            raise RuntimeError(_load_error)
        if not config_path.exists():
            _load_error = f"Voice config not found: {config_path}"
            raise RuntimeError(_load_error)

        try:
            from piper import PiperVoice
            _piper_voice = PiperVoice.load(str(model_path), config_path=str(config_path))
            return _piper_voice
        except Exception as exc:
            _load_error = f"Failed to load Piper voice: {exc}"
            raise RuntimeError(_load_error) from exc


# No voice prompt needed for Piper (uses built-in speaker)


def _synthesize_sync(
    text: str,
    language: str = "Auto",
    *,
    output_format: str = "wav",
    effects: bool = True,
) -> SynthesisResult:
    text = _truncate_for_tts(text.strip())
    if not text:
        raise ValueError("Text is required for TTS")

    voice = _get_piper_voice()
    # Collect audio chunks from Piper (yields AudioChunk per sentence)
    audio_chunks = []
    sample_rate = getattr(getattr(voice, 'config', None), 'sample_rate', 16000)
    for chunk in voice.synthesize(text):
        # Prefer numpy array if available, else bytes
        if hasattr(chunk, 'audio_int16_array') and chunk.audio_int16_array is not None:
            arr = np.asarray(chunk.audio_int16_array, dtype=np.float32) / 32768.0
            audio_chunks.append(arr)
        elif hasattr(chunk, 'audio_int16_bytes') and chunk.audio_int16_bytes:
            arr = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            audio_chunks.append(arr)
        elif hasattr(chunk, 'audio_float_array'):
            audio_chunks.append(np.asarray(chunk.audio_float_array, dtype=np.float32))

    if not audio_chunks:
        raise ValueError("No audio generated from Piper")

    audio = np.concatenate(audio_chunks) if len(audio_chunks) > 1 else audio_chunks[0]

    response_bytes, media_type = encode_audio(
        audio,
        sample_rate,
        output_format=output_format,
        effects=effects,
    )
    mp3_bytes, _ = encode_audio(audio, sample_rate, output_format="mp3", effects=effects)
    return SynthesisResult(response_bytes, sample_rate, media_type, mp3_bytes)


async def synthesize(
    text: str,
    language: str = "Auto",
    *,
    output_format: str = "wav",
    effects: bool = True,
) -> SynthesisResult:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _synthesize_sync(text, language, output_format=output_format, effects=effects),
    )


def status() -> dict:
    model_path = Path(DEFAULT_PIPER_MODEL)
    config_path = Path(DEFAULT_PIPER_CONFIG)
    device = _resolve_device()

    info = {
        "enabled": TTS_ENABLED,
        "model_path": str(model_path),
        "model_exists": model_path.exists(),
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "device": device,
        "cpu_threads": _CPU_THREADS,
        "effects_enabled": TTS_EFFECTS_ENABLED,
        "loaded": _piper_voice is not None,
        "ready": False,
        "error": _load_error,
        "performance_hint": "Piper TTS (fast local CPU synthesis)",
    }

    if not TTS_ENABLED:
        info["error"] = info["error"] or "TTS disabled via TTS_ENABLED"
        return info

    if not model_path.exists() or not config_path.exists():
        return info

    info["ready"] = _piper_voice is not None
    return info


def warmup() -> None:
    if not TTS_ENABLED or not TTS_WARMUP_ON_STARTUP:
        return
    try:
        _get_piper_voice()
    except Exception:
        pass