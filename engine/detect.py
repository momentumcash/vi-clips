"""Moment detection: transcript phrases + signal sheet → LLM editorial pass → verified candidates.

Every candidate's start/end must map to real phrase timestamps and its cited hook/payoff lines must exist (verbatim or
near-verbatim) inside its range. Anything else is discarded as a hallucination, with the reason recorded.
Long sources (> 25 min of transcript) are windowed (20 min, 3 min overlap) and then merged in one global pass.
"""
import difflib
import json
import math
import os
import re

import llm
import signals as sigm
from config import CATEGORIES, SCORE_KEYS, PROMPTS, TARGETS
from transcribe import phrases as make_phrases, fmt_ts, parse_ts

SYSTEM = ("You are a meticulous senior short-form video editor. You answer with a single JSON object exactly as "
          "requested — no prose, no markdown fences. You never invent transcript lines or timestamps.")
WINDOW_S, OVERLAP_S, LONG_S = 20 * 60, 3 * 60, 25 * 60


def _norm(s):
    return re.sub(r"[^a-z0-9' ]+", " ", (s or "").lower()).split()


def _cite_ok(cited, ph_in_range):
    """True if the cited line (with or without its [ts] prefix) is in the range's transcript text."""
    text = re.sub(r"^\s*\[[^\]]*\]\s*", "", cited or "")
    cw = _norm(text)
    if len(cw) == 0:
        return False
    rng = [w for p in ph_in_range for w in _norm(p["text"])]
    hay = " ".join(rng)
    if " ".join(cw) in hay:
        return True
    # fuzzy: best match over sliding windows of the same length
    n = len(cw)
    best = 0.0
    for i in range(0, max(1, len(rng) - n + 1)):
        r = difflib.SequenceMatcher(None, cw, rng[i:i + n]).ratio()
        best = max(best, r)
        if best >= 0.8:
            return True
    return False


def _phrase_at(phs, t, tol=1.5):
    best = min(phs, key=lambda p: abs(p["start"] - t)) if phs else None
    if best is None or abs(best["start"] - t) > tol:
        return None
    return best


def transcript_block(phs):
    return "\n".join(f"[{fmt_ts(p['start'])}] {p['text']}" for p in phs)


def _validate(obj):
    errs = []
    if not isinstance(obj, dict) or not isinstance(obj.get("candidates"), list):
        return ["top level must be an object with a 'candidates' list"]
    for i, c in enumerate(obj["candidates"]):
        if not isinstance(c, dict):
            errs.append(f"candidate {i} is not an object")
            continue
        for k in ("start", "end", "title", "hook_line", "payoff_line", "category", "scores", "reasons"):
            if k not in c:
                errs.append(f"candidate {i} missing '{k}'")
        try:
            a, b = parse_ts(c.get("start")), parse_ts(c.get("end"))
            if b < a:
                errs.append(f"candidate {i}: end before start")
        except (TypeError, ValueError):
            errs.append(f"candidate {i}: start/end must be 'mm:ss.s' strings copied from the transcript")
        if c.get("category") not in CATEGORIES:
            errs.append(f"candidate {i}: category must be one of {', '.join(CATEGORIES)}")
        sc = c.get("scores") or {}
        for k in SCORE_KEYS:
            v = sc.get(k)
            if not isinstance(v, (int, float)) or not 0 <= v <= 10:
                errs.append(f"candidate {i}: scores.{k} must be a number 0-10")
        rs = c.get("reasons") or {}
        if not isinstance(rs, dict):
            errs.append(f"candidate {i}: reasons must be an object")
    return errs


def build_prompt(source_desc, duration, target, max_n, phs, sig, a=0.0, b=None):
    with open(os.path.join(PROMPTS, "detect.md")) as f:
        tpl = f.read()
    lo, hi = TARGETS[target]
    tgt = "auto — choose the natural length of each moment between 15 and 90 s" if target == "auto" else f"{lo:.0f}–{hi:.0f} s"
    return (tpl.replace("{{SOURCE}}", source_desc).replace("{{DURATION}}", fmt_ts(duration) + " (mm:ss)")
            .replace("{{TARGET}}", tgt).replace("{{MAX}}", str(max_n))
            .replace("{{SIGNALS}}", sigm.sheet(sig, a, b)).replace("{{TRANSCRIPT}}", transcript_block(phs)))


