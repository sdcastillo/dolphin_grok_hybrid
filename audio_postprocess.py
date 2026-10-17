"""Layered vocal post-processing: dry signal preserved, effects added on top."""

from __future__ import annotations

import io
import os

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

TTS_EFFECTS_ENABLED = os.getenv("TTS_EFFECTS_ENABLED", "true").lower() in {
    "1", "true", "yes", "on",
}
TTS_REVERB_MIX = float(os.getenv("TTS_REVERB_MIX", "0.24"))
TTS_TREBLE_MIX = float(os.getenv("TTS_TREBLE_MIX", "0.42"))
TTS_BASS_MIX = float(os.getenv("TTS_BASS_MIX", "0.48"))
TTS_TREBLE_GAIN_DB = float(os.getenv("TTS_TREBLE_GAIN_DB", "5.5"))
TTS_BASS_GAIN_DB = float(os.getenv("TTS_BASS_GAIN_DB", "6.0"))


def _to_mono(audio: np.ndarray) -> np.ndarray:
    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    return arr


def _normalize(audio: np.ndarray, peak: float = 0.95) -> np.ndarray:
    max_val = float(np.max(np.abs(audio))) or 1.0
    return (audio / max_val) * peak


def _highpass(audio: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    sos = butter(2, cutoff_hz / (sample_rate / 2), btype="high", output="sos")
    return sosfiltfilt(sos, audio)


def _lowpass(audio: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    sos = butter(2, cutoff_hz / (sample_rate / 2), btype="low", output="sos")
    return sosfiltfilt(sos, audio)


def _reverb_layer(dry: np.ndarray, sample_rate: int) -> np.ndarray:
    """Wet-only reverb tail layered on top of the dry vocal."""
    wet = np.zeros_like(dry)
    taps = (
        (38, 0.34),
        (61, 0.28),
        (89, 0.22),
        (127, 0.17),
        (173, 0.13),
    )
    for ms, gain in taps:
        delay = int(sample_rate * ms / 1000)
        if delay <= 0 or delay >= len(dry):
            continue
        wet[delay:] += dry[:-delay] * gain
        echo = delay * 2
        if echo < len(dry):
            wet[echo:] += dry[:-echo] * gain * 0.45
    return _highpass(wet, sample_rate, 280)


def _treble_layer(dry: np.ndarray, sample_rate: int, gain_db: float) -> np.ndarray:
    """Additive high-end shimmer; dry highs stay, boosted highs are layered."""
    highs = _highpass(dry, sample_rate, 2200)
    boost = 10 ** (gain_db / 20.0)
    return highs * (boost - 1.0)


def _bass_layer(dry: np.ndarray, sample_rate: int, gain_db: float) -> np.ndarray:
    """Additive low-end warmth; dry lows stay, boosted lows are layered."""
    lows = _lowpass(dry, sample_rate, 260)
    boost = 10 ** (gain_db / 20.0)
    return lows * (boost - 1.0)


def apply_vocal_fx(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """
    Keep the synthesized vocal dry and layer reverb / treble / bass on top.
    The source TTS waveform is not replaced — effects are additive.
    """
    dry = _normalize(_to_mono(audio))

    layered = (
        dry
        + _reverb_layer(dry, sample_rate) * TTS_REVERB_MIX
        + _treble_layer(dry, sample_rate, TTS_TREBLE_GAIN_DB) * TTS_TREBLE_MIX
        + _bass_layer(dry, sample_rate, TTS_BASS_GAIN_DB) * TTS_BASS_MIX
    )
    return _normalize(layered)


def encode_audio(
    audio: np.ndarray,
    sample_rate: int,
    output_format: str = "wav",
    *,
    effects: bool = True,
) -> tuple[bytes, str]:
    processed = apply_vocal_fx(audio, sample_rate) if effects and TTS_EFFECTS_ENABLED else _to_mono(audio)

    if output_format.lower() == "mp3":
        from pydub import AudioSegment

        pcm = (np.clip(processed, -1.0, 1.0) * 32767).astype(np.int16)
        segment = AudioSegment(
            pcm.tobytes(),
            frame_rate=sample_rate,
            sample_width=2,
            channels=1,
        )
        buffer = io.BytesIO()
        segment.export(buffer, format="mp3", bitrate="192k")
        buffer.seek(0)
        return buffer.getvalue(), "audio/mpeg"

    # Produce a standard 16-bit PCM WAV for best compatibility with browsers and Audio elements.
    # This ensures a proper 'RIFF' header.
    pcm = (np.clip(processed, -1.0, 1.0) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    sf.write(buffer, pcm, sample_rate, format='WAV', subtype='PCM_16')
    buffer.seek(0)  # ensure position for any readers
    return buffer.getvalue(), "audio/wav"