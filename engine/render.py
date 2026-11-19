"""Render one clip straight from the source: fast seek (-ss before -i) + accurate trim, reframe crop expression,
scale to 1080×1920, HDR→SDR when needed, libass captions with bundled fonts, optional thin progress bar,
two-pass loudnorm to −14 LUFS / −1 dBTP, H.264 High + AAC 48 kHz, attribution in metadata. Thumbnail at best_open_s.
"""
import hashlib
import json
import os
import re

import captions as capm
import media
import reframe
from config import FONTS

OUT_W, OUT_H = 1080, 1920


def signature(clip, preset_name, settings):
    """Hash of everything that changes the rendered pixels/audio — used to skip valid renders on resume."""
    crop = clip.get("crop") or {}
    payload = {"s": round(clip["start_s"], 3), "e": round(clip["end_s"], 3), "cap": clip.get("captions") or [],
               "preset": preset_name, "crop": {k: crop.get(k) for k in ("mode", "x", "track", "text_spans")},
               "ctx": (clip.get("context_note") or "").strip() or None,
               "bar": bool(settings.get("progress_bar", True)), "punch": bool(settings.get("punch_in", False)), "layout": settings.get("layout", "crop"), "v": 12}
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def out_fps(pr):
    f = pr.get("fps") or 30.0
    return f if 23 <= f <= 60 else 30.0


def measure_loudness(src, pr, a, dur, cancel=None, work=None):
    """Cached per (source, range): re-renders of the same range skip the measuring pass."""
    cache = None
    if work:
        cache = os.path.join(work, "loud_" + hashlib.sha1(f"{src}|{a:.3f}|{dur:.3f}".encode()).hexdigest()[:12] + ".json")
        if os.path.exists(cache):
            try:
                with open(cache) as f:
                    return json.load(f)
            except ValueError:
                pass
    j = _measure_loudness(src, pr, a, dur, cancel)
    if cache:
        with open(cache, "w") as f:
            json.dump(j, f)
    return j


def _measure_loudness(src, pr, a, dur, cancel=None):
    am = media.amap(pr)
    if am is None:
        return None
    err = media.ffmpeg(["-ss", f"{a:.3f}", "-i", src, "-t", f"{dur:.3f}", "-map", am, "-vn",
                        "-af", "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json", "-f", "null", "-"], cancel=cancel)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        return None
    j = json.loads(m.group(0))
    try:
        if float(j["input_i"]) < -70:  # digital silence: nothing to normalise
            return None
    except (ValueError, KeyError):
        return None
    return j


def fit_geometry(pr):
    """Fit-to-width placement of the full 16:9 frame inside 1080×1920: (height, y offset) in output px."""
    fh = int(round(OUT_W * pr["height"] / pr["width"] / 2)) * 2
    return fh, (OUT_H - fh) // 2


def text_avoid_zones(pr, crop):
    """Output-pixel rows occupied by burned-in text during fit-to-width spans (captions must stay out of them)."""
    spans = (crop or {}).get("text_spans") or []
    if not spans or reframe.crop_frac(pr) is None:
        return []
    fh, yo = fit_geometry(pr)
    return [{"start": sp["start"], "end": sp["end"], "y0": yo + sp["band"][0] * fh, "y1": yo + sp["band"][1] * fh} for sp in spans]


YAP_HOLD = 3.5  # seconds the hook pill stays up (same as the old context box)


def split_hook(note):
    """'headline || (sub)' -> (headline, sub). Hook-pill style: lowercase headline, parenthetical under it."""
    head, _, sub = (note or "").partition("||")
    head, sub = head.strip(), sub.strip()
    if sub and len(head.split()) <= 2:  # "speed || tries to fix..." -> one pill: "Speed tries to fix..."
        head, sub = (head[:1].upper() + head[1:] + " " + sub.strip("() ")).strip(), ""
    head, sub = head.strip(), sub.strip()
    if sub and not sub.startswith("("):
        sub = f"({sub.strip('()')})"
    return head, sub  # keep the writer's casing (streamer-clip style: name first, one CAPS word, emoji at the end)