def _verify(cands, phs, duration):
    ok, discarded = [], []
    for c in cands:
        try:
            ts, te = parse_ts(c["start"]), parse_ts(c["end"])
        except (TypeError, ValueError, KeyError):
            discarded.append({"reason": "unparseable start/end", "candidate": c})
            continue
        p0, p1 = _phrase_at(phs, ts), _phrase_at(phs, te)
        if not p0 or not p1:
            discarded.append({"reason": f"start/end {c['start']}–{c['end']} do not match transcript phrase timestamps", "candidate": c})
            continue
        if p1["end"] <= p0["start"] or p0["start"] > duration:
            discarded.append({"reason": "range outside the source", "candidate": c})
            continue
        in_range = [p for p in phs if p["end"] >= p0["start"] - 1.0 and p["start"] <= p1["end"] + 1.0]
        if not _cite_ok(c.get("hook_line"), in_range):
            discarded.append({"reason": f"hook_line not found in the clip's transcript: {str(c.get('hook_line'))[:120]}", "candidate": c})
            continue
        if not _cite_ok(c.get("payoff_line"), in_range):
            discarded.append({"reason": f"payoff_line not found in the clip's transcript: {str(c.get('payoff_line'))[:120]}", "candidate": c})
            continue
        try:
            bo = parse_ts(c.get("best_open") or c["start"])
            pb = _phrase_at(phs, bo)
            best_open = pb["start"] if pb and p0["start"] <= pb["start"] <= p1["end"] else p0["start"]
        except (TypeError, ValueError):
            best_open = p0["start"]
        c2 = dict(c)
        payoff_ts = _ts_or(c.get("payoff_line"))
        pp = _phrase_at(in_range, payoff_ts) if payoff_ts is not None else None
        # the clip ends on the payoff line (+ reaction tail in code), never on later material the model let run on
        raw_end = pp["end"] if pp and p0["start"] < pp["end"] <= p1["end"] + 0.01 else p1["end"]
        c2.update(raw_start=p0["start"], raw_end=raw_end, model_end=p1["end"], best_open_s=best_open,
                  hook_ts=_ts_or(c.get("hook_line")), payoff_ts=payoff_ts)
        ok.append(c2)
    return ok, discarded


def _ts_or(line):
    m = re.match(r"\s*\[([\d:.]+)\]", line or "")
    try:
        return parse_ts(m.group(1)) if m else None
    except ValueError:
        return None


def detect(provider, transcript, sig, duration, target, max_clips, source_desc, cancel=None, log=None):
    """Returns dict(candidates, discarded, passed_over, metas, windows)."""
    words = transcript["words"]
    if len(words) < 15:
        raise ValueError("too little speech to find moments (fewer than 15 words transcribed)")
    phs = make_phrases(words)
    max_n = int(min(20, max_clips + 4))
    span = words[-1]["end"]
    metas, cands, discarded, passed = [], [], [], []
    windows = []
    if span <= LONG_S:
        windows.append((0.0, duration))
    else:
        a = 0.0
        while a < span:
            windows.append((a, min(duration, a + WINDOW_S)))
            if a + WINDOW_S >= span:
                break
            a += WINDOW_S - OVERLAP_S
    for wi, (a, b) in enumerate(windows):
        wp = [p for p in phs if a <= p["start"] < b]
        if len(wp) < 3:
            continue
        if log:
            log(wi / len(windows), f"LLM pass {wi + 1}/{len(windows)} ({fmt_ts(a)}–{fmt_ts(b)})")
        n_here = max_n if len(windows) == 1 else max(3, math.ceil(max_n * 0.6))
        prompt = build_prompt(source_desc, duration, target, n_here, wp, sig, a, b)
        obj, m = llm.complete_json(provider, prompt, SYSTEM, _validate, cancel=cancel)
        metas += m
        ok, bad = _verify(obj.get("candidates") or [], wp, duration)
        for c in ok:
            c["window"] = wi
        cands += ok
        discarded += bad
        if obj.get("passed_over"):
            passed.append(str(obj["passed_over"]))
    if len(windows) > 1 and cands:
        if log:
            log(0.95, "global merge pass")
        cands, m = merge_pass(provider, cands, phs, max_n, cancel=cancel)
        metas += m
    return {"candidates": cands, "discarded": discarded, "passed_over": " ".join(passed), "metas": metas,
            "windows": [[round(a, 1), round(b, 1)] for a, b in windows], "phrases": len(phs)}


MERGE_SYSTEM = SYSTEM


