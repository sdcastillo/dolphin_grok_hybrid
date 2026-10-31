"""Procedural block picture. Same prompt and seed always draw the same PNG."""

from __future__ import annotations

import hashlib
import struct
import zlib

_PALETTE = (
    (34, 139, 34),
    (46, 92, 46),
    (139, 90, 43),
    (90, 58, 34),
    (70, 130, 180),
    (176, 196, 222),
    (210, 180, 140),
    (105, 105, 105),
    (47, 79, 79),
    (255, 215, 0),
    (178, 34, 34),
    (240, 248, 255),
)


def status() -> dict:
    return {"ready": True, "model": "procedural-minecraft-pixel", "sizes": [256, 384, 512, 768, 1024]}


def _png(width: int, height: int, rgb: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(raw, 9)),
            chunk(b"IEND", b""),
        ]
    )


def generate_image(prompt: str, size: int = 512, seed: int | None = None, variation: int = 0, animate: bool = False) -> bytes:
    del animate  # still a single PNG; the caller stores it as one frame
    side = size if size in (256, 384, 512, 768, 1024) else 512
    blocks = 16
    cell = side // blocks
    digest = hashlib.sha256(f"{prompt}|{seed}|{variation}".encode()).digest()
    pixels = bytearray(side * side * 3)
    for by in range(blocks):
        for bx in range(blocks):
            n = digest[(by * blocks + bx) % len(digest)]
            # Lower rows lean toward ground colors so the picture reads as terrain.
            if by > 11:
                color = _PALETTE[n % 4]
            elif by < 3:
                color = _PALETTE[4 + (n % 2)]
            else:
                color = _PALETTE[n % len(_PALETTE)]
            for y in range(by * cell, min(side, (by + 1) * cell)):
                row = y * side * 3
                for x in range(bx * cell, min(side, (bx + 1) * cell)):
                    i = row + x * 3
                    pixels[i:i + 3] = bytes(color)
    return _png(side, side, bytes(pixels))
