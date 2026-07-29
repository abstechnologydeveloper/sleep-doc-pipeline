"""Generate a dependency-free PNG used by social link previews."""

import struct
import zlib
from functools import lru_cache


WIDTH = 1200
HEIGHT = 630


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


@lru_cache(maxsize=1)
def landing_preview_png() -> bytes:
    """Return a colorful 1200x630 branded story-card composition."""
    rows = []
    for y in range(HEIGHT):
        row = bytearray([0])
        for x in range(WIDTH):
            blend = x / WIDTH
            red = int(238 - 106 * blend)
            green = int(244 - 88 * blend)
            blue = int(255 - 20 * blend)

            # Warm cinematic preview card.
            if 540 < x < 1090 and 80 < y < 550:
                red, green, blue = 31, 35, 67
                glow = max(0, 150 - int(((x - 865) ** 2 + (y - 220) ** 2) ** 0.5))
                red += glow // 3
                green += glow // 5
            # Moon and play control.
            if (x - 900) ** 2 + (y - 190) ** 2 < 62 ** 2:
                red, green, blue = 255, 222, 135
            if (x - 815) ** 2 + (y - 420) ** 2 < 48 ** 2:
                red, green, blue = 111, 140, 255
            if 800 < x < 800 + (y - 390) and 390 < y < 450:
                red, green, blue = 255, 255, 255

            # Left-side brand mark and content lines.
            if 95 < x < 205 and 95 < y < 205:
                red, green, blue = 87, 111, 235
            if 110 < x < 190 and 118 < y < 132:
                red, green, blue = 255, 255, 255
            if 110 < x < 190 and 150 < y < 164:
                red, green, blue = 255, 255, 255
            if 95 < x < 455 and 270 < y < 298:
                red, green, blue = 31, 43, 75
            if 95 < x < 405 and 320 < y < 348:
                red, green, blue = 87, 111, 235
            if 95 < x < 470 and 395 < y < 410:
                red, green, blue = 108, 120, 145
            if 95 < x < 415 and 432 < y < 447:
                red, green, blue = 108, 120, 145

            row.extend((min(red, 255), min(green, 255), min(blue, 255)))
        rows.append(bytes(row))
    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )
