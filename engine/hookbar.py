"""Hook pill ("black bar" style) drawn with Pillow (~0.1 s), plus colour emoji.
Headline on the pill (Montserrat 700; shrinks to fit 960 px, wraps to 2 lines if still too long), optional white sub line.
Streamer-clip style: "Streamer gets robbed by an NPC in GTA 6 😭" — emoji drawn with Apple Color Emoji."""
import os, re
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Montserrat-VF.ttf")
EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
EMOJI_RE = re.compile("([\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]+)")


def _font(px, weight):
    f = ImageFont.truetype(FONT, int(px))
    try:
        f.set_variation_by_axes([weight])
    except Exception:  # noqa: BLE001
        pass
    return f


def _runs(text):
    return [(t, bool(EMOJI_RE.fullmatch(t))) for t in EMOJI_RE.split(text) if t]


def _emoji_img(e, px):
    f = ImageFont.truetype(EMOJI_FONT, 160)
    im = Image.new("RGBA", (220 * max(1, len(e)), 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((0, 0), e, font=f, embedded_color=True)
    box = im.getbbox()
    im = im.crop(box) if box else im
    return im.resize((max(1, round(im.width * px / im.height)), px), Image.LANCZOS)


def _width(text, font, px):
    w = 0
    for t, is_e in _runs(text):
        w += (px * 1.05 * len([c for c in t if ord(c) > 0x2000 and c not in "\u200d\ufe0f"]) + px * .15) if is_e else font.getlength(t)
    return w


def _draw_line(im, text, font, px, cx, cy, fill):
    d = ImageDraw.Draw(im)
    x = cx - _width(text, font, px) / 2
    for t, is_e in _runs(text):
        if is_e:
            em = _emoji_img(t, int(px * .95))
            im.alpha_composite(em, (int(x + px * .08), int(cy - em.height / 2)))
            x += em.width + px * .15
        else:
            d.text((x, cy), t, font=font, fill=fill, anchor="lm")
            x += font.getlength(t)


def _wrap2(text, font, px, maxw):
    words = text.split()
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        w = max(_width(a, font, px), _width(b, font, px))
        if best is None or w < best[0]:
            best = (w, [a, b])
    return best[1] if best else [text]


def draw(out, head, sub="", color="#00d4ff", sc=2):
    maxW, M = 960, 30
    fs = 62
    while fs > 46 and _width(head, _font(fs, 700), fs) > maxW:
        fs -= 2
    rows = [head] if _width(head, _font(fs, 700), fs) <= maxW else _wrap2(head, _font(fs, 700), fs, maxW)
    while fs > 32 and max(_width(r, _font(fs, 700), fs) for r in rows) > maxW:
        fs -= 2
    px, py, lh = round(fs * .48), round(fs * .23), round(fs * 1.24)
    sfs = min(44, round(fs * .72)); sLH = round(sfs * 1.27)
    tF, sF = _font(fs * sc, 700), _font(sfs * sc, 600)
    bw = max(_width(r, _font(fs, 700), fs) for r in rows) + 2 * px; bh = lh * len(rows) + 2 * py
    sw = _font(sfs, 600).getlength(sub) if sub else 0
    W = max(bw, sw) + 2 * M; H = M + bh + ((20 + sLH) if sub else 0) + M
    im = Image.new("RGBA", (int(W * sc), int(H * sc)), (0, 0, 0, 0))
    cx = W / 2
    ImageDraw.Draw(im).rounded_rectangle([(cx - bw / 2) * sc, M * sc, (cx + bw / 2) * sc, (M + bh) * sc],
                                         radius=round(fs * .35) * sc, fill=color)
    for i, r in enumerate(rows):
        _draw_line(im, r, tF, fs * sc, cx * sc, (M + py + i * lh + lh / 2 + 2) * sc, "#111111")
    if sub:
        y = (M + bh + 20 + sLH / 2) * sc
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((cx * sc, y + 2 * sc), sub, font=sF, fill=(0, 0, 0, 170), anchor="mm")
        im = Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(4 * sc)), im)
        _draw_line(im, sub, sF, sfs * sc, cx * sc, y, "#ffffff")  # emoji-aware (was tofu boxes)
    box = im.getbbox()
    if box:
        im = im.crop((max(0, box[0] - 8), max(0, box[1] - 8), min(im.width, box[2] + 8), min(im.height, box[3] + 8)))
    im.save(out)
    return out
