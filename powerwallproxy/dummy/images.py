"""Procedural PNGs (stdlib only) standing in for screenshots, charts and photos."""

import math
import random
import struct
import zlib


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def plasma(width: int, height: int, rng: random.Random) -> bytes:
    a, b, c = (rng.uniform(0.005, 0.03) for _ in range(3))
    phase = rng.uniform(0, math.tau)
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            v = math.sin(x * a + phase) + math.sin(y * b) + math.sin((x + y) * c)
            row += bytes((
                int(127 + 127 * math.sin(v * math.pi)),
                int(127 + 127 * math.sin(v * math.pi + 2.1)),
                int(127 + 127 * math.sin(v * math.pi + 4.2)),
            ))
        rows.append(bytes(row))
    return _png(width, height, rows)


def bar_chart(width: int, height: int, rng: random.Random) -> bytes:
    bars = rng.randint(5, 12)
    values = [rng.uniform(0.15, 0.95) for _ in range(bars)]
    hue = rng.choice([(66, 135, 245), (240, 120, 60), (80, 200, 120), (200, 90, 220)])
    bg, grid = (24, 26, 32), (48, 52, 62)
    margin = width // 12
    bar_w = (width - 2 * margin) / bars
    rows = []
    for y in range(height):
        row = bytearray()
        level = 1 - (y - margin) / (height - 2 * margin)
        for x in range(width):
            px = bg
            if margin <= y < height - margin and (y - margin) % (height // 8) == 0:
                px = grid
            i = int((x - margin) // bar_w) if x >= margin else -1
            if 0 <= i < bars and (x - margin) % bar_w > bar_w * 0.15 and 0 <= level <= values[i] and y < height - margin:
                shade = 0.6 + 0.4 * level
                px = tuple(int(ch * shade) for ch in hue)
            row += bytes(px)
        rows.append(bytes(row))
    return _png(width, height, rows)


def fake_screenshot(width: int, height: int, rng: random.Random) -> bytes:
    """Window chrome + text-ish lines, like a browser/terminal screenshot."""
    accent = rng.choice([(90, 160, 255), (255, 170, 60), (120, 220, 140)])
    lines = [(rng.randint(0, 3) * 24, rng.randint(80, width - 60)) for _ in range(height // 18)]
    rows = []
    for y in range(height):
        row = bytearray()
        line_i, in_line = divmod(y - 48, 18)
        for x in range(width):
            if y < 32:
                px = (52, 55, 64) if not (12 <= y < 20 and x % 22 < 8 and x < 70) else (200, 90, 90)
            elif y < 36:
                px = accent
            else:
                px = (250, 250, 248)
                if y >= 48 and 0 <= line_i < len(lines) and 3 <= in_line < 11:
                    indent, length = lines[line_i]
                    if 20 + indent <= x < 20 + indent + length and (x // 7) % 9 != 0:
                        px = (70, 74, 84) if line_i % 5 else accent
            row += bytes(px)
        rows.append(bytes(row))
    return _png(width, height, rows)


GENERATORS = {"plasma": plasma, "chart": bar_chart, "screenshot": fake_screenshot}


def generate_pool(seed: int, per_kind: int = 3) -> dict[str, list[bytes]]:
    rng = random.Random(seed)
    sizes = [(480, 300), (400, 400), (640, 360)]
    return {
        kind: [gen(*sizes[i % len(sizes)], rng) for i in range(per_kind)]
        for kind, gen in GENERATORS.items()
    }
