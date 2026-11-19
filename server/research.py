"""Research House: find YouTube channels worth clipping (real numbers via yt-dlp, no API key).

Writes data/app/research.json. Run: python3 server/research.py  (takes ~1-3 min).
A channel is "worth clipping" when it posts LONG videos (20+ min, lots of moments per upload) that pull big views,
especially views far above its subscriber count (x = views / subs).
"""
import json, os, re, statistics, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402

OUT = os.path.join(settings.APP_DATA, "research.json")
YTDLP = settings.ytdlp()
LONG_S = 10 * 60

# GTA 6 creators worth clipping: MEDIUM channels (not the giants everyone already clips) that post
# long GTA 6 videos or streams, where clips can travel further than the original.
QUERIES = [  # (search, niche label)
    ("gta 6 gameplay", "Gameplay"),
    ("gta 6 live stream", "Live"),
    ("gta 6 first time playing", "First playthrough"),
    ("gta 6 funny moments", "Funny"),
    ("gta 6 early access", "Early access"),
    ("gta 6 reaction", "Reaction"),
    ("gta 6 roleplay", "Roleplay"),
    ("gta 6 stream highlights", "Streamer"),
    ("playing gta 6 with friends", "Co-op"),
    ("gta 6 day 1", "Day 1"),
    ("gta 6 story mode", "Story"),
    ("gta 6 chaos", "Funny"),
    ("gta 6 first impressions", "Reaction"),
    ("gta 6 i tried", "Challenge"),
    ("gta 6 duo", "Co-op"),
    ("gta 6 rp", "Roleplay"),
]
MIN_SUBS, MAX_SUBS = 40_000, 1_500_000   # medium creators
GTA = re.compile(r"\bgta\s*(6|vi)\b|grand theft auto (6|vi)|\bleonida\b|vice city", re.I)


def ytdlp_json(args, timeout=120):
    p = subprocess.run([YTDLP, "--no-update", "--flat-playlist", "-J", *args], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError((p.stderr or "yt-dlp failed").strip().splitlines()[-1][:200])
    return json.loads(p.stdout)


def search(q, n=30):
    try:
        d = ytdlp_json([f"ytsearch{n}:{q}"])
    except Exception as e:
        print("search failed", q, e, file=sys.stderr)
        return []
    return d.get("entries") or []


def channel(cid):
    d = ytdlp_json(["--playlist-end", "12", f"https://www.youtube.com/channel/{cid}/videos"])
    avatar = next((t.get("url") for t in d.get("thumbnails") or [] if t.get("id") == "avatar_uncropped"), None)
    return {"subs": d.get("channel_follower_count"), "avatar": avatar, "entries": d.get("entries") or [],
            "url": d.get("channel_url") or f"https://www.youtube.com/channel/{cid}"}


def main():
    t0 = time.time()
    hits = {}  # channel_id -> {name, niche, best search video}
    for q, niche in QUERIES:
        for e in search(q):
            dur, views, cid = e.get("duration") or 0, e.get("view_count"), e.get("channel_id")
            if not cid or dur < LONG_S or not views or not GTA.search(e.get("title") or ""):
                continue
            h = hits.setdefault(cid, {"name": e.get("channel") or e.get("uploader") or "?", "niche": niche, "seen": []})
            h["seen"].append({"id": e["id"], "title": e.get("title") or "", "views": views, "minutes": round(dur / 60)})
    # strongest candidates first: biggest long video found in search
    ranked = sorted(hits.items(), key=lambda kv: max(v["views"] for v in kv[1]["seen"]), reverse=True)[:70]

    def build(item):
        cid, h = item
        try:
            c = channel(cid)
        except Exception as e:
            print("channel failed", h["name"], e, file=sys.stderr)
            return None
        subs = c["subs"] or 0
        if not MIN_SUBS <= subs <= MAX_SUBS:
            return None
        recent = [x for x in c["entries"] if (x.get("duration") or 0) > 0]
        gta_recent = [x for x in recent if GTA.search(x.get("title") or "")]
        long_recent = [x for x in recent if (x.get("duration") or 0) >= LONG_S]
        vids = [{"id": x["id"], "title": x.get("title") or "", "views": x.get("view_count"),
                 "minutes": round((x.get("duration") or 0) / 60),
                 "x": round(x["view_count"] / subs, 2) if subs and x.get("view_count") else None} for x in long_recent]
        # search hits carry reliable view counts; merge them in when the channel page didn't give views
        known = {v["id"] for v in vids}
        for s in h["seen"]:
            if s["id"] not in known:
                vids.append({**s, "x": round(s["views"] / subs, 2) if subs else None})
        with_views = [v for v in vids if v["views"]]
        if not with_views:
            return None
        median = int(statistics.median(v["views"] for v in with_views))
        best_x = max((v["x"] for v in with_views if v["x"]), default=None)
        avg_min = round(statistics.mean(v["minutes"] for v in vids if v["minutes"])) if vids else None
        if len(gta_recent) < 2:
            return None
        why = (f"{len(gta_recent)} of its last {len(recent) or 12} uploads are GTA 6, {len(long_recent)} run {avg_min}+ min"
               f", typical one pulls {fmt(median)} views" + (f" ({best_x:g}× its subs at best)" if best_x and best_x >= 1 else "")
               + ". Mid-size, so clips of it aren't everywhere yet.")
        return {"id": cid, "name": h["name"], "url": c["url"], "subs": subs, "avatar": c["avatar"], "niche": h["niche"],
                "median_views": median, "best_x": best_x, "long_in_last_12": len(long_recent), "gta_in_last_12": len(gta_recent), "recent_window": "last 12 uploads",
                "avg_minutes": avg_min, "why": why,
                "videos": sorted(with_views, key=lambda v: v["views"], reverse=True)[:6]}

    with ThreadPoolExecutor(8) as ex:
        channels = [c for c in ex.map(build, ranked) if c]
    channels.sort(key=lambda c: (c["best_x"] or 0), reverse=True)
    out = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "queries": [q for q, _ in QUERIES],
           "method": "YouTube search (yt-dlp, no API key) for long GTA 6 videos, kept channels with 40K-1.5M subs and 2+ GTA 6 uploads in their last 12. x = views / subscribers.",
           "seconds": round(time.time() - t0), "channels": channels}
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)
    print(f"{len(channels)} channels in {out['seconds']}s -> {OUT}")


def fmt(n):
    return f"{n / 1e6:.1f}M".replace(".0M", "M") if n >= 1e6 else f"{round(n / 1e3)}K" if n >= 1e3 else str(n)


if __name__ == "__main__":
    main()
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import live_now
        live_now.main()
    except Exception as e:  # noqa: BLE001
        print("live now failed:", e, file=sys.stderr)
