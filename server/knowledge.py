"""Knowledge House: what makes short-form clips go viral.

  python3 server/knowledge.py seed     -> writes data/app/knowledge.json from the starter rules (if it doesn't exist)
  python3 server/knowledge.py update   -> asks headless Claude (WebSearch/WebFetch) for NEW, sourced principles and appends them

Every principle carries where it came from. "starter" = shipped starter rule; "proven" = widely tested on posts;
"research" = found on the web by the updater (always with a source URL).
"""
import json, os, re, shutil, subprocess, sys, uuid
from datetime import datetime, timezone

from settings import APP_DATA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(APP_DATA, "knowledge.json")
CATS = ["hook", "retention", "editing", "captions", "format", "picking", "distribution"]

SEED = [
    # hook
    ("hook", "Open on the surprise, not the setup", "Frame one is the conflict itself. No intros, no \"so\", no \"hey guys\".", "Viewers decide in the first second; setup spends that second on nothing.", "starter", "Common clipper practice"),
    ("hook", "A hook opens a loop", "If the first line answers itself it's a caption, not a hook. Cut before the answer.", "People stay to close a question. A complete thought gives them a reason to leave.", "starter", "Common clipper practice"),
    ("hook", "Talk to the viewer, not about yourself", "Best openers: a question to the viewer, an emotion word, an imperative. Worst: opening on \"I\".", "Viewers watch for what's in it for them.", "starter", "Common clipper practice"),
    ("hook", "Specific beats general", "A concrete number or a named thing beats a broad claim.", "Specifics read as true and make the viewer want the rest.", "starter", "Common clipper practice"),
    ("hook", "The withheld part must still read as a sentence", "A teaser that only makes sense after the payoff is noise, not a loop.", "Half-sentence teasers confuse cold viewers.", "starter", "Common clipper practice"),
    ("hook", "Lead with the visual flash", "If the moment has a crash, reveal or wipeout, open on it near-wordless and hard-cut in.", "Shorter and poppier wins; talking cold-opens are the fallback.", "starter", "Common clipper practice"),
    # retention
    ("retention", "Open the next loop before closing this one", "The hook only opens the first loop. Ask the next question before (or as) you answer the current one.", "Viewers leave the moment a loop closes.", "proven", "Short-form retention basics"),
    ("retention", "Something changes every ~3 seconds", "A cut, a zoom, a new caption beat or a reaction at least every 3 seconds.", "Static stretches are where the swipe happens.", "starter", "Common clipper practice"),
    ("retention", "The body pays off without outside context", "A clip must make sense to someone who never saw the long video.", "Inside jokes and references to earlier parts lose cold viewers.", "starter", "Common clipper practice"),
    ("retention", "Show what the words say", "If the line talks about a thing, that thing is on screen every frame it's mentioned.", "Mismatch between words and picture breaks trust and attention.", "starter", "Common editing practice"),
    # picking
    ("picking", "Pick moments by picture first", "Sweep the video as pictures before reading the transcript; the best moments often have weak words.", "A near-miss on screen can be transcribed as just \"Like this.\"", "starter", "Common clipper practice"),
    ("picking", "Three passes, not one score", "1) 1-3s of tension, 2) the body pays it off alone, 3) motion every ~3s. All three must pass.", "One blended score hides a missing piece.", "starter", "Common clipper practice"),
    ("picking", "One subject, one clip", "Two clips about the same moment compete; find a genuinely separate moment instead.", "Repeats read as filler to the algorithm and the viewer.", "starter", "Common clipper practice"),
    ("picking", "Topic beats channel size", "A video that pulls many times its channel's subscribers proves the topic carried it. Clip those topics.", "Outliers on small channels are the clearest signal of demand.", "research", "Outlier method (views / subscribers)"),
    # editing
    ("editing", "Cut on measured silence", "Join cuts on real silence in the waveform, never mid-word.", "Clipped words sound cheap and break flow.", "starter", "Common editing practice"),
    ("editing", "Length is what the moment needs", "Most strong hooks run 2-4s; the clip ends when the payoff lands. Never truncate a word to hit a number.", "A clip cut short of its payoff wastes the hook.", "starter", "Common editing practice"),
    ("editing", "Authentic or cinematic, never the middle", "Raw phone-real and high-end cinematic both win; the AI-looking middle loses.", "Viewers punish anything that looks mass-produced.", "proven", "Short-form creator consensus"),
    # captions
    ("captions", "Captions inside the safe zone", "Keep words clear of TikTok's UI: off the bottom ~20% and the right-side buttons.", "Covered captions get swiped.", "starter", "Platform safe zones"),
    ("captions", "Big, few words, one active word", "Short caption chunks with the spoken word highlighted.", "Most people watch muted at first; the eye follows the highlight.", "starter", "Common editing practice"),
    # format
    ("format", "Copy what already worked", "Every clip idea should trace back to a format that already went viral.", "Proven demand beats guessing.", "proven", "Short-form creator consensus"),
    ("format", "Make it yours, don't reupload", "Copy the format, topic and title style, then add your own edit and commentary.", "Reused or barely changed footage gets demonetized as reused content.", "research", "YouTube reused-content policy"),
    # distribution
    ("distribution", "Don't spam uploads", "Space posts out (about one a day per account) instead of dumping a batch.", "Bursts read as low quality and split the audience's attention.", "research", "Creator growth guides"),
    ("distribution", "Break your own videos down", "Use AI to break your own posts down part by part (hook, on-screen, ending) against their numbers.", "Learning from your own data compounds.", "proven", "Short-form creator consensus"),
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(d):
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)