def merge_pass(provider, cands, phs, max_n, cancel=None):
    """Global pass over windowed candidates: drop overlaps from window seams, keep the best, and link payoffs to setups
    from earlier in the source (sets needs_context/context_note)."""
    lines = []
    for i, c in enumerate(cands):
        excerpt = " ".join(p["text"] for p in phs if c["raw_start"] <= p["start"] <= c["raw_end"])[:500]
        lines.append(json.dumps({"id": i, "range": f"{fmt_ts(c['raw_start'])}–{fmt_ts(c['raw_end'])}", "title": c.get("title"),
                                 "category": c.get("category"), "hook_line": c.get("hook_line"), "payoff_line": c.get("payoff_line"),
                                 "scores": c.get("scores"), "excerpt": excerpt}))
    outline = "\n".join(f"[{fmt_ts(p['start'])}] {p['text']}" for p in phs[::6])[:30000]
    prompt = (
        "These candidate shorts were found in separate overlapping windows of one long source. Choose the final set.\n"
        f"- Keep at most {max_n}. Drop duplicates (same moment found twice at a window seam) — keep the better-bounded one.\n"
        "- Prefer five great clips over many okay ones.\n"
        "- If a clip's payoff depends on a setup from EARLIER in the source that is not inside the clip, set needs_context "
        "true and write a one-line on-screen context_note that gives a cold viewer that setup.\n"
        "Return ONLY JSON: {\"keep\": [{\"id\": 0, \"needs_context\": false, \"context_note\": \"\"}]} ordered best first.\n\n"
        "CANDIDATES (one JSON per line):\n" + "\n".join(lines) +
        "\n\nSOURCE OUTLINE (every 6th phrase, for spotting setups):\n" + outline)

    def val(o):
        if not isinstance(o, dict) or not isinstance(o.get("keep"), list):
            return ["need {\"keep\": [...]}"]
        bad = [k for k in o["keep"] if not isinstance(k, dict) or not isinstance(k.get("id"), int) or not 0 <= k["id"] < len(cands)]
        return [f"invalid keep entries: {bad[:3]}"] if bad else []
    obj, metas = llm.complete_json(provider, prompt, MERGE_SYSTEM, val, cancel=cancel)
    out, seen = [], set()
    for k in obj["keep"]:
        if k["id"] in seen:
            continue
        seen.add(k["id"])
        c = dict(cands[k["id"]])
        if k.get("needs_context"):
            c["needs_context"] = True
            c["context_note"] = k.get("context_note") or c.get("context_note") or ""
        out.append(c)
    return out, metas


# ---------------------------------------------------------------------------------------------- variants
VARIANT_SYSTEM = SYSTEM


def variant_openings(provider, clip, phs, band, cancel=None):
    """Ask for ≤3 alternate opening lines for an existing clip (same ending). Each must be a real phrase start inside
    [clip.start - 20 s, clip.end - band_lo]. Returns (list of phrase starts with reasons, metas)."""
    lo = band[0]
    cand_phs = [p for p in phs if clip["start_s"] - 20 <= p["start"] <= clip["end_s"] - lo and abs(p["start"] - clip["start_s"]) > 0.5]
    if not cand_phs:
        return [], []
    block = transcript_block([p for p in phs if clip["start_s"] - 20 <= p["start"] <= clip["end_s"]])
    prompt = (
        f"A short currently opens at [{fmt_ts(clip['start_s'])}] with: \"{clip.get('hook') or ''}\" and ends at "
        f"[{fmt_ts(clip['end_s'])}]. Propose up to 3 ALTERNATE opening lines (same ending) that could hook harder in the first "
        "1.5 s — a stronger claim, a question, the middle of the action. Each opening must be one of the phrase timestamps "
        "below, copied exactly. Only propose openings that are genuinely strong; zero is fine.\n"
        "Return ONLY JSON: {\"openings\": [{\"start\": \"mm:ss.s\", \"line\": \"exact phrase text\", \"why\": \"one sentence\", "
        "\"hook_score\": 0}]}\n\nTRANSCRIPT:\n" + block)

    def val(o):
        if not isinstance(o, dict) or not isinstance(o.get("openings"), list):
            return ["need {\"openings\": [...]}"]
        return []
    obj, metas = llm.complete_json(provider, prompt, VARIANT_SYSTEM, val, cancel=cancel)
    out = []
    for o in obj["openings"][:3]:
        try:
            p = _phrase_at(cand_phs, parse_ts(o.get("start")), tol=1.0)
        except (TypeError, ValueError):
            continue
        if p and _cite_ok(o.get("line"), [p]) and all(abs(p["start"] - x["start"]) > 0.5 for x in out):
            out.append({"start": p["start"], "line": p["text"], "why": str(o.get("why") or "")[:300],
                        "hook_score": o.get("hook_score")})
    return out, metas
