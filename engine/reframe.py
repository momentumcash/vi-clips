"""Reframe 16:9 → 9:16: face track (≈3 fps), active-speaker heuristic, median smoothing, dead zone, capped pan speed,
hard cut on a sustained speaker switch; saliency (motion+edges) or centre when there are no faces.

Face detector, in order of preference — all on-device, nothing is downloaded:
  1. Apple Vision (VNDetectFaceRectanglesRequest) through a tiny Swift helper compiled once into data/bin/.
  2. scikit-image's bundled LBP frontal-face cascade (cv2 5.0 in this venv ships without cascade classifiers or DNN
     face models, so OpenCV alone cannot detect faces here).
The detector used is recorded on the clip (`crop.detector`).
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess

import numpy as np

import media
from config import ROOT, ENGINE, DATA

SWIFT_SRC = os.path.join(ENGINE, "bin", "facedetect.swift")
SAMPLE_FPS = 3.0
SAMPLE_W = 480


# ---------------------------------------------------------------------------------------------- detectors
def vision_bin():
    if not os.path.exists(SWIFT_SRC):
        return None
    h = hashlib.sha1(open(SWIFT_SRC, "rb").read()).hexdigest()[:10]
    out = os.path.join(DATA, "bin", f"facedetect-{h}")  # shared compiled-helper cache
    if os.path.exists(out):
        return out
    fail = out + ".failed"
    if os.path.exists(fail):
        return None
    swiftc = shutil.which("swiftc")
    if not swiftc:
        open(fail, "w").write("swiftc not found")
        return None
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        r = subprocess.run([swiftc, "-O", SWIFT_SRC, "-o", out + ".tmp"], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not os.path.exists(out + ".tmp"):
        open(fail, "w").write(r.stderr[-2000:])
        return None
    os.replace(out + ".tmp", out)
    return out


_LBP = None


def _lbp():
    global _LBP
    if _LBP is None:
        import skimage
        from skimage.feature import Cascade
        _LBP = Cascade(os.path.join(os.path.dirname(skimage.__file__), "data", "lbpcascade_frontalface_opencv.xml"))
    return _LBP


def detect_faces(paths, cancel=None):
    """→ ({path: [[x, y, w, h, conf], …] normalized, top-left origin}, detector_name)."""
    if not paths:
        return {}, "none"
    b = vision_bin()
    if b:
        out = {}
        for i in range(0, len(paths), 150):
            batch = paths[i:i + 150]
            lines = []
            rc, err = media.run([b] + batch, cancel=cancel, stdout_lines=lines, nice=True)
            if rc != 0:
                break
            for line in lines:
                try:
                    j = json.loads(line)
                    out[j["file"]] = [f for f in j["faces"] if f[4] >= 0.5]
                except (ValueError, KeyError):
                    pass
        else:
            return out, "apple_vision"
    # fallback: skimage LBP cascade
    from PIL import Image
    det = _lbp()
    out = {}
    for p in paths:
        if cancel is not None and cancel.is_set():
            raise media.Cancelled()
        im = np.asarray(Image.open(p).convert("L"), dtype=np.float64) / 255.0
        h, w = im.shape
        res = det.detect_multi_scale(img=im, scale_factor=1.2, step_ratio=1, min_size=(24, 24), max_size=(h // 2, h // 2))
        out[p] = [[r["c"] / w, r["r"] / h, r["width"] / w, r["height"] / h, 1.0] for r in res]
    return out, "lbp_cascade"


# ---------------------------------------------------------------------------------------------- sampling
def sample_frames(src, pr, a, b, out_dir, fps=SAMPLE_FPS, width=SAMPLE_W, cancel=None):
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "s_*.jpg")):
        os.remove(f)
    chain = f"fps={fps},scale={width}:-2"
    sdr = media.sdr_filter(pr)
    if sdr:
        chain += "," + sdr
    media.ffmpeg(["-hwaccel", "videotoolbox", "-ss", f"{max(0, a):.3f}", "-t", f"{b - a:.3f}", "-i", src, "-map", "0:v:0", "-vf", chain, "-q:v", "4",
                  os.path.join(out_dir, "s_%05d.jpg")], cancel=cancel)
    files = sorted(glob.glob(os.path.join(out_dir, "s_*.jpg")))
    return [((i + 0.5) / fps, p) for i, p in enumerate(files)]


# ---------------------------------------------------------------------------------------------- geometry
def crop_frac(pr):
    """Crop width as a fraction of source width for a full-height 9:16 window; None if the source is ≈9:16 or taller."""
    W, H = pr["width"], pr["height"]
    if not W or not H:
        return None
    if W / H <= 9 / 16 + 0.02:
        return None
    return (H * 9 / 16) / W


def clamp_x(x, cwf):
    return float(min(1 - cwf / 2, max(cwf / 2, x)))


def _median(xs, k):
    if len(xs) < 3:
        return list(xs)
    h = k // 2
    a = np.array(xs, dtype=float)
    return [float(np.median(a[max(0, i - h):i + h + 1])) for i in range(len(a))]


def smooth_path(times, targets, cwf, dead=0.12, vmax=0.6, cut=1.0, cut_hold=3, med_k=5, resets=None):
    """targets in source-width units (None = unknown). dead/vmax/cut are in crop-width units (vmax per second).
    resets[i] = True at a scene cut / subject re-appearing: jump there instead of panning (an editor would cut).
    Returns [[t, x], …] keyframes (before simplification)."""
    xs = list(targets)
    resets = list(resets or [False] * len(xs))
    first = next((x for x in xs if x is not None), None)
    if first is None:
        return [[0.0, 0.5]]
    last = first
    for i, x in enumerate(xs):  # hold last known
        if x is None:
            xs[i] = last
        else:
            last = x
    seg_starts = [0] + [i for i in range(1, len(xs)) if resets[i]] + [len(xs)]
    sm = []
    for a, b in zip(seg_starts, seg_starts[1:]):
        sm += _median(xs[a:b], med_k)
    xs = sm
    dz, vm, cutd = dead * cwf, vmax * cwf, cut * cwf
    cur = clamp_x(xs[0], cwf)
    path = [[times[0], cur]]
    far = 0
    for i in range(1, len(xs)):
        dt = times[i] - times[i - 1]
        tgt = clamp_x(xs[i], cwf)
        d = tgt - cur
        if resets[i] and abs(d) > dz:
            path.append([times[i] - 0.001, cur])
            cur = tgt
            far = 0
            path.append([times[i], cur])
            continue
        if abs(d) > cutd:
            far += 1
            if far >= cut_hold:  # sustained big move (speaker switch / new subject) → hard cut, like an editor would
                path.append([times[i] - 0.001, cur])
                cur = tgt
                far = 0
            path.append([times[i], cur])
            continue
        far = 0
        if abs(d) > dz:
            step = min(abs(d) - dz * 0.5, vm * dt)
            cur = clamp_x(cur + np.sign(d) * step, cwf)
        path.append([times[i], cur])
    return path


def simplify(path, eps=0.004, max_pts=60):
    def rdp(pts, e):
        if len(pts) < 3:
            return pts
        a, b = np.array(pts[0]), np.array(pts[-1])
        best, idx = -1.0, 0
        for i in range(1, len(pts) - 1):
            t = pts[i][0]
            # vertical distance to the chord at the same t (time-parametrised)
            x_on = a[1] + (b[1] - a[1]) * ((t - a[0]) / (b[0] - a[0] if b[0] != a[0] else 1))
            d = abs(pts[i][1] - x_on)
            if d > best:
                best, idx = d, i
        if best > e:
            return rdp(pts[:idx + 1], e)[:-1] + rdp(pts[idx:], e)
        return [pts[0], pts[-1]]
    out = rdp(path, eps)
    while len(out) > max_pts:
        eps *= 1.6
        out = rdp(path, eps)
    return [[round(t, 3), round(x, 4)] for t, x in out]


def crop_x_at(track, t):
    """Python twin of the ffmpeg expression (centre x in source-width units)."""
    if not track:
        return 0.5
    if t <= track[0][0]:
        return track[0][1]
    for (t0, x0), (t1, x1) in zip(track, track[1:]):
        if t < t1:
            return x0 + (x1 - x0) * (t - t0) / max(1e-6, t1 - t0)
    return track[-1][1]


def crop_expr(track, x_const=0.5):
    """ffmpeg crop `x` expression (pixels), clamped to [0, iw-ow]. Uses the trimmed input's `t` (starts at 0)."""
    if not track:
        inner = f"{x_const:.4f}"
    else:
        inner = f"{track[-1][1]:.4f}"
        for (t0, x0), (t1, x1) in reversed(list(zip(track, track[1:]))):
            slope = (x1 - x0) / max(1e-6, t1 - t0)
            inner = f"if(lt(t,{t1:.3f}),{x0:.4f}+({slope:.5f})*(t-{t0:.3f}),{inner})"
        inner = f"if(lt(t,{track[0][0]:.3f}),{track[0][1]:.4f},{inner})"
    return f"max(0,min(iw-ow,({inner})*iw-ow/2))"


