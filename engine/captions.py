"""Word-timed captions → ASS (libass) for 1080×1920.

Beats are built from whisper words: 1–3 words for energetic presets, up to 2 lines for podcast/cinematic, broken at
punctuation, pauses and phrase-ish boundaries. One emphasised keyword per beat at most (numbers, names, strong words,
or keywords the detector marked). Every beat is measured with the real font metrics (libass sizing rule:
em = size × unitsPerEm / (winAscent + winDescent)) so text stays inside the safe zone:
centre 55–72 % of height, never above 12 % or below 80 %, ≤ 86 % of width.
"""
import json
import os
import re
import struct

from config import FONTS, PRESETS_FILE

W, H = 1080, 1920
SAFE = {"center_min": 0.55, "center_max": 0.72, "top_min": 0.12, "bottom_max": 0.80, "max_w": 0.86}
STRONG = set("""never always insane crazy secret biggest best worst only first last huge massive million billion thousand
hundred free money dead died kill killed love hate wrong right truth lie lied impossible literally actually seriously
everything nothing everyone nobody why how stop wait shocked scared terrified fastest slowest hardest easiest broke rich
fired quit won lost win lose record finally""".split())
ANIM_SCALE = {"pop": 1.04, "pop_big": 1.10, "fade": 1.0, "none": 1.0}


def load_presets():
    with open(PRESETS_FILE) as f:
        return json.load(f)


# ---------------------------------------------------------------------------------------------- font metrics
_METRICS = {}


def _font_metrics(path):
    if path in _METRICS:
        return _METRICS[path]
    with open(path, "rb") as fh:
        d = fh.read()
    n = struct.unpack(">H", d[4:6])[0]
    tables = {}
    for i in range(n):
        tag, _cs, off, ln = struct.unpack(">4sIII", d[12 + 16 * i:28 + 16 * i])
        tables[tag] = off
    upem = struct.unpack(">H", d[tables[b"head"] + 18:tables[b"head"] + 20])[0]
    o = tables.get(b"OS/2")
    if o is not None:
        wa, wd = struct.unpack(">HH", d[o + 74:o + 78])
    else:
        hh = tables[b"hhea"]
        a, dsc = struct.unpack(">hh", d[hh + 4:hh + 8])
        wa, wd = a, -dsc
    _METRICS[path] = (upem, wa + wd)
    return _METRICS[path]


_PIL = {}


def _pil_font(preset, size):
    from PIL import ImageFont
    path = os.path.join(FONTS, preset["font_file"])
    upem, h = _font_metrics(path)
    em = size * upem / h
    key = (path, round(em, 1))
    if key not in _PIL:
        _PIL[key] = ImageFont.truetype(path, max(1, int(round(em))))
    return _PIL[key]


def text_width(preset, text, size=None):
    size = size or preset["size"]
    f = _pil_font(preset, size)
    return f.getlength(text)


# ---------------------------------------------------------------------------------------------- beats
def _clean(w):
    return re.sub(r"\s+", " ", w).strip()


def normalise_words(words):
    """Casing/punctuation from the transcript: capitalise sentence starts, fix standalone 'i'."""
    out = []
    cap_next = True
    for w in words:
        t = _clean(w["word"])
        if not t:
            continue
        if re.fullmatch(r"i('m|'ll|'d|'ve)?", t, re.I):
            t = "I" + t[1:]
        if cap_next and t[:1].isalpha():
            t = t[0].upper() + t[1:]
        cap_next = t[-1:] in ".?!"
        out.append(dict(w, word=t))
    return out


def _emph_score(word, first_in_sentence, keywords):
    bare = re.sub(r"[^\w$%'-]", "", word)
    lw = bare.lower()
    if not bare:
        return 0
    if lw in keywords:
        return 3
    if re.search(r"\d", bare) or bare.startswith("$") or bare.endswith("%"):
        return 2.8
    if lw.strip("'") in STRONG:
        return 2
    if bare[:1].isupper() and not first_in_sentence and lw not in ("i", "i'm", "i'll", "i've", "i'd"):
        return 1.5
    return 0