def seed(force=False):
    if os.path.exists(OUT) and not force:
        print("exists:", OUT)
        return
    shipped = os.path.join(ROOT, "seed", "knowledge.json")  # the knowledge base that ships with the repo
    if os.path.exists(shipped):
        import shutil as _sh
        _sh.copyfile(shipped, OUT)
        print("seeded from", shipped)
        return
    t = now()
    d = {"updated": t, "principles": [
        {"id": uuid.uuid4().hex[:8], "category": c, "title": ti, "rule": r, "why": w, "confidence": conf,
         "source": {"label": src, "url": None}, "added": t} for c, ti, r, w, conf, src in SEED],
         "log": [{"date": t, "added": len(SEED), "note": "Seeded with the starter rules"}]}
    save(d)
    print("seeded", len(SEED))


PROMPT = """You maintain a knowledge base of principles for what makes SHORT-FORM video clips (TikTok, Reels, Shorts) go viral,
used by a tool that cuts long videos (podcasts, streams, interviews, gameplay) into clips.

Existing principle titles (do not repeat these or near-duplicates):
{titles}

Use WebSearch and WebFetch to find up to 5 NEW principles that are concrete and actionable for picking, hooking, editing,
captioning or distributing clips. Prefer sources from the last 12 months: platform creator docs (TikTok Creator Academy,
YouTube Shorts help, Instagram creators), published retention data, well-known clipping agencies or creators explaining their method.
Every principle MUST have a real source URL you actually opened. Skip anything vague ("be authentic", "post consistently").

Reply with ONLY a JSON array, no prose:
[{{"category": one of {cats}, "title": "<= 8 words", "rule": "1-2 sentences, what to do", "why": "1 sentence", "source_label": "site or author", "source_url": "https://..."}}]
"""


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def update():
    seed()
    d = json.load(open(OUT))
    titles = "\n".join("- " + p["title"] for p in d["principles"])
    import getpass
    user = os.environ.get("USER") or getpass.getuser()
    env = dict(os.environ, USER=user, LOGNAME=os.environ.get("LOGNAME") or user)
    p = subprocess.run([shutil.which("claude") or os.path.expanduser("~/.local/bin/claude"), "-p", "--output-format", "json", "--allowedTools", "WebSearch,WebFetch"],
                       input=PROMPT.format(titles=titles, cats=CATS), capture_output=True, text=True, timeout=900, env=env)
    if p.returncode != 0:
        raise SystemExit("claude failed: " + (p.stderr or p.stdout)[-400:])
    text = json.loads(p.stdout).get("result", "")
    m = re.search(r"\[.*\]", text, re.S)
    items = json.loads(m.group(0)) if m else []
    have = {norm(x["title"]) for x in d["principles"]}
    t, added = now(), 0
    for it in items:
        url = (it.get("source_url") or "").strip()
        if it.get("category") not in CATS or not url.startswith("http") or norm(it.get("title", "")) in have:
            continue
        d["principles"].append({"id": uuid.uuid4().hex[:8], "category": it["category"], "title": it["title"].strip(),
                                "rule": it.get("rule", "").strip(), "why": it.get("why", "").strip(), "confidence": "research",
                                "source": {"label": it.get("source_label", "web").strip(), "url": url}, "added": t})
        have.add(norm(it["title"]))
        added += 1
    d["updated"] = t
    d["log"].insert(0, {"date": t, "added": added, "note": "Web research update" if added else "Web research: nothing new worth adding"})
    save(d)
    print("added", added)


if __name__ == "__main__":
    {"seed": lambda: seed(force="--force" in sys.argv), "update": update}[sys.argv[1] if len(sys.argv) > 1 else "seed"]()
