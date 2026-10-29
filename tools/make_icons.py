"""Draws icon.png / logo.png for the Home Assistant app (original artwork, no JBL marks).

    python tools/make_icons.py <output dir>     (needs Pillow; uses Windows Segoe UI fonts)
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(sys.argv[1])
SS = 4  # supersampling


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(size, top, bottom):
    w, h = size
    g = Image.new("RGB", (1, h))
    for y in range(h):
        g.putpixel((0, y), lerp(top, bottom, y / (h - 1)))
    return g.resize((w, h))


def speaker_icon(px):
    s = px * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    # rounded-square background, warm orange gradient
    bg = gradient((s, s), (255, 140, 40), (232, 70, 20)).convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img.paste(bg, (0, 0), mask)

    # speaker body
    bw, bh = int(s * 0.46), int(s * 0.70)
    x0, y0 = (s - bw) // 2, int(s * 0.15)
    body = [x0, y0, x0 + bw, y0 + bh]

    # light-ring glow behind the woofers
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx = s // 2
    for cy, r in ((y0 + int(bh * 0.30), int(bw * 0.36)), (y0 + int(bh * 0.72), int(bw * 0.36))):
        gd.ellipse([cx - r * 1.35, cy - r * 1.35, cx + r * 1.35, cy + r * 1.35], fill=(255, 230, 120, 170))
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.04))
    img.alpha_composite(glow)

    d = ImageDraw.Draw(img)
    d.rounded_rectangle(body, radius=int(bw * 0.18), fill=(24, 24, 30))
    # woofers with lit rings
    for cy in (y0 + int(bh * 0.30), y0 + int(bh * 0.72)):
        r = int(bw * 0.33)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 200, 70), width=int(s * 0.022))
        r2 = int(r * 0.78)
        d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], fill=(48, 48, 58))
        r3 = int(r * 0.30)
        d.ellipse([cx - r3, cy - r3, cx + r3, cy + r3], fill=(80, 80, 94))
    # tweeter dot between woofers
    ty, tr = y0 + int(bh * 0.51), int(bw * 0.06)
    d.ellipse([cx - tr, ty - tr, cx + tr, ty + tr], fill=(255, 200, 70))
    # sound waves on both sides
    for side in (-1, 1):
        for i, rr in enumerate((0.33, 0.41)):
            r = int(s * rr)
            bbox = [cx - r, s // 2 - r, cx + r, s // 2 + r]
            start, end = (-35, 35) if side == 1 else (145, 215)
            d.arc(bbox, start, end, fill=(255, 255, 255, 230 - i * 70), width=int(s * 0.022))
    return img.resize((px, px), Image.LANCZOS)


def logo(w, h, icon):
    s = SS
    img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    ic = icon.resize((h * s, h * s), Image.LANCZOS)
    img.alpha_composite(ic, (0, 0))
    d = ImageDraw.Draw(img)
    x = int(h * s * 1.10)
    avail = w * s - x - s * 4

    def fit(path, size, text):
        font = ImageFont.truetype(path, size)
        while d.textlength(text, font=font) > avail:
            size -= s
            font = ImageFont.truetype(path, size)
        return font

    big = fit("C:/Windows/Fonts/segoeuib.ttf", int(h * s * 0.36), "PartyBox")
    small = fit("C:/Windows/Fonts/segoeui.ttf", int(h * s * 0.17), "for Home Assistant")
    d.text((x, int(h * s * 0.16)), "PartyBox", font=big, fill=(232, 80, 20))
    d.text((x + int(s * 2), int(h * s * 0.60)), "for Home Assistant", font=small, fill=(110, 110, 120))
    return img.resize((w, h), Image.LANCZOS)


icon = speaker_icon(256)
icon.resize((128, 128), Image.LANCZOS).save(OUT / "icon.png")
logo(250, 100, speaker_icon(400)).save(OUT / "logo.png")
icon.save(OUT / "icon-256.png")
print("written to", OUT)