CONJ = set("and but so because that to which who when if or then".split())


def _split_lines(ws, preset):
    """Choose a line break for a 2-line beat (index of first word of line 2) or None. Upper-case display presets
    break only when one line would be too wide; sentence-case presets also keep lines to max_chars_line."""
    if preset.get("max_lines", 1) < 2 or len(ws) < 2:
        return None
    text = " ".join(w["word"] for w in ws)
    shown = text.upper() if preset.get("case") == "upper" else text
    over = ANIM_SCALE.get(preset.get("animation", "none"), 1.0)
    width = text_width(preset, shown) * over + 2 * float(preset.get("outline_w", 0))
    too_wide = width > 0.80 * W
    too_long = preset.get("case") != "upper" and len(text) > preset.get("max_chars_line", 24) and len(ws) >= 3
    if not (too_wide or too_long):
        return None
    best, best_cost = None, 1e9
    for k in range(1, len(ws)):
        l1 = " ".join(w["word"] for w in ws[:k])
        l2 = " ".join(w["word"] for w in ws[k:])
        cost = abs(len(l1) - len(l2))
        if ws[k - 1]["word"][-1:] in ",;:":
            cost -= 6
        if ws[k]["word"].lower().strip(",.") in CONJ:
            cost -= 4
        if cost < best_cost:
            best, best_cost = k, cost
    return best


def build_beats(words, preset, clip_dur, keywords=()):
    """words: clip-relative [{word,start,end}] → [{start,end,text,emphasis:[idx],lines:[[...]]}]."""
    words = normalise_words(words)
    kw = {re.sub(r"[^\w$%'-]", "", k).lower() for k in (keywords or []) if k}
    maxw = int(preset.get("words_per_beat", 3))
    maxc = int(preset.get("max_chars_line", 20)) * int(preset.get("max_lines", 1))
    beats, cur = [], []
    for i, w in enumerate(words):
        if cur:
            prev = cur[-1]
            text_len = len(" ".join(x["word"] for x in cur + [w]))
            if (len(cur) >= maxw or w["start"] - prev["end"] > 0.35 or prev["word"][-1:] in ".?!,;:" or text_len > maxc):
                beats.append(cur)
                cur = []
        cur.append(w)
    if cur:
        beats.append(cur)
    out = []
    sent_start = True
    for bi, ws in enumerate(beats):
        start = max(0.0, ws[0]["start"])
        end = ws[-1]["end"]
        nxt = beats[bi + 1][0]["start"] if bi + 1 < len(beats) else clip_dur
        end = nxt if nxt - end < 0.6 else end + 0.2
        end = min(end, nxt, clip_dur)
        if end - start < 0.12:
            end = min(nxt, clip_dur, start + 0.12)
        if end <= start:
            continue
        scores = []
        for k, w in enumerate(ws):
            first = sent_start if k == 0 else ws[k - 1]["word"][-1:] in ".?!"
            scores.append(_emph_score(w["word"], first, kw))
        sent_start = ws[-1]["word"][-1:] in ".?!"
        emph = [max(range(len(ws)), key=lambda k: scores[k])] if max(scores) > 0 else []
        brk = _split_lines(ws, preset)
        texts = [w["word"] for w in ws]
        if preset.get("case") == "upper":
            texts = [t.upper() for t in texts]
        lines = [texts[:brk], texts[brk:]] if brk else [texts]
        out.append({"start": round(start, 3), "end": round(end, 3), "text": "\n".join(" ".join(l) for l in lines),
                    "emphasis": emph})
    # enforce strict non-overlap after rounding
    for a, b in zip(out, out[1:]):
        if a["end"] > b["start"]:
            a["end"] = b["start"]
    return [c for c in out if c["end"] - c["start"] >= 0.05 and c["text"].strip()]


