# Colors for new sources: readable on the dark UI and as far as possible from every color already in use.
# Text contrast is at least 4.5:1 against both dark surfaces (WCAG AA for text, so legend labels in the
# source's color are readable too); distance is CIE76 delta E in Lab.

import colorsys
import math

SURFACES = ("#07090d", "#0c0f15")  # --bg and --panel
# Colors the UI already means something by: research categories, the overlap zone, error, accent, good, the engines.
RESERVED = ("#f472b6", "#cbd5e1", "#a3e635", "#f5b841", "#f87171", "#8b7cff", "#34d399", "#e879f9", "#fb923c")
MIN_CONTRAST = 4.5
GOLDEN_ANGLE = 137.508


def _rgb(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_: str) -> float:
    r, g, b = (_linear(c) for c in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _lab(hex_: str) -> tuple[float, float, float]:
    r, g, b = (_linear(c) for c in _rgb(hex_))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116  # noqa: E731
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: str, b: str) -> float:
    return math.dist(_lab(a), _lab(b))


def readable(hex_: str) -> bool:
    return all(contrast(hex_, s) >= MIN_CONTRAST for s in SURFACES)


def next_color(taken: list[str], candidates: int = 48) -> str:
    # Tries hues around the wheel (golden-angle steps), each at the lowest lightness that is still readable,
    # and keeps the one farthest from every color in use. Deterministic for the same inputs.
    used = [c.lower() for c in (*taken, *RESERVED)]
    best, best_d = "#f4f6fb", -1.0
    for i in range(candidates):
        hue = (i * GOLDEN_ANGLE) % 360 / 360
        for step in range(40):
            light = 0.55 + step * 0.01
            c = _hex(colorsys.hls_to_rgb(hue, light, 0.7))
            if readable(c):
                break
        else:
            continue
        d = min((delta_e(c, u) for u in used), default=100.0)
        if d > best_d:
            best, best_d = c, d
    return best
