"""Edit a clip from plain feedback ("make the hook crazier", "start 2 seconds later", "cut the ending").

apply(clip_id, text): reads the clip from ClipForge, asks headless Claude to turn the feedback into a small JSON patch,
applies it (PATCH /api/clips/:id) and starts a re-render. Returns {changes:[...], summary}. Also logs it as feedback so
the clipper learns how you fix clips.
"""
import json, os, re, subprocess, urllib.request

import feedback
from settings import ENGINE_URL as CF
import shutil
CLAUDE = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
PRESETS = ["bold_entertainment", "gaming", "high_energy", "clean_podcast", "minimal_cinematic"]


def cf(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(CF + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


PROMPT = """You edit one short-form clip (TikTok/Shorts) that was cut from a longer video. Turn the owner's feedback into changes.

CLIP
- title: {title}
- hook pill (on screen the whole clip; streamer-clip style: streamer's short name first, what happens, the game, 1-2 emoji at the end, 6-11 words, sentence case, max one CAPS word, exaggerated but true, NO "||"): {hook}
- range in the source: {start:.1f}s to {end:.1f}s ({dur:.1f}s long; source is {src_dur:.0f}s)
- caption style: {preset} (options: {presets})
- framing: {crop}
- what is said in the clip (with source timestamps):
{words}
- a little before the clip:
{before}
- a little after the clip:
{after}

OWNER'S FEEDBACK: "{text}"

Reply with ONLY JSON, no prose. Include only the keys that should change:
{{"context_note": "new hook pill text in that style, e.g. \"Speed tried to fix a girl's squat in GTA 6 😭\" (empty string = no pill)",
  "title": "new title",
  "start_s": new absolute start in seconds, "end_s": new absolute end in seconds,
  "caption_preset": one of the options,
  "crop": {{"mode": "auto"}} or {{"mode": "manual", "x": 0.0-1.0 (0 = left edge, 0.5 = centre, 1 = right edge)}},
  "summary": "one short line saying what you changed, in plain words"}}
WORDS: when he says "caption", "the text", "the top", "the title on it" or quotes new wording, he means the HOOK PILL (context_note). If he gives the words, use them as the pill text (fix casing, add the game/emoji only if missing). Only change caption_preset if he names the subtitle font/style or says "subtitles".
Rules: change ONLY what the feedback asks for (a hook request changes context_note only; don't touch title, captions or framing unless asked); keep clips between 3 and 180 s and inside the source; when trimming, land on the transcript times; never invent events for the hook.
"""


def _words(tr, a, b):
    rows = [w for w in tr if a <= w.get("start", 0) <= b]
    return " ".join(f"[{w['start']:.1f}] {w['word'].strip()}" if i % 8 == 0 else w["word"].strip() for i, w in enumerate(rows))[:3500] or "(no words)"


def apply(clip_id, text):
    clip = cf("GET", f"/api/clips/{int(clip_id)}")
    src = cf("GET", f"/api/sources/{clip['source_id']}")
    words = clip.get("words") or []
    a, b = float(clip["start_s"]), float(clip["end_s"])
    prompt = PROMPT.format(title=clip.get("title") or "", hook=clip.get("context_note") or "(none)", start=a, end=b, dur=b - a,
                           src_dur=float(src.get("duration_s") or b), preset=clip.get("caption_preset") or "bold_entertainment",
                           presets=", ".join(PRESETS),
                           crop=json.dumps(clip.get("crop") or {"mode": "auto"})[:200],
                           words=_words(words, a, b), before=_words(words, a - 12, a), after=_words(words, b, b + 12),
                           text=text.strip()[:600])
    import getpass
    user = os.environ.get("USER") or getpass.getuser()
    env = dict(os.environ, USER=user, LOGNAME=os.environ.get("LOGNAME") or user)
    p = subprocess.run([CLAUDE, "-p", "--output-format", "json", "--model", "haiku", "--tools", ""], input=prompt,
                       capture_output=True, text=True, timeout=180, env=env)
    if p.returncode != 0:
        raise RuntimeError("Claude couldn't read that: " + (p.stderr or p.stdout)[-200:])
    out = json.loads(p.stdout).get("result", "")
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError("Claude didn't return changes")
    plan = json.loads(m.group(0))
    body = {k: plan[k] for k in ("context_note", "title", "start_s", "end_s", "caption_preset", "crop") if k in plan}
    if "caption_preset" in body and body["caption_preset"] not in PRESETS:
        body.pop("caption_preset")
    if not body:
        return {"changes": [], "summary": plan.get("summary") or "Nothing to change"}
    cf("PATCH", f"/api/clips/{int(clip_id)}", body)
    fast = False
    if set(body) <= {"context_note", "title"}:  # hook/title only: answer now, lay the new pill over the kept clip in the background
        import threading
        fast = True
        threading.Thread(target=lambda: cf("POST", f"/api/clips/{int(clip_id)}/rehook", {}, timeout=180), daemon=True).start()
    else:
        cf("POST", f"/api/clips/{int(clip_id)}/render", {})
    summary = plan.get("summary") or ", ".join(body)
    try:
        feedback.record({"clip_id": clip_id, "title": clip.get("title"), "source": clip.get("source_title"), "reasons": [],
                         "note": f"EDIT \"{text.strip()[:160]}\" -> {summary}", "kind": "edit"})
    except Exception:  # noqa: BLE001
        pass
    return {"changes": list(body), "summary": summary, "fast": fast, "context_note": body.get("context_note")}
