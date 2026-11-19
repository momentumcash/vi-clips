"""Live streams happening right now (YouTube live search, no key) -> data/app/research.json "live" list, ranked by viewers.
Run: python3 server/live_now.py  (~10-20 s). Called by research refresh too."""
import json, os, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402

OUT = os.path.join(settings.APP_DATA, "research.json")
LIVE_FILTER = "EgJAAQ%253D%253D"  # YouTube search filter: Live
QUERIES = ["gta 6", "gta 6 live", "gta vi", "gta 6 stream", "ishowspeed", "gaming"]


def ytdlp(args, timeout=60):
    p = subprocess.run([settings.ytdlp(), "--no-update", *args], capture_output=True, text=True, timeout=timeout)
    return p.stdout


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"


def _num(text):
    import re
    m = re.search(r"([\d.,]+)\s*([KM]?)", text or "")
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    return int(n * {"K": 1e3, "M": 1e6}.get(m.group(2), 1))


def search_live(q):
    """One request: YouTube's live-filtered results page; parse ytInitialData videoRenderers (title, channel, watching)."""
    import re, urllib.request
    url = f"https://www.youtube.com/results?search_query={q.replace(' ', '+')}&sp={LIVE_FILTER}&hl=en"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en"})
    html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if v:
                badges = json.dumps(v.get("badges") or []) + json.dumps(v.get("thumbnailOverlays") or [])
                vc = " ".join(r.get("text", "") for r in (v.get("viewCountText") or {}).get("runs", [])) or (v.get("viewCountText") or {}).get("simpleText", "")
                if "LIVE" in badges.upper() or "watching" in vc:
                    owner = (v.get("ownerText") or {}).get("runs", [{}])[0]
                    ep = owner.get("navigationEndpoint", {}).get("browseEndpoint", {})
                    thumbs = (((v.get("channelThumbnailSupportedRenderers") or {}).get("channelThumbnailWithLinkRenderer") or {})
                              .get("thumbnail") or {}).get("thumbnails") or []
                    out.append({"id": v.get("videoId"), "url": f"https://www.youtube.com/watch?v={v.get('videoId')}",
                                "viewers": _num(vc), "channel": owner.get("text", ""),
                                "channel_url": "https://www.youtube.com" + ep.get("canonicalBaseUrl", "") if ep.get("canonicalBaseUrl") else "",
                                "avatar": thumbs[-1]["url"] if thumbs else None,
                                "title": "".join(r.get("text", "") for r in (v.get("title") or {}).get("runs", []))})
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(json.loads(m.group(1)))
    return out


def main():
    t0 = time.time()
    with ThreadPoolExecutor(4) as ex:
        rows = [r for rs in ex.map(search_live, QUERIES) for r in rs if r.get("id")]
    best = {}
    for r in rows:  # one stream per channel
        if r["channel"] not in best or r["viewers"] > best[r["channel"]]["viewers"]:
            best[r["channel"]] = r
    import re
    gta = re.compile(r"\bgta\s*(6|vi)\b|grand theft auto\s*(6|vi)|vice city|leonida", re.I)
    live = sorted((r for r in best.values() if gta.search(r["title"])), key=lambda r: r["viewers"], reverse=True)[:16]
    try:
        d = json.load(open(OUT))
    except (FileNotFoundError, ValueError):
        d = {}
    d["live"] = live
    d["live_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = OUT + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)
    print(f"{len(live)} live streams in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
