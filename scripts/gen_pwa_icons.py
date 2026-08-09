"""Generira ATLAS PWA ikone (reproducibilno). Marka: bijeli 'A' kao planinski
vrh + vodoravna 'knjigovodstvena' prečka, na indigo→violet gradijentu, rounded
square. Bez fonta/cairo — čisti Pillow poligoni.

Pokretanje: python scripts/gen_pwa_icons.py
Ispis: atlas/web/static/icons/*.png (favicon.svg se piše ručno, ista geometrija).
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "atlas" / "web" / "static" / "icons"
TOP = (79, 70, 229)     # #4f46e5 indigo
BOT = (124, 58, 237)    # #7c3aed violet
WHITE = (255, 255, 255, 255)

# marka u viewboxu 0..1000 (apex, dvije noge, prečka)
APEX = (500, 205)
LB, RB = (255, 815), (745, 815)
BAR = ((360, 612), (640, 612))
SW_LEG, SW_BAR = 158, 128


def _gradient(S: int) -> Image.Image:
    g = Image.new("RGB", (S, S))
    d = ImageDraw.Draw(g)
    for y in range(S):
        t = y / (S - 1)
        d.line([(0, y), (S, y)], fill=tuple(int(TOP[i] + (BOT[i] - TOP[i]) * t) for i in range(3)))
    return g


def _stroke(d: ImageDraw.ImageDraw, p1, p2, w, f):
    d.line([p1, p2], fill=f, width=w)
    r = w // 2
    for (x, y) in (p1, p2):  # okrugli krajevi/spoj
        d.ellipse([x - r, y - r, x + r, y + r], fill=f)


def _mark(size: int, art_frac: float) -> Image.Image:
    """RGBA sloj samo s bijelom markom (skalirana u centriran okvir)."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    side = size * art_frac
    off = (size - side) / 2
    sc = side / 1000.0
    def P(pt):
        return (off + pt[0] * sc, off + pt[1] * sc)
    _stroke(d, P(APEX), P(LB), max(1, int(SW_LEG * sc)), WHITE)
    _stroke(d, P(APEX), P(RB), max(1, int(SW_LEG * sc)), WHITE)
    _stroke(d, P(BAR[0]), P(BAR[1]), max(1, int(SW_BAR * sc)), WHITE)
    return layer


def make(size: int, maskable: bool = False, opaque: bool = False) -> Image.Image:
    ss = 4
    S = size * ss
    grad = _gradient(S)
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    if maskable or opaque:
        md.rectangle([0, 0, S, S], fill=255)   # puni kvadrat (mask/Apple bez alfe)
    else:
        md.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    base.paste(grad, (0, 0), mask)
    art_frac = 0.60 if maskable else 0.78     # maskable = veća sigurna zona
    base.alpha_composite(_mark(S, art_frac))
    out = base.resize((size, size), Image.LANCZOS)
    if opaque:
        bg = Image.new("RGB", (size, size), TOP)
        bg.paste(out, (0, 0), out)
        return bg
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    make(192).save(OUT / "icon-192.png")
    make(512).save(OUT / "icon-512.png")
    make(512, maskable=True).save(OUT / "icon-maskable-512.png")
    make(180, opaque=True).save(OUT / "apple-touch-icon.png")
    make(32).save(OUT / "favicon-32.png")
    print(f"ikone zapisane u {OUT}")


if __name__ == "__main__":
    main()
