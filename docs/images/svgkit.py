"""A tiny drawing kit for the project's architecture diagrams.

Every diagram is described once, in code, and rendered twice (light and dark) from
that single description, so the two variants can never drift apart. Pure standard
library; the SVGs use only system fonts and no scripts, which is what GitHub allows
in an <img>.
"""
from __future__ import annotations

FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

# Each colour "kind" is a (fill, stroke) pair. The same kind means the same thing in
# every diagram: api = request path, data = stores, compute = workers and agents,
# llm = a single model call, edge = plumbing / deterministic code, ext = third parties,
# ok / bad = outcomes.
THEMES = {
    "light": dict(text="#111827", sub="#4b5563", band="#f8fafc", band_stroke="#e2e8f0", arrow="#475569",
                  api=("#dcfce7", "#16a34a"), data=("#fef3c7", "#d97706"), compute=("#ede9fe", "#7c3aed"),
                  llm=("#dbeafe", "#3b82f6"), edge=("#e5e7eb", "#6b7280"), ext=("#f3f4f6", "#6b7280"),
                  ok=("#dcfce7", "#16a34a"), bad=("#fee2e2", "#dc2626"),
                  label_bg="#ffffff", head="#64748b"),
    "dark": dict(text="#e6edf3", sub="#9aa7b5", band="#161b22", band_stroke="#30363d", arrow="#8b949e",
                 api=("#12361f", "#3fb950"), data=("#3a2b0b", "#d29922"), compute=("#2a2050", "#a371f7"),
                 llm=("#0f2a4d", "#58a6ff"), edge=("#21262d", "#8b949e"), ext=("#21262d", "#8b949e"),
                 ok=("#12361f", "#3fb950"), bad=("#3d1418", "#f85149"),
                 label_bg="#0d1117", head="#8b949e"),
}
PAGE_BG = {"light": "#ffffff", "dark": "#0d1117"}

# Approximate Helvetica/Arial advance widths in 1/1000 em, used only to warn when a
# line of text is likely to overflow its box. Real fonts differ a little by platform,
# so the check keeps a 10% safety margin.
_NARROW = dict.fromkeys("ijl.,:;'|!", 250) | dict.fromkeys("frt()[]{}/ -·", 320)
_WIDE = dict.fromkeys("mMW@", 900) | dict.fromkeys("wOQGDHNRUCAB", 730)


def est_width(s: str, size: float, bold: bool = False) -> float:
    total = 0.0
    for ch in s:
        if ch in _NARROW:
            total += _NARROW[ch]
        elif ch in _WIDE:
            total += _WIDE[ch]
        elif ord(ch) > 0x2000:  # arrows, emoji and other wide glyphs
            total += 900
        else:
            total += 560
    return total / 1000 * size * (1.06 if bold else 1.0)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;")