# ---------------------------------------------------------------------------------------------- analysis
def _gray(p):
    from PIL import Image
    return np.asarray(Image.open(p).convert("L"), dtype=np.float32)


def _resets(times, has_face, cuts_rel):
    out = [False] * len(times)
    for i in range(1, len(times)):
        if has_face is not None and has_face[i] and not has_face[i - 1]:
            out[i] = True
        if any(times[i - 1] < c <= times[i] + 1e-3 for c in cuts_rel):
            out[i] = True
    return out


def analyze(src, pr, a, b, work, cancel=None, progress=None, scene_cuts=None):
    cwf = crop_frac(pr)
    if cwf is None:
        return {"mode": "auto", "kind": "vertical", "x": 0.5, "track": [], "detector": None, "face_ratio": None,
                "note": "source is already vertical — scaled, not cropped"}
    samples = sample_frames(src, pr, a, b, work, cancel=cancel)
    if progress:
        progress(0.3)
    paths = [p for _, p in samples]
    faces, detector = detect_faces(paths, cancel=cancel)
    if progress:
        progress(0.6)
    times = [t for t, _ in samples]
    n_face = sum(1 for p in paths if faces.get(p))
    face_ratio = n_face / max(1, len(paths))
    grays = {}

    def g(p):
        if p not in grays:
            grays[p] = _gray(p)
        return grays[p]

    targets = []
    if face_ratio >= 0.25:
        kind = "face"
        prev_x = None
        cur_pick, challenger, ch_count = None, None, 0
        prev_p = None
        for t, p in samples:
            fs = faces.get(p) or []
            if not fs:
                targets.append(None)
                prev_p = p
                continue
            # group shot: all faces fit in the window → frame the group
            xs = [f[0] + f[2] / 2 for f in fs]
            if len(fs) > 1 and (max(f[0] + f[2] for f in fs) - min(f[0] for f in fs)) < cwf * 0.85:
                x = (min(f[0] for f in fs) + max(f[0] + f[2] for f in fs)) / 2
                targets.append(x)
                prev_x = x
                prev_p = p
                continue
            # active speaker: size + mouth-region motion vs previous sample + continuity
            areas = np.array([f[2] * f[3] for f in fs])
            act = np.zeros(len(fs))
            if prev_p is not None:
                A, B = g(p), g(prev_p)
                H, W = A.shape
                for k, f in enumerate(fs):
                    x0, y0 = int(f[0] * W), int((f[1] + f[3] * 0.6) * H)
                    x1, y1 = int((f[0] + f[2]) * W), int((f[1] + f[3]) * H)
                    if x1 - x0 > 4 and y1 - y0 > 4 and A.shape == B.shape:
                        act[k] = float(np.mean(np.abs(A[y0:y1, x0:x1] - B[y0:y1, x0:x1])))
            an = areas / (areas.max() + 1e-9)
            mn = act / (act.max() + 1e-9) if act.max() > 0 else act
            prox = np.array([1 - min(1, abs(x - prev_x) / 0.5) for x in xs]) if prev_x is not None else np.zeros(len(fs))
            score = 0.45 * an + 0.35 * mn + 0.2 * prox
            best = int(np.argmax(score))
            bx = xs[best]
            # hysteresis: only switch subject when a different face wins 3 samples (~1 s) running
            if cur_pick is None or abs(bx - cur_pick) < cwf * 0.5:
                cur_pick, ch_count = bx, 0
            else:
                if challenger is not None and abs(bx - challenger) < cwf * 0.5:
                    ch_count += 1
                else:
                    challenger, ch_count = bx, 1
                if ch_count >= 3:
                    cur_pick, ch_count = bx, 0
                else:
                    near = min(xs, key=lambda x: abs(x - cur_pick))
                    if abs(near - cur_pick) < cwf * 0.5:
                        cur_pick = near
            targets.append(cur_pick)
            prev_x = cur_pick
            prev_p = p
        cuts_rel = [c - a for c in (scene_cuts or []) if a < c < b]
        path = smooth_path(times, targets, cwf, resets=_resets(times, [bool(faces.get(p)) for p in paths], cuts_rel))
    else:
        kind = "saliency"
        prev = None
        conf = []
        for t, p in samples:
            A = g(p)
            ex = np.abs(np.diff(A, axis=1)).sum(axis=0)
            ex = np.concatenate([ex, ex[-1:]])
            col = 0.5 * ex / (ex.sum() + 1e-9)
            if prev is not None and prev.shape == A.shape:
                mo = np.abs(A - prev).sum(axis=0)
                col = col + mo / (mo.sum() + 1e-9)
            prev = A
            W = len(col)
            xs_ = (np.arange(W) + 0.5) / W
            cx = float((col * xs_).sum() / (col.sum() + 1e-9))
            # peakiness: how concentrated the energy is (uniform → ~0)
            k = max(1, int(W * cwf))
            win = np.convolve(col, np.ones(k), mode="valid")
            peak = float(win.max() / (col.sum() + 1e-9))
            conf.append(peak)
            targets.append(cx if peak > cwf * 1.35 else 0.5)
        if np.mean(conf) < cwf * 1.35:
            kind = "center"
            targets = [0.5] * len(targets)
        cuts_rel = [c - a for c in (scene_cuts or []) if a < c < b]
        path = smooth_path(times, targets, cwf, dead=0.2, vmax=0.35, med_k=9, resets=_resets(times, None, cuts_rel))
    track = simplify(path)
    if progress:
        progress(1.0)
    xs_all = [x for _, x in track]
    try:
        hits, text_spans = text_cut_scan(samples, track, cwf)
        text_cut = hits[:6]
    except Exception:  # noqa: BLE001 - a heuristic must never break a render
        text_cut, text_spans = [], []
    # typical position + size of the main face (largest per sample): the stream layout's close-up is built from it
    big = [max(fs, key=lambda f: f[2] * f[3]) for fs in (faces.get(p) or [] for _, p in samples) if fs]
    face_box = ([round(float(np.median([f[0] + f[2] / 2 for f in big])), 4), round(float(np.median([f[1] + f[3] / 2 for f in big])), 4),
                 round(float(np.median([f[3] for f in big])), 4)] if big else None)
    # streamer face cam: a face in a side third of the frame, seen in most samples (game characters move around the middle)
    side = [[f for f in fs if (f[0] + f[2] / 2) < 0.3 or (f[0] + f[2] / 2) > 0.7] for fs in (faces.get(p) or [] for _, p in samples)]
    side = [max(fs, key=lambda f: f[2] * f[3]) for fs in side if fs]
    cam_box = ([round(float(np.median([f[0] + f[2] / 2 for f in side])), 4), round(float(np.median([f[1] + f[3] / 2 for f in side])), 4),
                round(float(np.median([f[3] for f in side])), 4)] if len(side) >= max(2, 0.4 * len(samples)) else None)
    return {"mode": "auto", "kind": kind, "x": round(float(np.median(xs_all)), 4), "track": track, "detector": detector, "cam_box": cam_box,
            "face_ratio": round(face_ratio, 3), "crop_w": round(cwf, 4), "samples": len(samples), "text_cut": text_cut,
            "text_spans": text_spans, "face_box": face_box}


