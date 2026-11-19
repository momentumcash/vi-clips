from publish.base import Publisher


class Instagram(Publisher):
    name = "instagram"
    needs = ["INSTAGRAM_BUSINESS_ACCOUNT_ID", "INSTAGRAM_ACCESS_TOKEN"]
    doc = "https://developers.facebook.com/docs/instagram-platform/content-publishing — Reels via a Business/Creator account"
    why = "the Graph API also needs the video at a public HTTPS URL, which ClipForge does not host"
