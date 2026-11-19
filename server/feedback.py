"""Reject feedback: your reasons for rejecting clips teach the clipper.

record(entry) appends to data/feedback.json, then rewrites the LEARNED block inside the engine's detect prompt
(engine/prompts/detect.md, between the <!-- learned:start --> / <!-- learned:end --> markers). ClipForge reads that
file on every job, so the next video is picked with his feedback in mind; no engine restart needed.
It also logs the lesson in Knowledge House (data/knowledge.json log).
"""
import json, os, re
from collections import Counter
from datetime import datetime, timezone

from settings import APP_DATA, DETECT_PROMPT as DETECT

FB = os.path.join(APP_DATA, "feedback.json")
KNOW = os.path.join(APP_DATA, "knowledge.json")
START, END = "<!-- learned:start -->", "<!-- learned:end -->"

REASONS = {  # chip id -> what the clipper should do differently
    "weak_hook": "Weak hook: the first 1.5 s didn't grab. Start later, on the line that grabs, or drop it.",
    "boring": "Boring / slow: too much talking without a payoff. Cut tighter or drop it.",
    "no_payoff": "No payoff: it ended before (or without) the moment landing.",
    "needs_context": "Confusing without context: a cold viewer couldn't follow it.",
    "bad_cut": "Bad start or end point: cut mid-thought or ran on after the payoff.",
    "wrong_moment": "Wrong moment: there were better moments in the video than this one.",
    "bad_hook_box": "Hook box text was weak or gave the payoff away.",
    "too_long": "Too long for what it is.",
}

LIKES = {  # approve chip id -> what to repeat
    "great_hook": "Great hook: the first second grabbed. Open like this.",
    "funny": "Funny: a real laugh moment. Find more of these.",
    "big_payoff": "Big payoff: the moment landed hard at the end.",
    "clean_cut": "Clean start and end: tight, no dead air.",
    "good_hook_text": "Hook text on top was strong and didn't spoil it.",
    "right_length": "Right length for the moment.",
}


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def learned_block(items):
    if not items:
        return ""
    edits = [it for it in items if it.get("kind") == "edit"][-10:]
    likes = [it for it in items if it.get("kind") == "approve"]
    rated = [it for it in items if it.get("kind") == "rating"]
    items = [it for it in items if it.get("kind") not in ("edit", "approve", "rating")]
    good = ""
    if rated:
        avg = sum(it["score"] for it in rated) / len(rated)
        rl = [f"## How the owner RATES clips (1-10; {len(rated)} rated, average {avg:.1f})"]
        top = [it for it in rated if it["score"] >= 8][-8:]
        low = [it for it in rated if it["score"] < 8 and it.get("note")][-14:]
        if top:
            rl.append("Rated 8-10 (make more like these):")
            rl += [f"- {it['score']}/10 \"{it['title'][:70]}\"" + (f": {it['note'][:160]}" if it.get("note") else "") for it in top]
        if low:
            rl.append("What he said would make a clip a 10 (apply this up front):")
            rl += [f"- {it['score']}/10 \"{it['title'][:70]}\": {it['note'][:200]}" for it in low]
        good += "\n".join(rl) + "\n\n"
    if likes:
        lt = Counter(r for it in likes for r in it.get("reasons", []))
        gl = ["## What the owner APPROVED (do more of this)", f"{len(likes)} clip(s) approved with a reason. What he liked:"]
        gl += [f"- {LIKES.get(r, r)} ({n}x)" for r, n in lt.most_common()]
        gl += [f"- \"{it['title'][:70]}\": {it['note'][:200]}" for it in likes if it.get("note")][-12:]
        good = "\n".join(gl) + "\n\n"
    if not items and not edits:
        return good.strip()
    if not items and edits:
        return good + "## How the owner fixes clips (do this up front next time)\n" + "\n".join(f"- \"{e['title'][:70]}\": {e['note'][:200]}" for e in edits)
    tally = Counter(r for it in items for r in it.get("reasons", []))
    lines = [good + "## What the owner rejected before (avoid repeating these)",
             f"{len(items)} clip(s) rejected so far. Most common reasons:"]
    for r, n in tally.most_common():
        lines.append(f"- {REASONS.get(r, r)} ({n}x)")
    notes = [it for it in items if it.get("note")][-12:]
    if notes:
        lines.append("His own words on recent rejects:")
        for it in notes:
            lines.append(f"- \"{it['title'][:70]}\": {it['note'][:200]}")
    if edits:
        lines.append("How he fixes clips by hand (do this up front next time):")
        lines += [f"- \"{e['title'][:70]}\": {e['note'][:200]}" for e in edits]
    lines.append("Use these when choosing and bounding moments, and when writing the hook box.")
    return "\n".join(lines)


def write_detect(items):
    s = open(DETECT).read()
    block = f"{START}\n{learned_block(items)}\n{END}"
    if START in s:
        s = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, s, flags=re.S)
    else:  # first time: put it right before the scoring rules
        s = s.replace("## Scores", block + "\n\n## Scores", 1)
    with open(DETECT, "w") as f:
        f.write(s)


def record(entry):
    items = _load(FB, [])
    t = datetime.now(timezone.utc).isoformat(timespec="seconds")
    item = {"at": t, "clip_id": entry.get("clip_id"), "title": str(entry.get("title") or "")[:200],
            "source": str(entry.get("source") or "")[:200],
            "reasons": [r for r in entry.get("reasons") or [] if r in REASONS or r in LIKES],
            "note": str(entry.get("note") or "").strip()[:500],
            "kind": entry.get("kind") if entry.get("kind") in ("edit", "approve", "rating") else "reject"}
    if item["kind"] == "rating":
        item["score"] = max(1, min(10, int(entry.get("score") or 0) or 1))
    items.append(item)
    _save(FB, items)
    write_detect(items)
    k = _load(KNOW, None)
    if k is not None:
        if item["kind"] == "rating":
            msg = f"You rated \"{item['title'][:60]}\" {item['score']}/10" + (f": {item['note'][:90]}" if item["note"] else "")
        elif item["kind"] == "approve":
            why = ", ".join(LIKES[r].split(":")[0].lower() for r in item["reasons"]) or "no reason picked"
            msg = f"You approved \"{item['title'][:60]}\": {why}" + (f" ({item['note'][:80]})" if item["note"] else "")
        elif item["kind"] == "edit":
            msg = f"You edited \"{item['title'][:60]}\": {item['note'][5:110]}"
        else:
            why = ", ".join(REASONS[r].split(":")[0].lower() for r in item["reasons"]) or "no reason picked"
            msg = f"You rejected \"{item['title'][:60]}\": {why}" + (f" ({item['note'][:80]})" if item["note"] else "")
        k.setdefault("log", []).insert(0, {"date": t, "added": 0, "note": msg})
        _save(KNOW, k)
    return {"saved": True, "total": len(items)}
