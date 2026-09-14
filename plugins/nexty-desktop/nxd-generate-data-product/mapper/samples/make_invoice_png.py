"""Generate `07-media-direct/invoice.png`. Stdlib only — no Pillow.

WHY THIS IS CHECKED IN. The original fixture image was produced inline and the
generator thrown away, so "regenerate it larger" meant reconstructing a bitmap
font from scratch. A fixture whose inputs cannot be rebuilt is a fixture nobody
can safely change.

WHY THE SIZE MATTERS. The first version was 240x96. The API documents accuracy
problems below roughly 200px on the short edge, which made a live misread
ambiguous: was the harness unable to verify, or was the image simply too small
to read? Those are different findings and the fixture must not conflate them.
This renders at 8x scale (656x272), comfortably above the floor, so a misread
is a genuine misread.

The glyphs are a hand-built 5x7 bitmap font. Only the characters this invoice
needs are defined; `_render` raises on anything else rather than silently
dropping it, because a missing glyph would change what the image says without
changing the file that says it.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

#: 5x7 bitmap glyphs, one string per row, '#' = ink.
_FONT: dict[str, tuple[str, ...]] = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "C": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "#...#", ".###."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    "$": ("..#..", ".####", "#.#..", ".###.", "..#.#", "####.", "..#.."),
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
}

_GLYPH_W = 5
_GLYPH_H = 7
#: 8x, chosen by running it: 5x gave a 166px short edge, still under the
#: documented ~200px floor, and the check at the bottom caught it.
_SCALE = 8
_PAD = 16


def _render(lines: list[tuple[int, int, str]], width: int, height: int) -> bytearray:
    """Paint text into a 1-byte-per-pixel greyscale buffer (255 = white)."""
    buf = bytearray(b"\xff" * (width * height))
    for ox, oy, text in lines:
        for index, char in enumerate(text.upper()):
            glyph = _FONT.get(char)
            if glyph is None:
                raise KeyError(
                    f"no glyph for {char!r}; add it rather than letting the "
                    "image silently say something different from this source"
                )
            for gy, row in enumerate(glyph):
                for gx, cell in enumerate(row):
                    if cell != "#":
                        continue
                    for sy in range(_SCALE):
                        for sx in range(_SCALE):
                            px = ox + (index * (_GLYPH_W + 1) + gx) * _SCALE + sx
                            py = oy + (gy * _SCALE) + sy
                            if 0 <= px < width and 0 <= py < height:
                                buf[py * width + px] = 0
    return buf


def _png(buf: bytearray, width: int, height: int) -> bytes:
    """Minimal greyscale PNG. Filter byte 0 (None) on every scanline."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(buf[y * width : (y + 1) * width])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    text = [
        "ACME INVOICE",
        "REF INV-2044",
        "TOTAL  $90.00",
    ]
    cols = max(len(line) for line in text)
    width = _PAD * 2 + cols * (_GLYPH_W + 1) * _SCALE
    line_h = (_GLYPH_H + 3) * _SCALE
    height = _PAD * 2 + len(text) * line_h

    buf = _render(
        [(_PAD, _PAD + i * line_h, line) for i, line in enumerate(text)],
        width,
        height,
    )
    out = Path(__file__).parent / "07-media-direct" / "invoice.png"
    out.write_bytes(_png(buf, width, height))
    print(f"wrote {out} — {width}x{height}, {out.stat().st_size} bytes")
    if min(width, height) < 200:
        print("  WARNING: short edge under 200px, the documented accuracy floor")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
