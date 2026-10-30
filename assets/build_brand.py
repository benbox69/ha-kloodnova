#!/usr/bin/env python3
"""Génère le logo Kloodnova (SVG + PNG) : un nuage portant un K facetté dans l'esprit
de Koolnova (noir + magenta), avec des filets d'air de climatisation dessous.

    .venv/bin/pip install pillow
    .venv/bin/python assets/build_brand.py

Sorties :
  assets/kloodnova-icon.svg, assets/kloodnova-icon-dark.svg   (icône vectorielle)
  assets/preview.png                                          (aperçu clair / sombre)
  custom_components/kloodnova/brand/*.png                     (images chargées par HA)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
BRAND = ROOT / "custom_components" / "kloodnova" / "brand"

# Palette : noir et magenta de Koolnova ; variantes pour fond sombre.
THEMES = {
    "light": {"cloud": "#141414", "k": "#FFFFFF", "accent": "#A61561", "text": "#141414"},
    "dark": {"cloud": "#F2F2F2", "k": "#141414", "accent": "#D9338A", "text": "#F2F2F2"},
}

DY = -12  # recentre verticalement l'ensemble nuage + filets d'air dans le carré 256


# ------------------------------------------------------------------ géométrie

def clip_poly(poly, a, b, c):
    """Garde la partie du polygone où a*x + b*y >= c (Sutherland-Hodgman)."""
    out = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        dp, dq = a * p[0] + b * p[1] - c, a * q[0] + b * q[1] - c
        if dp >= 0:
            out.append(p)
        if (dp >= 0) != (dq >= 0):
            t = dp / (dp - dq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def band(x0, y_top, y_bot, slope, ymin=-1e9, ymax=1e9, xmax=400):
    """Bande oblique entre deux droites parallèles, coupée à gauche (x0) et en y."""
    dx = xmax - x0
    poly = [(x0, y_top), (xmax, y_top + slope * dx), (xmax, y_bot + slope * dx), (x0, y_bot)]
    poly = clip_poly(poly, 0, 1, ymin)  # y >= ymin
    poly = clip_poly(poly, 0, -1, -ymax)  # y <= ymax
    return poly


def streak(x_left, x_right, y, h=11, lean=11):
    """Filet d'air : parallélogramme aux extrémités coupées à 45° (comme le K)."""
    return [(x_left + lean, y), (x_right + lean, y), (x_right, y + h), (x_left, y + h)]


# Nuage : un rectangle arrondi + trois bosses (union de formes pleines).
CLOUD_PILL = (44, 112, 212, 172, 30)  # x0, y0, x1, y1, rayon
CLOUD_BUMPS = [(92, 118, 34), (136, 96, 46), (180, 122, 32)]  # cx, cy, r

# K : fût + bras supérieur (accent) + bras inférieur, séparés par de fins filets.
K_STEM = [(98, 76), (113, 76), (113, 158), (98, 158)]
K_UPPER = band(118, 100, 122, -1.1, ymin=76)
K_LOWER = band(118, 127, 149, 1.0, ymax=158)

STREAKS = [streak(54, 176, 186), streak(82, 206, 204), streak(54, 140, 222)]


def shift(poly):
    return [(x, y + DY) for x, y in poly]


# ------------------------------------------------------------------ rendu PNG

def render_icon(size: int, theme: str, ss: int = 4) -> Image.Image:
    t = THEMES[theme]
    n = size * ss
    k = n / 256
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def pts(poly):
        return [(x * k, (y + DY) * k) for x, y in poly]

    x0, y0, x1, y1, r = CLOUD_PILL
    d.rounded_rectangle([x0 * k, (y0 + DY) * k, x1 * k, (y1 + DY) * k], radius=r * k, fill=t["cloud"])
    for cx, cy, rad in CLOUD_BUMPS:
        d.ellipse([(cx - rad) * k, (cy + DY - rad) * k, (cx + rad) * k, (cy + DY + rad) * k], fill=t["cloud"])
    d.polygon(pts(K_STEM), fill=t["k"])
    d.polygon(pts(K_LOWER), fill=t["k"])
    d.polygon(pts(K_UPPER), fill=t["accent"])
    for s in STREAKS:
        d.polygon(pts(s), fill=t["accent"])
    return img.resize((size, size), Image.LANCZOS)