# ---------------------------------------------------------------------------------------------- layout + safe zone
def layout(cap, preset):
    """Geometry of one caption beat at the preset size: {'size', 'width', 'height', 'cy', 'top', 'bottom'} in px.
    Shrinks the size until the widest line (incl. outline/box padding and animation overshoot) fits ≤ 86 % width."""
    size = float(preset["size"])
    lines = cap["text"].split("\n")
    pad = 2 * float(preset.get("outline_w", 0))
    over = ANIM_SCALE.get(preset.get("animation", "none"), 1.0)
    maxw = SAFE["max_w"] * W
    while True:
        wmax = max(text_width(preset, l, size) for l in lines) * over + pad
        if wmax <= maxw or size <= 24:
            break
        size *= 0.94
    height = len(lines) * size * 1.0 * over + pad
    cy = preset["position"] * H
    # keep the block inside [top_min, bottom_max] even when it is tall
    top = cy - height / 2
    bottom = cy + height / 2
    if bottom > SAFE["bottom_max"] * H:
        cy -= bottom - SAFE["bottom_max"] * H
    if top < SAFE["top_min"] * H:
        cy += SAFE["top_min"] * H - top
    cy = min(max(cy, SAFE["center_min"] * H), SAFE["center_max"] * H)
    return {"size": round(size, 1), "width": round(wmax, 1), "height": round(height, 1), "cy": round(cy, 1),
            "top": round(cy - height / 2, 1), "bottom": round(cy + height / 2, 1)}


def check_safe(caps, preset):
    """List of violations (empty = all inside the safe zone)."""
    bad = []
    for i, c in enumerate(caps):
        g = layout(c, preset)
        if not (SAFE["center_min"] * H - 0.5 <= g["cy"] <= SAFE["center_max"] * H + 0.5):
            bad.append(f"beat {i}: centre {g['cy'] / H:.3f}")
        if g["top"] < SAFE["top_min"] * H - 0.5 or g["bottom"] > SAFE["bottom_max"] * H + 0.5:
            bad.append(f"beat {i}: vertical extent {g['top'] / H:.3f}-{g['bottom'] / H:.3f}")
        if g["width"] > SAFE["max_w"] * W + 0.5:
            bad.append(f"beat {i}: width {g['width'] / W:.3f}")
    return bad


def check_timing(caps, clip_dur):
    bad = []
    for i, c in enumerate(caps):
        if not c.get("text", "").strip():
            bad.append(f"beat {i} empty")
        if c["start"] < -1e-3 or c["end"] > clip_dur + 1e-3 or c["end"] <= c["start"]:
            bad.append(f"beat {i} outside clip time ({c['start']:.2f}-{c['end']:.2f} of {clip_dur:.2f})")
    for i, (a, b) in enumerate(zip(caps, caps[1:])):
        if a["end"] > b["start"] + 1e-3:
            bad.append(f"beats {i}/{i + 1} overlap")
    return bad


# ---------------------------------------------------------------------------------------------- ASS
def _ass_color(hex_, alpha=1.0):
    h = hex_.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(round(255 * (1 - alpha)))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(t):
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(s):
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


NOTE_SIZE = 84


def wrap_note(text, preset, size=NOTE_SIZE, max_lines=2):
    """Greedy wrap of the on-screen context note to <= 2 lines within 86 % width (box padding included);
    shrinks the size if a word or the result still doesn't fit."""
    words = text.split()
    while True:
        lines, cur = [], ""
        for w in words:
            cand = (cur + " " + w).strip()
            if text_width(preset, cand, size) + 28 <= SAFE["max_w"] * W or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        fits = len(lines) <= max_lines and all(text_width(preset, l, size) + 28 <= SAFE["max_w"] * W for l in lines)
        if fits or size <= 30:
            return lines[:max_lines], int(size)
        size *= 0.92


