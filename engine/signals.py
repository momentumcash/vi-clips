"""Measured signals: loudness curve, loud bursts, pauses, laughter-like bursts, scene cuts, filmstrip frames.

Laughter here is a HEURISTIC, not a classifier: a burst of high energy with high spectral flatness (broadband, noisy —
laughter/cheering/crowd is flatter than voiced speech), 0.3–4 s long, optionally backed by a transcript cue nearby.
It is labelled "laughter-like burst" everywhere.
"""
import os
import re
import wave

import numpy as np

import media

HOP = 0.05          # seconds per analysis frame
SR = 16000
N_HOP = int(SR * HOP)
NFFT = 1024
CUES = re.compile(r"\b(ha(ha)+|hah|lol|lmao|haha|laugh\w*|no way|oh my god|omg|what the|dude|bro|yo+|wow|crazy|insane|stop)\b", re.I)
VERSION = 2


def _frames_from_wav(path, cancel=None, progress=None):
    """Stream the WAV in 60 s chunks → per-frame RMS dB and spectral flatness (300–6000 Hz)."""
    db_list, flat_list = [], []
    with wave.open(path, "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise media.MediaError("signals expect 16 kHz mono 16-bit WAV")
        total = w.getnframes()
        chunk = N_HOP * 1200  # 60 s
        win = np.hanning(NFFT).astype(np.float32)
        freqs = np.fft.rfftfreq(NFFT, 1.0 / SR)
        band = (freqs >= 300) & (freqs <= 6000)
        read = 0
        carry = np.zeros(0, dtype=np.float32)
        while True:
            raw = w.readframes(chunk)
            if not raw:
                break
            x = np.concatenate([carry, np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0])
            n = len(x) // N_HOP
            carry = x[n * N_HOP:]
            fr = x[:n * N_HOP].reshape(n, N_HOP)
            rms = np.sqrt(np.mean(fr * fr, axis=1) + 1e-12)
            db_list.append(20 * np.log10(rms + 1e-9))
            pad = np.zeros((n, NFFT), dtype=np.float32)
            pad[:, :N_HOP] = fr
            spec = np.abs(np.fft.rfft(pad * win, axis=1)) ** 2
            sb = spec[:, band] + 1e-10
            flat = np.exp(np.mean(np.log(sb), axis=1)) / np.mean(sb, axis=1)
            flat_list.append(flat)
            read += chunk
            if progress and total:
                progress(min(1.0, read / total))
            if cancel is not None and cancel.is_set():
                raise media.Cancelled()
    if not db_list:
        return np.zeros(0), np.zeros(0)
    return np.concatenate(db_list), np.concatenate(flat_list)


def _runs(mask, min_len, max_len=None, merge_gap=2):
    """Contiguous True runs (frame indices) with small gaps merged."""
    runs = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if runs and i - runs[-1][1] <= merge_gap:
                runs[-1][1] = j
            else:
                runs.append([i, j])
            i = j
        else:
            i += 1
    return [(a, b) for a, b in runs if b - a >= min_len and (max_len is None or b - a <= max_len)]


def _local_median(db, win_frames):
    """Median dB in ~30 s blocks, interpolated to frame rate (robust local reference level)."""
    n = len(db)
    if n == 0:
        return db
    centers, meds = [], []
    for a in range(0, n, win_frames):
        seg = db[a:a + win_frames]
        voiced = seg[seg > -55]
        meds.append(float(np.median(voiced)) if len(voiced) > 10 else float(np.median(seg)))
        centers.append(a + len(seg) / 2)
    return np.interp(np.arange(n), centers, meds)


def scene_and_strip(src, pr, out_strip_dir, cancel=None, progress=None, n_frames=120):
    """One decode pass: scdet at 6 fps on a 256px copy → scene-cut times; plus ~n_frames filmstrip JPGs
    (160 px tall) named f_<ms>.jpg for the timeline."""
    dur = pr["duration"]
    os.makedirs(out_strip_dir, exist_ok=True)
    every = max(1.0, dur / n_frames)
    sdr = media.sdr_filter(pr)
    pre = "fps=6,scale=256:-2" + ("," + sdr if sdr else "")
    fc = (f"[0:v:0]{pre},split=2[s][f];[s]scdet=threshold=14[sd];"
          f"[f]fps=1/{every:.4f},scale=-2:160[fr]")
    tmp_pattern = os.path.join(out_strip_dir, "tmp_%05d.jpg")
    err = media.ffmpeg(["-hwaccel", "videotoolbox", "-i", src, "-filter_complex", fc,
                        "-map", "[sd]", "-f", "null", "-",
                        "-map", "[fr]", "-q:v", "6", "-fps_mode", "passthrough", tmp_pattern],
                       cancel=cancel, progress=progress, duration=dur)
    cuts = []
    for m in re.finditer(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)", err):
        cuts.append({"t": round(float(m.group(2)), 2), "score": round(float(m.group(1)), 1)})
    # rename tmp frames to their timestamps (frame k is at ~k*every)
    for name in sorted(os.listdir(out_strip_dir)):
        if name.startswith("tmp_"):
            k = int(name[4:9]) - 1
            t = k * every
            os.replace(os.path.join(out_strip_dir, name), os.path.join(out_strip_dir, f"f_{int(t * 1000):09d}.jpg"))
    return cuts


def compute(wav, words, duration, cancel=None, progress=None):
    db, flat = _frames_from_wav(wav, cancel=cancel, progress=progress)
    n = len(db)
    t = np.arange(n) * HOP
    out = {"version": VERSION, "hop": HOP}
    # energy curve for the UI (≤ ~4000 points), power-averaged
    step = max(1, int(np.ceil(n / 4000)))
    pw = 10 ** (db / 10)
    m = n // step
    curve = []
    if m:
        cdb = 10 * np.log10(pw[:m * step].reshape(m, step).mean(axis=1) + 1e-12)
        curve = [[round(float(i * step * HOP), 2), round(float(v), 1)] for i, v in enumerate(cdb)]
    out["curve"] = curve
    # loudness per 10 s window
    wf = int(10 / HOP)
    windows = []
    for a in range(0, n, wf):
        seg = pw[a:a + wf]
        windows.append([round(a * HOP, 1), round(float(10 * np.log10(seg.mean() + 1e-12)), 1)])
    out["windows_10s"] = windows
    med = _local_median(db, int(30 / HOP))
    sm = np.convolve(db, np.ones(6) / 6, mode="same") if n > 6 else db
    # loud bursts: ≥ 8 dB above local median for ≥ 0.3 s
    bursts = []
    for a, b in _runs(sm > med + 8, min_len=int(0.3 / HOP)):
        bursts.append({"t": round(a * HOP, 2), "dur": round((b - a) * HOP, 2), "db_over": round(float(np.max(sm[a:b] - med[a:b])), 1)})
    out["loud_bursts"] = bursts
    # laughter-like: energetic + broadband (flatness) 0.3–4 s; cue words within ±2 s raise confidence
    fl_sm = np.convolve(flat, np.ones(6) / 6, mode="same") if n > 6 else flat
    laugh = []
    cue_times = [(w["start"], w["word"]) for w in words if CUES.search(w["word"])]
    for a, b in _runs((sm > med + 5) & (fl_sm > 0.28), min_len=int(0.3 / HOP), max_len=int(4.0 / HOP)):
        ta, tb = a * HOP, b * HOP
        cue = next((c for ct, c in cue_times if ta - 2 <= ct <= tb + 2), None)
        laugh.append({"t": round(ta, 2), "dur": round(tb - ta, 2), "flatness": round(float(fl_sm[a:b].mean()), 2),
                      "cue": cue, "basis": "energy+flatness+cue" if cue else "energy+flatness"})
    for ct, c in cue_times:  # cue-only (e.g. "(laughs)" in transcript with no acoustic burst)
        if not any(l["t"] - 2 <= ct <= l["t"] + l["dur"] + 2 for l in laugh) and re.search(r"ha(ha)+|laugh|lol|lmao", c, re.I):
            laugh.append({"t": round(ct, 2), "dur": 0.0, "flatness": None, "cue": c, "basis": "cue only"})
    out["laughter_like"] = sorted(laugh, key=lambda x: x["t"])
    # pauses from the transcript (no speech ≥ 1.2 s)
    pauses = []
    prev_end = 0.0
    for w in words:
        if w["start"] - prev_end >= 1.2:
            pauses.append({"t": round(prev_end, 2), "dur": round(w["start"] - prev_end, 2)})
        prev_end = max(prev_end, w["end"])
    if duration and duration - prev_end >= 1.2 and words:
        pauses.append({"t": round(prev_end, 2), "dur": round(duration - prev_end, 2)})
    out["pauses"] = pauses
    # speech rate per 30 s
    rate = []
    for a in np.arange(0, max(duration, 1), 30):
        cnt = sum(1 for w in words if a <= w["start"] < a + 30)
        rate.append([round(float(a), 1), round(cnt / 30.0, 2)])
    out["speech_rate_30s"] = rate
    out["median_db"] = round(float(np.median(db[db > -55])) if np.any(db > -55) else -90.0, 1)
    return out, db


def clip_level_db(db_frames, a, b):
    """Mean power (dB) between a and b seconds, from the frame dB array."""
    i, j = int(a / HOP), int(b / HOP)
    seg = db_frames[i:j]
    if len(seg) == 0:
        return -90.0
    return float(10 * np.log10(np.mean(10 ** (seg / 10)) + 1e-12))


def sheet(sig, a=0.0, b=None):
    """Plain-text signal sheet for the detection prompt, limited to [a, b]."""
    from transcribe import fmt_ts
    b = b if b is not None else 1e12
    inr = lambda t: a <= t <= b  # noqa: E731
    lines = ["SIGNAL SHEET — measured by code from the audio/video (not by a model).",
             f"Typical speech level in this source: {sig.get('median_db')} dBFS (RMS).",
             "Loudness per 10 s window (dBFS RMS):"]
    ws = [f"[{fmt_ts(t)}] {v}" for t, v in sig.get("windows_10s", []) if inr(t)]
    for i in range(0, len(ws), 8):
        lines.append("  " + "  ".join(ws[i:i + 8]))
    lb = [f"[{fmt_ts(x['t'])}] +{x['db_over']} dB for {x['dur']} s" for x in sig.get("loud_bursts", []) if inr(x["t"])]
    lines.append("Loud bursts (≥ 8 dB over the local median, ≥ 0.3 s): " + ("; ".join(lb[:60]) if lb else "none"))
    lp = [f"[{fmt_ts(x['t'])}] {x['dur']} s" for x in sig.get("pauses", []) if inr(x["t"])]
    lines.append("Long pauses (no speech ≥ 1.2 s): " + ("; ".join(lp[:80]) if lp else "none"))
    ll = [f"[{fmt_ts(x['t'])}] {x['dur']} s ({x['basis']}{', cue “' + x['cue'] + '”' if x.get('cue') else ''})"
          for x in sig.get("laughter_like", []) if inr(x["t"])]
    lines.append("Laughter-like bursts (heuristic: loud + broadband noise, NOT a laughter classifier — could be laughter, "
                 "cheering, crowd or wind): " + ("; ".join(ll[:60]) if ll else "none"))
    sc = [f"[{fmt_ts(x['t'])}]" for x in sig.get("scene_cuts", []) if inr(x["t"])]
    lines.append("Scene cuts (ffmpeg scdet): " + (" ".join(sc[:150]) if sc else "none detected"))
    rt = [f"[{fmt_ts(t)}] {v}" for t, v in sig.get("speech_rate_30s", []) if inr(t)]
    lines.append("Speech rate, words/s per 30 s: " + "  ".join(rt[:100]))
    return "\n".join(lines)
