"""Transparent scoring. overall = round(10 × Σ wᵢ·scoreᵢ) with the contract weights.

Model-judged keys come from the LLM. Measured inputs blend in and `score_basis` says so:
  pacing     ← words/s + dead-air ratio inside the clip           (blend 50/50 with the model)
  clarity    ← whisper avg logprob / no-speech prob + audio level  (blend 50/50 with the model)
  open_frame ← face present + sharpness (Laplacian var) + not black at best_open_s   (measured only)
"""
from config import WEIGHTS, SCORE_KEYS


def _lin(v, a, b):
    """0 at a, 10 at b (clamped); works for a > b too."""
    if a == b:
        return 10.0
    return max(0.0, min(10.0, 10.0 * (v - a) / (b - a)))


def pacing_measured(words, a, b):
    ws = [w for w in words if w["start"] >= a - 0.01 and w["end"] <= b + 0.01]
    dur = max(0.1, b - a)
    if not ws:
        return 0.0, {"wps": 0.0, "dead_air": 1.0}
    wps = len(ws) / dur
    dead = 0.0
    prev = a
    for w in ws:
        g = w["start"] - prev
        if g > 0.7:
            dead += g - 0.7
        prev = w["end"]
    if b - prev > 1.0:
        dead += (b - prev) - 1.0
    ratio = dead / dur
    # 2.2–3.6 words/s is brisk spoken English; below 1.0 drags
    s_wps = _lin(wps, 0.8, 2.2) if wps < 2.2 else (10.0 if wps <= 3.6 else _lin(wps, 5.5, 3.6))
    s_dead = _lin(ratio, 0.35, 0.0)
    return round(0.5 * s_wps + 0.5 * s_dead, 2), {"wps": round(wps, 2), "dead_air": round(ratio, 3)}


def clarity_measured(segments, level_db, a, b):
    segs = [s for s in segments if s["end"] > a and s["start"] < b]
    if not segs:
        return 0.0, {"avg_logprob": None, "no_speech": None, "level_db": round(level_db, 1)}
    tot = sum(min(s["end"], b) - max(s["start"], a) for s in segs) or 1.0
    lp = sum(s["avg_logprob"] * (min(s["end"], b) - max(s["start"], a)) for s in segs) / tot
    ns = sum(s["no_speech_prob"] * (min(s["end"], b) - max(s["start"], a)) for s in segs) / tot
    s_lp = _lin(lp, -1.0, -0.15)
    s_ns = _lin(ns, 0.6, 0.05)
    s_lv = _lin(level_db, -45, -28) if level_db < -28 else (10.0 if level_db <= -8 else _lin(level_db, 0, -8))
    return round(0.5 * s_lp + 0.2 * s_ns + 0.3 * s_lv, 2), {"avg_logprob": round(lp, 3), "no_speech": round(ns, 3), "level_db": round(level_db, 1)}


def open_frame_measured(info):
    """info from reframe.faces_at: faces, sharpness (Laplacian variance on a 480px frame), luma (0-255)."""
    if info is None:
        return None, {}
    luma = info["luma"]
    if luma < 16:
        return 0.0, {"face": False, "sharpness": round(info["sharpness"], 1), "luma": round(luma, 1), "black": True}
    face = bool(info["faces"])
    sharp = max(0.0, min(1.0, (info["sharpness"] - 30) / 270))
    bright = 1.0 if 40 <= luma <= 215 else 0.5
    s = 4.0 * face + 4.0 * sharp + 2.0 * bright
    return round(s, 2), {"face": face, "sharpness": round(info["sharpness"], 1), "luma": round(luma, 1), "black": False}


def open_frame_min(infos):
    """Minimum open-frame score over the first second (the viewer sees all of it, not just frame 0). A frame where a
    9:16 crop would slice burned-in text is capped at 4."""
    if not infos:
        return None, {}
    worst, wd = None, {}
    for inf in infos:
        s, d = open_frame_measured(inf)
        if inf.get("text_cut"):
            s = min(s, 4.0)
            d = dict(d, text_cut=True)
        d["t"] = inf.get("t")
        if worst is None or s < worst:
            worst, wd = s, d
    wd["frames"] = len(infos)
    return round(worst, 2), wd


def blend(model_scores, model_reasons, measured):
    """measured: {key: (score, detail_dict, reason_str)} for pacing/clarity/open_frame (score may be None)."""
    scores, reasons, basis = {}, {}, {}
    for k in SCORE_KEYS:
        m = model_scores.get(k)
        m = float(m) if isinstance(m, (int, float)) else None
        r = (model_reasons or {}).get(k) or ""
        meas = measured.get(k)
        if meas and meas[0] is not None:
            if k == "open_frame" or m is None:
                scores[k] = round(meas[0], 1)
                basis[k] = "measured"
                reasons[k] = meas[2]
            else:
                scores[k] = round(0.5 * m + 0.5 * meas[0], 1)
                basis[k] = "blend"
                reasons[k] = (r + " " if r else "") + "Measured: " + meas[2]
        else:
            scores[k] = round(m if m is not None else 5.0, 1)
            basis[k] = "model"
            reasons[k] = r
    return scores, reasons, basis


def overall(scores):
    return round(10 * sum(WEIGHTS[k] * float(scores.get(k, 0)) for k in WEIGHTS))