def avoid_cy(g, zones, start, end):
    """Centre y for a beat that must stay clear of burned-in text rows (output px) during [start, end]; scans the
    55–72 % safe band. Returns the preset position if nothing overlaps in time or no clear slot exists."""
    hit = [z for z in (zones or []) if z["start"] < end and z["end"] > start]
    if not hit:
        return g["cy"]
    h = g["height"]
    pad = 12

    def clear(cy):
        return all(cy + h / 2 + pad <= z["y0"] or cy - h / 2 - pad >= z["y1"] for z in hit)
    if clear(g["cy"]):
        return g["cy"]
    lo, hi = SAFE["center_min"] * H, SAFE["center_max"] * H
    cands = sorted((lo + k * (hi - lo) / 34 for k in range(35)), key=lambda c: abs(c - g["cy"]))
    for cy in cands:
        if clear(cy) and cy - h / 2 >= SAFE["top_min"] * H and cy + h / 2 <= SAFE["bottom_max"] * H:
            return cy
    return g["cy"]


def to_ass(caps, preset, clip_dur, context_note=None, avoid=None):
    p = preset
    bold = -1 if p.get("bold") else 0
    border_style = 3 if p.get("box") else 1
    outline_col = _ass_color(p["outline"], p.get("outline_alpha", 1.0))
    back_col = _ass_color("#000000", 0.55)
    style = (f"Style: Cap,{p['font']},{p['size']},{_ass_color(p['primary'])},{_ass_color(p['accent'])},{outline_col},{back_col},"
             f"{bold},0,0,0,100,100,0,0,{border_style},{p.get('outline_w', 0)},{p.get('shadow', 0)},5,40,40,0,1")
    ctx_style = (f"Style: Ctx,{p['font']},{NOTE_SIZE},{_ass_color('#FFFFFF')},{_ass_color('#FFFFFF')},{_ass_color('#000000', 0.6)},"
                 f"{back_col},{bold},0,0,0,100,100,0,0,3,14,0,5,60,60,0,1")
    head = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
            "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding", style, ctx_style, "", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    anim = {"pop": r"\fscx88\fscy88\t(0,80,\fscx104\fscy104)\t(80,150,\fscx100\fscy100)",
            "pop_big": r"\fscx70\fscy70\t(0,90,\fscx110\fscy110)\t(90,170,\fscx100\fscy100)",
            "fade": r"\fad(110,90)", "none": ""}.get(p.get("animation", "none"), "")
    acc = _ass_color(p["accent"])
    pri = _ass_color(p["primary"])
    ev = []
    for ci, c in enumerate(caps):
        g = layout(c, p)
        a_tag = anim
        if p.get("animation") == "fade":
            # fade only into/out of silence; back-to-back beats switch cleanly (no blinking between beats)
            fin = 110 if ci == 0 or c["start"] - caps[ci - 1]["end"] > 0.25 else 0
            fout = 90 if ci == len(caps) - 1 or caps[ci + 1]["start"] - c["end"] > 0.25 else 0
            a_tag = rf"\fad({fin},{fout})" if fin or fout else ""
        words_lines = [l.split(" ") for l in c["text"].split("\n")]
        k = 0
        out_lines = []
        for ws in words_lines:
            parts = []
            for w in ws:
                t = _esc(w)
                if k in (c.get("emphasis") or []):
                    if p.get("italic_emphasis"):
                        t = r"{\i1}" + t + r"{\i0}"
                    else:
                        t = r"{\c" + acc + "}" + t + r"{\c" + pri + "}"
                parts.append(t)
                k += 1
            out_lines.append(" ".join(parts))
        size_tag = rf"\fs{g['size']:.0f}" if abs(g["size"] - p["size"]) > 0.5 else ""
        cy = avoid_cy(g, avoid, c["start"], c["end"])
        tags = rf"{{\an5\pos({W // 2},{cy:.0f}){size_tag}{a_tag}}}"
        ev.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Cap,,0,0,0,,{tags}" + r"\N".join(out_lines))
    if context_note:
        lines, size = wrap_note(context_note.strip()[:140], p)
        ev.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(min(clip_dur, 3.5))},Ctx,,0,0,0,,"
                  + "{" + rf"\an5\pos({W // 2},{int(0.2 * H)})\fs{size}\fad(150,200)" + "}" + r"\N".join(_esc(l) for l in lines))
    return "\n".join(head + ev) + "\n"