def find_font(name_has: tuple[str, ...], name_not: tuple[str, ...] = ()) -> tuple[str, int]:
    path = "/System/Library/Fonts/Avenir Next.ttc"
    for i in range(20):
        try:
            f = ImageFont.truetype(path, 20, index=i)
        except OSError:
            break
        full = " ".join(f.getname()).lower()
        if all(w in full for w in name_has) and not any(w in full for w in name_not):
            return path, i
    raise RuntimeError(f"police introuvable : {name_has}")


def render_logo(height: int, theme: str) -> Image.Image:
    """Icône + mot-symbole « KLOODNOVA » + signature, sur fond transparent."""
    t = THEMES[theme]
    s = height / 128
    word, tag = "KLOODNOVA", "Koolnova · Cloud A/C"
    path, idx = find_font(("bold",), ("italic", "condensed", "demi", "heavy"))
    f_word = ImageFont.truetype(path, int(46 * s), index=idx)
    path, idx = find_font(("italic",), ("condensed", "ultra", "heavy"))
    f_tag = ImageFont.truetype(path, int(19 * s), index=idx)

    track = 3.2 * s
    width_word = sum(f_word.getlength(c) + track for c in word) - track
    text_x = int(128 * s + 6 * s)
    width = int(text_x + max(width_word, f_tag.getlength(tag)) + 14 * s)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    img.alpha_composite(render_icon(height, theme), (0, 0))
    d = ImageDraw.Draw(img)
    x = text_x
    baseline = int(70 * s)
    for c in word:
        d.text((x, baseline), c, font=f_word, fill=t["text"], anchor="ls")
        x += f_word.getlength(c) + track
    d.text((text_x, int(100 * s)), tag, font=f_tag, fill=t["accent"], anchor="ls")
    return img


# ------------------------------------------------------------------ rendu SVG

def svg_icon(theme: str) -> str:
    t = THEMES[theme]

    def poly(p, fill):
        return f'<polygon fill="{fill}" points="' + " ".join(f"{x:.1f},{y + DY:.1f}" for x, y in p) + '"/>'

    x0, y0, x1, y1, r = CLOUD_PILL
    parts = [
        f'<rect fill="{t["cloud"]}" x="{x0}" y="{y0 + DY}" width="{x1 - x0}" height="{y1 - y0}" rx="{r}"/>',
        *[f'<circle fill="{t["cloud"]}" cx="{cx}" cy="{cy + DY}" r="{rad}"/>' for cx, cy, rad in CLOUD_BUMPS],
        poly(K_STEM, t["k"]),
        poly(K_LOWER, t["k"]),
        poly(K_UPPER, t["accent"]),
        *[poly(s, t["accent"]) for s in STREAKS],
    ]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">\n'
        "  <title>Kloodnova</title>\n  " + "\n  ".join(parts) + "\n</svg>\n"
    )


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(exist_ok=True)

    for theme in ("light", "dark"):
        prefix = "" if theme == "light" else "dark_"
        render_icon(256, theme).save(BRAND / f"{prefix}icon.png")
        render_icon(512, theme).save(BRAND / f"{prefix}icon@2x.png")
        render_logo(128, theme).save(BRAND / f"{prefix}logo.png")
        render_logo(256, theme).save(BRAND / f"{prefix}logo@2x.png")
        suffix = "" if theme == "light" else "-dark"
        (ASSETS / f"kloodnova-icon{suffix}.svg").write_text(svg_icon(theme), encoding="utf-8")

    # Aperçu : clair à gauche, sombre à droite.
    icon_l, icon_d = render_icon(256, "light"), render_icon(256, "dark")
    logo_l, logo_d = render_logo(128, "light"), render_logo(128, "dark")
    w = max(logo_l.width, 256) + 64
    sheet = Image.new("RGBA", (w * 2, 256 + 128 + 96), "white")
    ImageDraw.Draw(sheet).rectangle([w, 0, w * 2, sheet.height], fill="#1C1C1C")
    for ox, icon, logo in ((0, icon_l, logo_l), (w, icon_d, logo_d)):
        sheet.alpha_composite(icon, (ox + 32, 32))
        sheet.alpha_composite(logo, (ox + 32, 32 + 256 + 32))
    sheet.save(ASSETS / "preview.png")
    print("ok :", ", ".join(sorted(p.name for p in BRAND.glob("*.png"))))


if __name__ == "__main__":
    main()
