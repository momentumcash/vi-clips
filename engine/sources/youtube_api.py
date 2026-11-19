"""YouTube Data API v3 discovery (list a channel's recent uploads). Discovery only — it never downloads.
Blocked unless YOUTUBE_API_KEY is set. Downloading needs the creator's rights to be owned/licensed/permission_granted
and goes through ytdlp_authorized."""
import json
import os
import re
import urllib.parse
import urllib.request

NEEDS = ["YOUTUBE_API_KEY"]


def status():
    missing = [k for k in NEEDS if not os.environ.get(k)]
    return {"name": "youtube_api", "kind": "discovery", "status": "blocked" if missing else "ready", "needs": missing,
            "doc": "https://developers.google.com/youtube/v3/getting-started — create an API key, export YOUTUBE_API_KEY"}


def _get(path, params):
    params["key"] = os.environ["YOUTUBE_API_KEY"]
    url = "https://www.googleapis.com/youtube/v3/" + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def scan(app, creator):
    st = status()
    if st["status"] != "ready":
        return {"status": "blocked", "reason": "YOUTUBE_API_KEY is not set", "fix": "export YOUTUBE_API_KEY and restart the engine"}
    url = creator.get("channel_url") or ""
    m = re.search(r"/channel/(UC[\w-]+)", url)
    try:
        if m:
            cid = m.group(1)
        else:
            handle = re.search(r"/@([\w.-]+)", url)
            if not handle:
                return {"status": "error", "reason": "channel_url must contain /channel/UC… or /@handle"}
            r = _get("channels", {"part": "id,contentDetails", "forHandle": "@" + handle.group(1)})
            cid = r["items"][0]["id"]
        ch = _get("channels", {"part": "contentDetails", "id": cid})
        uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        items = _get("playlistItems", {"part": "snippet", "playlistId": uploads, "maxResults": 15})["items"]
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "reason": f"YouTube API error: {e}"}
    found = [{"title": i["snippet"]["title"], "url": "https://www.youtube.com/watch?v=" + i["snippet"]["resourceId"]["videoId"],
              "published_at": i["snippet"]["publishedAt"]} for i in items]
    return {"status": "ok", "found": found, "note": "discovery only — import a URL with POST /api/sources/url (rights-gated)"}
