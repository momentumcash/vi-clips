from publish.base import Publisher


class TikTok(Publisher):
    name = "tiktok"
    needs = ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_ACCESS_TOKEN"]
    doc = "https://developers.tiktok.com/doc/content-posting-api-get-started — Content Posting API needs an audited app"
    why = "unaudited TikTok apps can only post privately (SELF_ONLY)"