def open_frames(src, pr, t, work, n=5, span=1.0, cancel=None):
    """The first `span` seconds from t sampled at n frames → per frame: faces, sharpness, luma, and the burned-in text
    bands a face-centred (or centred) 9:16 window would slice. Used for the measured open_frame score (minimum)."""
    os.makedirs(work, exist_ok=True)
    tag = f"of_{int(t * 1000)}"
    for f in glob.glob(os.path.join(work, tag + "_*.jpg")):
        os.remove(f)
    chain = f"fps={n / span:.3f},scale={SAMPLE_W}:-2"
    sdr = media.sdr_filter(pr)
    if sdr:
        chain += "," + sdr
    media.ffmpeg(["-hwaccel", "videotoolbox", "-ss", f"{max(0.0, t):.3f}", "-t", f"{span:.3f}", "-i", src, "-map", "0:v:0",
                  "-vf", chain, "-frames:v", str(n), "-q:v", "3", os.path.join(work, tag + "_%02d.jpg")], cancel=cancel)
    paths = sorted(glob.glob(os.path.join(work, tag + "_*.jpg")))
    if not paths:
        return []
    faces, det = detect_faces(paths, cancel=cancel)
    cwf = crop_frac(pr)
    out = []
    for k, p in enumerate(paths):
        A = _gray(p)
        lap = A[1:-1, 1:-1] * -4 + A[:-2, 1:-1] + A[2:, 1:-1] + A[1:-1, :-2] + A[1:-1, 2:]
        fs = faces.get(p, [])
        xc = (max(fs, key=lambda f: f[2] * f[3])[0] + max(fs, key=lambda f: f[2] * f[3])[2] / 2) if fs else 0.5
        cut = frame_text_cut(A, clamp_x(xc, cwf), cwf) if cwf else []
        out.append({"t": round(t + k * span / n, 3), "faces": fs, "detector": det, "sharpness": float(lap.var()),
                    "luma": float(A.mean()), "text_cut": bool(cut), "frame": p})
    return out


