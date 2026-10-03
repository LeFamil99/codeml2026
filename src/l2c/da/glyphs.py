"""Glyph decoder for outlined DA pages (tier 2) - PLAN SS5.2-5.8, DA_PLAN phase 2.

On 340 DA pages every character is its own filled vector path (even-odd fill) and
there is no font and no text layer. Segmentation is therefore free and exact: each
path IS one glyph, with a sub-point bounding box. Reading it is classification:

1. rasterise each glyph path ourselves (Bezier-flattened, even-odd), normalise to a
   20x20 ink box keeping the aspect ratio;
2. templates: the base-14 fonts (``helv hebo cour cobo tiro``), sent through the SAME
   text-to-path round trip and the SAME rasteriser, so a template and a page glyph
   are produced by identical code;
3. cosine similarity, keeping the top-k candidates per glyph (the lexicon stage in
   ``decode.py`` needs the runners-up, PLAN SS5.8).

Nothing here sees the plan.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import numpy as np
import pymupdf
from PIL import Image, ImageChops, ImageDraw

N = 20                 # normalised bitmap side (PLAN SS5.2: 16-20 px measured)
HI = 64                # rasterisation resolution of the longer glyph side
FONTS = ("helv", "hebo", "heit", "hebi", "cour", "cobo", "coit", "cobi", "tiro", "tibo", "tiit", "tibi")
# CAD text carries a width factor; CLP's fabricator draws Times at ~0.36 (its "M" has
# aspect 0.46 vs 1.29 normal). Stretch-normalising such a glyph thickens its vertical
# stems ~3x, so templates are generated AT these widths and normalised the same way.
WIDTHS = (0.4, 0.6, 0.8, 1.0, 1.25)
CHARS = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
         ".,:;-=\"'@/()#&*+%<>ÉÈÀ°x")


@dataclass
class Glyph:
    x0: float
    y0: float
    x1: float
    y1: float
    vec: np.ndarray            # N*N float32, L2-normalised
    items: list | None = None  # the path items, kept so merged parts can be re-rasterised

    @property
    def rect(self) -> pymupdf.Rect:
        return pymupdf.Rect(self.x0, self.y0, self.x1, self.y1)

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def aspect(self) -> float:
        return self.w / max(self.h, 1e-6)


# ----------------------------------------------------------------- rasterising
def _subpaths(items) -> list[list[tuple[float, float]]]:
    """Flatten a path's items into closed polygons, split where the pen jumps."""
    polys: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []

    def start(p):
        nonlocal cur
        if not cur or abs(cur[-1][0] - p.x) > 1e-3 or abs(cur[-1][1] - p.y) > 1e-3:
            if len(cur) >= 3:
                polys.append(cur)
            cur = [(p.x, p.y)]

    for it in items:
        op = it[0]
        if op == "l":
            start(it[1])
            cur.append((it[2].x, it[2].y))
        elif op == "c":
            p0, c1, c2, p3 = it[1], it[2], it[3], it[4]
            start(p0)
            for t in np.linspace(0.125, 1.0, 8):
                u = 1 - t
                cur.append((u**3 * p0.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t**3 * p3.x,
                            u**3 * p0.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t**3 * p3.y))
        elif op == "re":
            r = it[1]
            if len(cur) >= 3:
                polys.append(cur)
            polys.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
            cur = []
        elif op == "qu":
            q = it[1]
            if len(cur) >= 3:
                polys.append(cur)
            polys.append([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)])
            cur = []
    if len(cur) >= 3:
        polys.append(cur)
    return polys