def pill_png(note, work, clip_id):
    """Draw the hook pill PNG with Pillow (~0.1 s). Returns a PNG path or None (caller falls back)."""
    head, sub = split_hook(note)
    if not head:
        return None
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, "hook_" + hashlib.sha1(f"{head}|{sub}|white".encode()).hexdigest()[:12] + ".png")
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        return out  # same text drawn before
    try:  # Pillow hook bar: ~0.1 s
        import hookbar
        return hookbar.draw(out, head, sub, "#ffffff")  # white box, black text
    except Exception:  # noqa: BLE001  no pill: the clip renders without it
        return None


def face_spans(work, a, dur, max_share=0.33):
    """Stream layout: moments to cut in close on the streamer = the strongest loud bursts (reactions) inside the clip.
    Returns [(start_rel, end_rel)], merged, together at most max_share of the clip."""
    sig = media.read_json(os.path.join(os.path.dirname(work), "signals.json")) or {}
    bursts = [b for b in sig.get("loud_bursts", []) if a <= b["t"] <= a + dur]
    bursts.sort(key=lambda b: b.get("db_over", 0), reverse=True)
    spans, used = [], 0.0
    for b in bursts:
        if b.get("db_over", 0) < 13 or used >= max_share * dur:
            break
        s0, s1 = max(0.0, b["t"] - a - 0.25), min(dur, b["t"] - a + b.get("dur", 0.4) + 1.1)
        if any(s0 < y1 + 1.0 and s1 > y0 - 1.0 for y0, y1 in spans):
            continue  # keep close-ups apart so it doesn't flicker
        spans.append((s0, s1)); used += s1 - s0
    return sorted(spans)


def cam_box(crop):
    """The streamer's face cam: a face in a side third of the frame (overlays sit in corners). A face near the middle is a
    game character or an IRL shot, not a cam, so those clips keep the full 16:9 layout."""
    fb = (crop or {}).get("cam_box") or (crop or {}).get("face_box")
    return fb if fb and (fb[0] < 0.3 or fb[0] > 0.7) else None