def faces_at(src, pr, t, work, cancel=None):
    """Face boxes + sharpness + brightness for one frame at t (used for the measured open_frame score)."""
    os.makedirs(work, exist_ok=True)
    p = os.path.join(work, f"open_{int(t * 1000)}.jpg")
    media.grab_frame(src, t, p, pr, width=SAMPLE_W, cancel=cancel)
    faces, det = detect_faces([p], cancel=cancel)
    A = _gray(p)
    lap = A[1:-1, 1:-1] * -4 + A[:-2, 1:-1] + A[2:, 1:-1] + A[1:-1, :-2] + A[1:-1, 2:]
    return {"faces": faces.get(p, []), "detector": det, "sharpness": float(lap.var()), "luma": float(A.mean()), "frame": p}


# ---------------------------------------------------------------------------------------------- burned-in text check
def _text_bands(A, min_frac=0.2):
    """Text-like horizontal bands in a grayscale frame: rows with dense, high-contrast vertical strokes that are
    clearly denser than the rows just above and below (overlay titles/lower thirds), returned as (y0, y1, x0, x1) px."""
    H, W = A.shape
    strong = (np.abs(np.diff(A, axis=1)) > 60).astype(np.float32)
    h = max(6, int(H * 0.06))
    step = max(3, h // 2)
    dens = []
    for y in range(int(H * 0.1), int(H * 0.95) - h, step):
        dens.append((y, strong[y:y + h].mean()))
    if len(dens) < 5:
        return []
    med = float(np.median([d for _, d in dens])) + 1e-4
    out = []
    for k, (y, d) in enumerate(dens):
        # compare with rows a full band-and-a-half away (titles are usually taller than one band)
        above = dens[k - 3][1] if k >= 3 else med
        below = dens[k + 3][1] if k + 3 < len(dens) else med
        if d < 2.5 * med or d < 2.0 * max(above, below) or d < 0.04:
            continue
        col = np.convolve(strong[y:y + h].mean(axis=0), np.ones(9) / 9, mode="same")
        on = col > 0.10
        best, cur, start, bstart = 0, 0, 0, 0
        gap = 0
        for x, v in enumerate(on):
            if v:
                if cur == 0:
                    start = x
                cur += 1 + gap
                gap = 0
                if cur > best:
                    best, bstart = cur, start
            elif cur:
                gap += 1
                if gap > 8:
                    cur, gap = 0, 0
        if best >= min_frac * W:
            out.append((y, y + h, bstart, bstart + best))
    return out


def frame_text_cut(A, xc, cwf):
    """Text bands in grayscale frame A that a 9:16 window centred at xc would slice → [(y0_frac, y1_frac)]."""
    H, W = A.shape
    x0, x1 = (xc - cwf / 2) * W, (xc + cwf / 2) * W
    margin = 0.03 * W
    out = []
    for (y0, y1, a, b) in _text_bands(A):
        inside = min(b, x1) - max(a, x0) > margin
        crosses = (a < x0 - margin < b) or (a < x1 + margin < b)
        if inside and crosses:
            out.append((y0 / H, y1 / H))
    return out


def text_cut_scan(samples, track, cwf, x_const=0.5):
    """Per sample: the text bands the crop would slice (burned-in titles like "ROOM 3 / LYNYRD SKYNYRD").
    Heuristic: no OCR. Returns (hit_times, spans) where spans = [{start, end, band: [y0, y1]}] in clip seconds /
    source-height fractions; neighbouring hits merge, each span is padded by one sample."""
    hits = []
    for t, p in samples:
        xc = crop_x_at(track, t) if track else x_const
        bands = frame_text_cut(_gray(p), xc, cwf)
        if bands:
            hits.append((t, min(b[0] for b in bands), max(b[1] for b in bands)))
    dt = (samples[1][0] - samples[0][0]) if len(samples) > 1 else 1 / SAMPLE_FPS
    spans = []
    for t, y0, y1 in hits:
        if spans and t - spans[-1]["end"] <= 1.1 * dt:
            sp = spans[-1]
            sp["end"] = t + dt
            sp["band"] = [min(sp["band"][0], y0), max(sp["band"][1], y1)]
        else:
            # pad a full sample each side (titles fade in/out between samples); a span that starts within the first
            # 0.6 s takes the whole opening so the hook never shows a sliced title
            st = max(0.0, t - dt)
            spans.append({"start": 0.0 if st < 0.6 else st, "end": t + dt, "band": [y0, y1]})
    for sp in spans:
        sp["start"], sp["end"] = round(sp["start"], 3), round(sp["end"], 3)
        sp["band"] = [round(sp["band"][0], 3), round(sp["band"][1], 3)]
    return [round(t, 1) for t, _, _ in hits], spans


def text_cut_times(samples, track, cwf, x_const=0.5, limit=3):
    return text_cut_scan(samples, track, cwf, x_const)[0][:limit]
