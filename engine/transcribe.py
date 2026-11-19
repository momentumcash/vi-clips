"""Transcription stage: faster-whisper word timestamps in a separate, niced, killable, resumable process."""
import os
import sys

import media

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workers", "whisper_worker.py")


def build_prompt(creator, source):
    """Bias whisper toward the names it otherwise mishears: creator name, source title, per-creator vocab list."""
    import hashlib
    import json as _json
    vocab = [str(v).strip() for v in ((creator or {}).get("vocab") or []) if str(v).strip()]
    title = ((source or {}).get("title") or "").strip()
    name = ((creator or {}).get("name") or "").strip()
    parts = [p for p in (title, name) if p]
    initial = ". ".join(parts) + ("." if parts else "") + (" " + ", ".join(vocab) + "." if vocab else "")
    hot = ", ".join(dict.fromkeys([name] + vocab)) if (name or vocab) else ""
    p = {"initial_prompt": initial.strip()[:400] or None, "hotwords": hot[:300] or None}
    p["sig"] = hashlib.sha1(_json.dumps(p, sort_keys=True).encode()).hexdigest()[:12]
    return p


def transcribe(wav, out_dir, model, duration, cancel=None, progress=None, prompt=None, status=None):
    lines = []
    args = [sys.executable, WORKER, wav, out_dir, model, f"{duration:.3f}"]
    if prompt:
        pp = os.path.join(out_dir, "transcript.prompt.json")
        media.write_json_atomic(pp, prompt)
        args.append(pp)
    rc, err = media.run(args, cancel=cancel, progress=progress, stdout_lines=lines, status=status)
    if rc != 0:
        tail = "\n".join(err.strip().splitlines()[-5:])
        raise media.MediaError(f"whisper worker failed ({rc}): {tail}")
    tr = media.read_json(os.path.join(out_dir, "transcript.json"))
    if not tr:
        raise media.MediaError("whisper worker finished without writing transcript.json")
    resumed = next((l for l in lines if l.startswith("RESUME")), None)
    return tr, resumed


def phrases(words, gap=0.6, max_words=16):
    """Gap-split phrases: a new phrase starts after a pause > gap s, after sentence punctuation once a phrase has
    ≥ 4 words, or at max_words. Each phrase: {start, end, text, i0, i1} (word index range, inclusive)."""
    out = []
    cur = []
    for i, w in enumerate(words):
        if cur:
            prev = words[i - 1]
            brk = (w["start"] - prev["end"] > gap or len(cur) >= max_words
                   or (prev["word"][-1:] in ".?!" and len(cur) >= 4))
            if brk:
                out.append(cur)
                cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    res = []
    for idx in out:
        ws = [words[i] for i in idx]
        res.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": " ".join(w["word"] for w in ws), "i0": idx[0], "i1": idx[-1]})
    return res


def fmt_ts(t):
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:04.1f}"


def parse_ts(s):
    s = str(s).strip().strip("[]")
    if ":" in s:
        parts = s.split(":")
        v = 0.0
        for p in parts:
            v = v * 60 + float(p)
        return v
    return float(s)