def _normalise(img: Image.Image) -> np.ndarray | None:
    """Ink box stretched to fill the square: CAD text carries width factors (condensed /
    expanded), so shape is compared independently of proportion; proportion is scored
    separately in ``classify``. Very thin marks (``1 l I | :``) keep their proportion,
    stretching them to a square would erase what identifies them."""
    box = img.getbbox()
    if box is None:
        return None
    crop = img.crop(box)
    w, h = crop.size
    if w < 0.3 * h or h < 0.3 * w:
        s = (N - 2) / max(w, h)
        nw, nh = max(1, round(w * s)), max(1, round(h * s))
    else:
        nw = nh = N - 2
    small = crop.resize((nw, nh), Image.BOX)
    canvas = Image.new("L", (N, N), 0)
    canvas.paste(small, ((N - nw) // 2, (N - nh) // 2))
    v = np.asarray(canvas, dtype=np.float32).ravel()
    n = np.linalg.norm(v)
    return v / n if n else None


def _scaled(items, sx: float, x0: float):
    """Items with x scaled by ``sx`` about ``x0`` (a CAD width factor)."""
    P = lambda p: pymupdf.Point(x0 + (p.x - x0) * sx, p.y)
    out = []
    for it in items:
        if it[0] in ("l", "c"):
            out.append((it[0], *[P(p) for p in it[1:]]))
        elif it[0] == "re":
            r = it[1]
            out.append(("re", pymupdf.Rect(P(r.tl), P(r.br))))
        elif it[0] == "qu":
            q = it[1]
            out.append(("qu", pymupdf.Quad(P(q.ul), P(q.ur), P(q.ll), P(q.lr))))
    return out


def rasterise(items, rect: pymupdf.Rect) -> np.ndarray | None:
    w, h = rect.width, rect.height
    if w <= 0 or h <= 0:
        return None
    s = HI / max(w, h)
    W, H = max(2, int(np.ceil(w * s)) + 2), max(2, int(np.ceil(h * s)) + 2)
    acc = Image.new("L", (W, H), 0)
    for poly in _subpaths(items):
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).polygon([((x - rect.x0) * s + 1, (y - rect.y0) * s + 1) for x, y in poly],
                                  fill=255)
        acc = ImageChops.logical_xor(acc.convert("1"), m.convert("1")).convert("L")   # even-odd
    return _normalise(acc)


# ----------------------------------------------------------------- page glyphs
def _is_ink(fill) -> bool:
    return fill is not None and min(fill) < 0.85          # not a white masking box


def page_glyphs(page: pymupdf.Page) -> list[Glyph]:
    """Every filled glyph-sized path on the page, double-emitted outlines removed."""
    out: list[Glyph] = []
    seen: set[tuple] = set()
    for d in page.get_drawings():
        r = d["rect"]
        if not _is_ink(d.get("fill")) or not (0.3 <= r.height <= 60 and 0.3 <= r.width <= 60):
            continue
        key = (round(r.x0 * 2), round(r.y0 * 2), round(r.x1 * 2), round(r.y1 * 2))
        if key in seen:                                   # PLAN SS5.15: outlines emitted twice
            continue
        seen.add(key)
        v = rasterise(d["items"], r)
        if v is not None:
            out.append(Glyph(r.x0, r.y0, r.x1, r.y1, v, d["items"]))
    return out


def merge(parts: list[Glyph]) -> Glyph:
    """One character drawn as several paths (``:``, ``É``, ``i``, a split ``R``)."""
    r = parts[0].rect
    for g in parts[1:]:
        r |= g.rect
    items = [it for g in parts for it in (g.items or [])]
    v = rasterise(items, r)
    return Glyph(r.x0, r.y0, r.x1, r.y1, v if v is not None else parts[0].vec, items)


# ----------------------------------------------------------------- templates
@dataclass(frozen=True)
class Bank:
    chars: tuple[str, ...]          # one entry per template
    fonts: tuple[str, ...]
    mat: np.ndarray                 # (n_templates, N*N)
    aspect: np.ndarray              # ink aspect w/h per template
    height: np.ndarray              # ink height relative to the font's cap height
    top: np.ndarray                 # ink top relative to cap top (0 = cap top, 1 = baseline)


@functools.lru_cache(maxsize=1)
def bank() -> Bank:
    """Base-14 templates through the same text->path round trip as the test pages."""
    chars, fonts, vecs, asp, hts, tops = [], [], [], [], [], []
    size, pitch = 60.0, 90.0
    for font in FONTS:
        doc = pymupdf.open()
        pg = doc.new_page(width=pitch * 12, height=pitch * 10)
        spots = []
        for i, ch in enumerate(CHARS + "H"):
            x, y = 20 + (i % 12) * pitch, 70 + (i // 12) * pitch
            pg.insert_text((x, y), ch, fontsize=size, fontname=font)
            spots.append((ch, x, y))
        svg = pg.get_svg_image(text_as_path=True)
        pdf = pymupdf.open("pdf", pymupdf.open("svg", svg.encode()).convert_to_pdf())[0]
        draws = [d for d in pdf.get_drawings() if d.get("fill") is not None]

        def ink_at(x, y):
            return [d for d in draws if x - 5 <= d["rect"].x0 and d["rect"].x1 <= x + pitch - 10
                    and y - size - 10 <= d["rect"].y0 and d["rect"].y1 <= y + 25]

        # cap height of this font: the "H" drawn last
        hx, hy = spots[-1][1], spots[-1][2]
        H = ink_at(hx, hy)
        cap_top = min(d["rect"].y0 for d in H)
        cap = hy - cap_top
        for ch, x, y in spots[:-1]:
            ds = ink_at(x, y)
            if not ds:
                continue
            r = pymupdf.Rect(ds[0]["rect"])
            for d in ds[1:]:
                r |= d["rect"]
            items = [it for d in ds for it in d["items"]]
            for wf in WIDTHS:
                rr = pymupdf.Rect(r.x0, r.y0, r.x0 + r.width * wf, r.y1)
                v = rasterise(_scaled(items, wf, r.x0), rr)
                if v is None:
                    continue
                chars.append(ch)
                fonts.append(f"{font}@{wf}")
                vecs.append(v)
                asp.append(rr.width / max(rr.height, 1e-6))
                hts.append(r.height / cap)
                tops.append((r.y0 - (y - cap)) / cap)     # 0 = cap line, 1 = baseline
    return Bank(tuple(chars), tuple(fonts), np.vstack(vecs), np.array(asp),
                np.array(hts), np.array(tops))


def scores(glyphs: list[Glyph]) -> np.ndarray:
    """(n_glyphs, n_templates) shape score: cosine minus the aspect penalty."""
    b = bank()
    G = np.vstack([g.vec for g in glyphs])
    sim = G @ b.mat.T
    ga = np.log(np.array([max(g.aspect, 1e-3) for g in glyphs]))[:, None]
    return sim - 0.25 * np.abs(ga - np.log(np.maximum(b.aspect, 1e-3))[None, :])


def classify(glyphs: list[Glyph], k: int = 5):
    """Top-k (chars, scores) per glyph: cosine similarity minus an aspect-ratio penalty
    against each template AT its width factor (proportion is what separates ``1 l I -``,
    and with width-factored templates a condensed font no longer pays for it)."""
    b = bank()
    if not glyphs:
        return [], []
    G = np.vstack([g.vec for g in glyphs])
    sim = G @ b.mat.T
    ga = np.log(np.array([max(g.aspect, 1e-3) for g in glyphs]))[:, None]
    sim = sim - 0.25 * np.abs(ga - np.log(np.maximum(b.aspect, 1e-3))[None, :])
    out_c, out_s = [], []
    for row in sim:
        order = np.argsort(-row)
        cs, ss, seen = [], [], set()
        for j in order:
            c = b.chars[j]
            if c in seen:
                continue
            seen.add(c)
            cs.append(c)
            ss.append(float(row[j]))
            if len(cs) == k:
                break
        out_c.append(cs)
        out_s.append(ss)
    return out_c, out_s