def build_filter(pr, crop, ass_path, dur, preset, settings, payoff_rel=None, hook_png=None):
    v = []
    cwf = reframe.crop_frac(pr)
    spans = (crop or {}).get("text_spans") or []
    stream = settings.get("layout") == "stream" and cwf is not None
    pre = ""
    fb0 = cam_box(crop) if stream else None
    hook_y = int(0.15 * OUT_H)
    no_caps = False
    if stream and fb0 and settings.get("split", True):
        # Split layout: face cam on top, gameplay below, orange bolt on the seam.
        TH = 860; BH = OUT_H - TH
        W, Hh = pr["width"], pr["height"]
        ch = min(Hh, max(Hh * 0.2, fb0[2] * Hh * 3.4)); cw = min(W, ch * OUT_W / TH)
        ch = cw * TH / OUT_W
        cx, cy = fb0[0] * W, fb0[1] * Hh
        x0 = int(min(max(0, cx - cw / 2), W - cw)); y0 = int(min(max(0, cy - ch * 0.42), Hh - ch)); x0 = int(x0 + cw * 0.05); cw = cw * 0.9; ch = ch * 0.9
        gw = int(Hh * OUT_W / BH // 2 * 2); gx = int((W - gw) / 2)
        bolt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "seam_bolt.png").replace(":", "\\:")
        pre = ("[0:v:0]split=2[st][sg];"
               f"[st]crop={int(cw)//2*2}:{int(ch)//2*2}:{x0}:{y0},hqdn3d=1.5:1.5:0:0,scale={OUT_W}:{TH}:flags=lanczos,cas=0.7[top];"
               f"[sg]crop={gw}:{Hh}:{gx}:0,scale={OUT_W}:{BH}:flags=lanczos[bot];"
               f"[top][bot]vstack[stk];movie='{bolt}',loop=-1:size=1,setpts=N/FRAME_RATE/TB,format=rgba[bolt];"
               f"[stk][bolt]overlay=x=(W-w)/2:y={TH}-h/2:shortest=1[base0];[base0]")
        stream = False  # skip the 16:9 paths below
        hook_y = "860-h/2"
        no_caps = True  # split clips: hook box only, no word captions
    if stream:
        # stream clips = the whole 16:9 frame, no crops, no face tracking (cheap blurred fill behind)
        fh, _yo = fit_geometry(pr)
        pre = ("[0:v:0]split=2[sb][sc];"
               f"[sb]scale={OUT_W}:{fh}:flags=lanczos[fit];"
               f"[sc]scale=-2:480:flags=fast_bilinear,crop=270:480,gblur=sigma=12,scale={OUT_W}:{OUT_H}:flags=fast_bilinear,"
               "eq=brightness=-0.16:saturation=0.85[bg];"
               "[bg][fit]overlay=(W-w)/2:(H-h)/2[base0];[base0]")
    elif pre:
        pass
    elif cwf is not None and spans:
        # burned-in titles would be sliced by the 9:16 window: for those spans show the whole frame fit-to-width over a
        # blurred, darkened fill of itself. `enable` switches per frame, so it cuts hard in and out (no zoom).
        if (crop or {}).get("mode") == "manual":
            x_expr = reframe.crop_expr([], x_const=float(crop.get("x", 0.5)))
        else:
            x_expr = reframe.crop_expr((crop or {}).get("track") or [], x_const=float((crop or {}).get("x", 0.5)))
        fh, _yo = fit_geometry(pr)
        on = "+".join(f"between(t,{sp['start']:.3f},{sp['end']:.3f})" for sp in spans)
        fit_scale = f"scale={OUT_W}:{fh}:flags=lanczos"
        close = f"crop=w=trunc(ih*9/16/2)*2:h=ih:x='{x_expr}':y=0,scale={OUT_W}:{OUT_H}:flags=lanczos"
        if stream:
            zw = int(round(OUT_W * 1.25 / 2)) * 2  # full frame zoomed 25% (edges trimmed) so the game fills more of the screen
            fit_scale = f"scale={zw}:-2:flags=lanczos,crop={OUT_W}:ih"
            fb = (crop or {}).get("face_box")
            if fb:  # tight close-up: ~4 face-heights tall, centred on the face, kept inside the frame
                H0 = min(pr["height"], max(pr["height"] * 0.3, fb[2] * pr["height"] * 4.2))
                H0 = int(H0 // 2 * 2); W0 = int(H0 * 9 / 16 // 2 * 2)
                cx, cy = fb[0] * pr["width"], fb[1] * pr["height"]
                x0 = int(min(max(0, cx - W0 / 2), pr["width"] - W0)); y0 = int(min(max(0, cy - H0 * 0.45), pr["height"] - H0))
                close = f"crop={W0}:{H0}:{x0}:{y0},scale={OUT_W}:{OUT_H}:flags=lanczos"
        if stream:  # full 16:9 frame by default; cut in close only on the reaction moments
            face_on = "+".join(f"between(t,{x0:.3f},{x1:.3f})" for x0, x1 in settings.get("face_spans") or []) or "0"
            crop_on = face_on
        pre = ("[0:v:0]split=3[sa][sb][sc];"
               f"[sa]{close}[cr];"
               f"[sb]{fit_scale}[fit];"
               f"[sc]scale=-2:480,crop=270:480,gblur=sigma=12,scale={OUT_W}:{OUT_H},eq=brightness=-0.16:saturation=0.85[bg];"
               "[bg][fit]overlay=(W-w)/2:(H-h)/2[fitted];"
               f"[fitted][cr]overlay=0:0:enable='{crop_on if stream else 'not(' + on + ')'}'[base0];[base0]")
    if pre:
        pass
    elif cwf is None:
        # already vertical (or taller): fit + centre-crop/pad to exactly 1080×1920
        v.append(f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={OUT_W}:{OUT_H}")
    else:
        if (crop or {}).get("mode") == "manual":
            x_expr = reframe.crop_expr([], x_const=float(crop.get("x", 0.5)))
        else:
            x_expr = reframe.crop_expr((crop or {}).get("track") or [], x_const=float((crop or {}).get("x", 0.5)))
        v.append(f"crop=w=trunc(ih*9/16/2)*2:h=ih:x='{x_expr}':y=0")
        v.append(f"scale={OUT_W}:{OUT_H}:flags=lanczos")
    if settings.get("punch_in") and payoff_rel is not None:
        a = max(0.0, payoff_rel)
        z = 1.05
        v.append(f"scale=w='if(gte(t,{a:.2f}),{int(OUT_W * z)},{OUT_W})':h='if(gte(t,{a:.2f}),{int(OUT_H * z)},{OUT_H})':eval=frame,"
                 f"crop={OUT_W}:{OUT_H}")
    sdr = media.sdr_filter(pr)
    if sdr:
        v.append(sdr)
    v.append(f"fps={out_fps(pr):.3f}")
    v.append("format=yuv420p")
    esc = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    fd = FONTS.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    if not no_caps:
        v.append(f"ass=filename='{esc}':fontsdir='{fd}'")
    chain = (pre if pre else "[0:v:0]") + ",".join(v)
    if hook_png:  # hook pill over the top for the first seconds (input #1 = the looped PNG)
        # stays up for the whole clip
        chain += (f"[pre_hook];[1:v]format=rgba,scale='min(880,iw)':-1,fade=t=in:st=0:d=0.18:alpha=1[hk];"
                  f"[pre_hook][hk]overlay=x=(W-w)/2:y={hook_y}:eof_action=repeat,format=yuv420p")
    if settings.get("progress_bar", True) and preset.get("progress_bar"):
        col = preset.get("bar_color", "#FFFFFF").lstrip("#")
        chain += "[base];" + f"color=c=0x{col}:s={OUT_W}x8:r={out_fps(pr):.3f},format=yuv420p[bar];" \
                 f"[base][bar]overlay=x='-W+W*t/{dur:.3f}':y=0:shortest=1:eval=frame,format=yuv420p[v]"
    else:
        chain += "[v]"
    return chain


def render(src, pr, clip, preset_name, preset, settings, attribution, out_mp4, work, cancel=None, progress=None):
    """Writes out_mp4 (atomically). Returns dict(loudness, ass, cmd)."""
    a, b = float(clip["start_s"]), float(clip["end_s"])
    dur = b - a
    os.makedirs(work, exist_ok=True)
    ass_path = os.path.join(work, f"clip_{clip['id']}.ass")
    note = (clip.get("context_note") or "").strip() or None  # every clip with hook text gets the pill (needs_context no longer gates it)
    hook_png = pill_png(note, work, clip["id"]) if note else None
    with open(ass_path, "w") as f:
        f.write(capm.to_ass(clip.get("captions") or [], preset, dur, context_note=None if hook_png else split_hook(note)[0] if note else None,
                            avoid=text_avoid_zones(pr, clip.get("crop"))))
    payoff_rel = None
    if settings.get("punch_in") and clip.get("payoff_ts") is not None:
        payoff_rel = float(clip["payoff_ts"]) - a
    fc = build_filter(pr, clip.get("crop"), ass_path, dur, preset, settings, payoff_rel, hook_png=None)
    loud = measure_loudness(src, pr, a, dur, cancel=cancel, work=work)
    args = ["-hwaccel", "videotoolbox", "-ss", f"{a:.3f}", "-t", f"{dur + 0.5:.3f}", "-i", src, "-filter_complex", fc, "-map", "[v]"]
    am = media.amap(pr)
    if am is not None:
        if loud:
            af = (f"loudnorm=I=-14:TP=-1.0:LRA=11:measured_I={loud['input_i']}:measured_TP={loud['input_tp']}:"
                  f"measured_LRA={loud['input_lra']}:measured_thresh={loud['input_thresh']}:offset={loud['target_offset']}:"
                  f"linear=true:print_format=summary")
        else:
            af = "anull"
        args += ["-map", am, "-af", af + ",aresample=48000", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    # hardware encoder (bench 2026-11-19: 12.5 s vs 66.7 s for x264 medium, VMAF 94.6 vs 96.2) — speed first
    args += ["-c:v", "h264_videotoolbox", "-profile:v", "high", "-q:v", "65", "-allow_sw", "1", "-pix_fmt", "yuv420p",
             "-r", f"{out_fps(pr):.3f}", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
             "-movflags", "+faststart", "-map_metadata", "-1",
             "-metadata", f"comment={attribution}", "-metadata", f"artist={attribution.split(' — ')[0].replace('Source: ', '')}",
             "-metadata", f"title={clip.get('title') or ''}", "-metadata", "encoder=ClipForge"]
    base = base_path(out_mp4)
    tmp = f"{base}.{os.getpid()}.part.mp4"
    args += ["-t", f"{dur:.3f}", tmp]
    os.makedirs(os.path.dirname(out_mp4), exist_ok=True)
    media.ffmpeg(args, cancel=cancel, progress=progress, duration=dur)
    os.replace(tmp, base)  # the clip without the hook pill: kept so hook edits take seconds
    with open(base + ".sig", "w") as f:
        f.write(base_signature(clip, preset_name, settings))
    split = settings.get("layout") == "stream" and settings.get("split", True) and cam_box(clip.get("crop"))
    hook_over(base, hook_png, out_mp4, dur, cancel=cancel, y="860-h/2" if split else None)
    return {"loudness_in": loud, "ass": ass_path}


def base_path(out_mp4):
    return out_mp4[:-4] + ".base.mp4" if out_mp4.endswith(".mp4") else out_mp4 + ".base.mp4"


def base_signature(clip, preset_name, settings):
    return signature(dict(clip, context_note=None), preset_name, settings)


def hook_over(base, hook_png, out_mp4, dur, cancel=None, y=None):
    """Lay the hook pill over the finished base clip (video re-encoded fast, audio copied). No pill = plain copy."""
    tmp = f"{out_mp4}.{os.getpid()}.part.mp4"
    if not hook_png:
        import shutil
        shutil.copyfile(base, tmp)
    else:
        media.ffmpeg(["-hwaccel", "videotoolbox", "-i", base, "-loop", "1", "-t", f"{dur + 0.5:.3f}", "-i", hook_png, "-filter_complex",
                      f"[1:v]format=rgba,scale='min(880,iw)':-1,fade=t=in:st=0:d=0.18:alpha=1[hk];"
                      f"[0:v][hk]overlay=x=(W-w)/2:y={int(0.15 * OUT_H) if y is None else y}:shortest=1,format=yuv420p[v]",
                      "-map", "[v]", "-map", "0:a?", "-c:a", "copy",
                      # hardware encoder for the overlay pass: ~8x faster; high bitrate so the second generation is clean
                      "-c:v", "h264_videotoolbox", "-profile:v", "high", "-b:v", "16M", "-maxrate", "20M", "-bufsize", "40M",
                      "-allow_sw", "1", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                      "-movflags", "+faststart", "-map_metadata", "0", "-t", f"{dur:.3f}", tmp], cancel=cancel)
    os.replace(tmp, out_mp4)


def rehook(clip, out_mp4, work, preset_name, settings):
    """Hook-only edit: reuse the kept base clip. Returns True if done, False if a full render is needed."""
    base = base_path(out_mp4)
    try:
        with open(base + ".sig") as f:
            ok = f.read().strip() == base_signature(clip, preset_name, settings)
    except FileNotFoundError:
        ok = False
    if not ok or not os.path.exists(base):
        return False
    note = (clip.get("context_note") or "").strip() or None
    png = pill_png(note, work, clip["id"]) if note else None
    split = settings.get("layout") == "stream" and settings.get("split", True) and cam_box(clip.get("crop"))
    hook_over(base, png, out_mp4, float(clip["end_s"]) - float(clip["start_s"]), y="860-h/2" if split else None)
    return True


def thumbnail(mp4, t_rel, out_jpg, cancel=None):
    tmp = f"{out_jpg}.{os.getpid()}.part.jpg"
    media.ffmpeg(["-ss", f"{max(0.0, t_rel):.3f}", "-i", mp4, "-frames:v", "1", "-vf", "scale=720:1280", "-q:v", "3", tmp],
                 cancel=cancel)
    os.replace(tmp, out_jpg)
    return out_jpg
