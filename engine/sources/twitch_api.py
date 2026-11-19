"""Twitch Helix discovery (list a channel's recent VODs). Discovery only. Blocked unless TWITCH_CLIENT_ID and
TWITCH_CLIENT_SECRET are set (app access token via client-credentials)."""
import json
import os
import re
import urllib.parse
import urllib.request

NEEDS = ["TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"]


def status():
    missing = [k for k in NEEDS if not os.environ.get(k)]
    return {"name": "twitch_api", "kind": "discovery", "status": "blocked" if missing else "ready", "needs": missing,
            "doc": "https://dev.twitch.tv/docs/api/ — register an app, export TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET"}


def _token():
    data = urllib.parse.urlencode({"client_id": os.environ["TWITCH_CLIENT_ID"], "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
                                   "grant_type": "client_credentials"}).encode()
    with urllib.request.urlopen("https://id.twitch.tv/oauth2/token", data=data, timeout=20) as r:
        return json.loads(r.read())["access_token"]


def _get(path, params, tok):
    req = urllib.request.Request("https://api.twitch.tv/helix/" + path + "?" + urllib.parse.urlencode(params),
                                 headers={"Client-Id": os.environ["TWITCH_CLIENT_ID"], "Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def scan(app, creator):
    if status()["status"] != "ready":
        return {"status": "blocked", "reason": "TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set",
                "fix": "export both and restart the engine"}
    m = re.search(r"twitch\.tv/([\w]+)", creator.get("channel_url") or "")
    if not m:
        return {"status": "error", "reason": "channel_url must look like https://twitch.tv/<login>"}
    try:
        tok = _token()
        u = _get("users", {"login": m.group(1)}, tok)["data"][0]
        vids = _get("videos", {"user_id": u["id"], "type": "archive", "first": 15}, tok)["data"]
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "reason": f"Twitch API error: {e}"}
    return {"status": "ok", "found": [{"title": v["title"], "url": v["url"], "duration": v["duration"],
                                       "published_at": v["published_at"]} for v in vids],
            "note": "discovery only — import a URL with POST /api/sources/url (rights-gated)"}