class Canvas:
    def __init__(self, theme: str, width: int, height: int, aria: str, check: bool = True):
        self.name = theme
        self.t = THEMES[theme]
        self.W, self.H = width, height
        self.aria = aria
        self.check = check
        self.out: list[str] = []
        self.warnings: list[str] = []
        self._markers: dict[str, str] = {}  # extra arrowhead colours -> marker id

    # ------------------------------------------------------------ text
    def text(self, x, y, s, size=15, weight=400, fill=None, anchor="middle", style=""):
        self.out.append(
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill or self.t["text"]}" '
            f'text-anchor="{anchor}" {style}>{_esc(s)}</text>')

    def _fit(self, s, size, weight, avail, where):
        if not self.check:
            return
        if est_width(s, size, weight >= 600) * 1.10 > avail:
            self.warnings.append(f"[{self.name}] '{s}' ({where}) may overflow {avail:.0f}px")

    # ------------------------------------------------------------ shapes
    def card(self, x, y, w, h, kind, title, lines=(), rx=14, title_size=17, line_size=14):
        fill, stroke = self.t[kind]
        self.out.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        self._fit(title, title_size, 700, w - 20, title)
        if lines:
            cy = y + h / 2 - (len(lines) * 9) + 2
            self.text(x + w / 2, cy, title, title_size, 700)
            for i, ln in enumerate(lines):
                self._fit(ln, line_size, 400, w - 20, title)
                self.text(x + w / 2, cy + 22 + i * 19, ln, line_size, 400, self.t["sub"])
        else:
            self.text(x + w / 2, y + h / 2 + 6, title, title_size, 700)

    def band(self, x, y, w, h, rx=20, tint=None):
        """A rounded background lane. `tint` colours it with one of the kinds instead."""
        if tint:
            fill, stroke = self.t[tint]
            self.out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" fill-opacity="0.35" '
                f'stroke="{stroke}" stroke-width="2"/>')
        else:
            self.out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{self.t["band"]}" '
                f'stroke="{self.t["band_stroke"]}" stroke-width="1.5"/>')

    def dot(self, cx, cy, r=9, ring=False):
        """UML start (filled) and end (ringed) markers for state diagrams."""
        c = self.t["arrow"]
        if ring:
            self.out.append(f'<circle cx="{cx}" cy="{cy}" r="{r + 5}" fill="none" stroke="{c}" stroke-width="2"/>')
        self.out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{c}"/>')

    def swatch(self, x, y, kind, size=18):
        fill, stroke = self.t[kind]
        self.out.append(
            f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')

    # ------------------------------------------------------------ arrows
    def _marker(self, color):
        if color is None:
            return "a"
        return self._markers.setdefault(color, f"m{len(self._markers) + 1}")

    def arrow(self, d, dashed=False, both=False, color=None):
        dash = ' stroke-dasharray="7 6"' if dashed else ""
        mid = self._marker(color)
        start = f' marker-start="url(#{mid})"' if both else ""
        self.out.append(
            f'<path d="{d}" fill="none" stroke="{color or self.t["arrow"]}" stroke-width="2.2"{dash}{start} '
            f'marker-end="url(#{mid})"/>')

    def label(self, x, y, s, w=None):
        """A small caption sitting on an arrow, with a background so the line does not strike through it."""
        w = w or (len(s) * 7.6 + 16)
        self.out.append(
            f'<rect x="{x - w / 2}" y="{y - 15}" width="{w}" height="22" rx="6" fill="{self.t["label_bg"]}" opacity="0.92"/>')
        self.text(x, y, s, 13.5, 600, self.t["sub"])

    def note(self, x, y, lines, size=14, gap=21, anchor="start", weight=400, fill=None):
        """Plain multi-line text (no box)."""
        for i, ln in enumerate(lines):
            self.text(x, y + i * gap, ln, size, weight, fill or self.t["sub"], anchor)

    def bullets(self, x, y, items, size=14, gap=19, item_gap=10):
        """A bulleted list; each item is a list of already-wrapped lines."""
        cy = y
        for lines in items:
            self.out.append(f'<circle cx="{x + 3}" cy="{cy - 4.5}" r="2.6" fill="{self.t["head"]}"/>')
            for i, ln in enumerate(lines):
                self.text(x + 14, cy + i * gap, ln, size, 400, self.t["sub"], "start")
            cy += len(lines) * gap + item_gap

    def heading(self, x, y, s, anchor="start"):
        self.text(x, y, s, 13, 700, self.t["head"], anchor, 'letter-spacing="2"')

    # ------------------------------------------------------------ output
    def svg(self) -> str:
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.W} {self.H}" width="{self.W}" '
                f'height="{self.H}" font-family="{FONT}" role="img" aria-label="{_esc(self.aria)}">')
        arrow = self.t["arrow"]
        markers = (f'<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                   f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{arrow}"/></marker>')
        for color, mid in self._markers.items():
            markers += (f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                        f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{color}"/></marker>')
        return "\n".join([head, f"<defs>{markers}</defs>", *self.out, "</svg>"])


def vcurve(x1, y1, x2, y2):
    """A gentle S-curve that leaves and arrives vertically."""
    ym = (y1 + y2) / 2
    return f"M{x1} {y1} C{x1} {ym} {x2} {ym} {x2} {y2}"


def hcurve(x1, y1, x2, y2):
    """A gentle S-curve that leaves and arrives horizontally."""
    xm = (x1 + x2) / 2
    return f"M{x1} {y1} C{xm} {y1} {xm} {y2} {x2} {y2}"
