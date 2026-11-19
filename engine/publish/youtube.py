from publish.base import Publisher


class YouTube(Publisher):
    name = "youtube"
    needs = ["YOUTUBE_OAUTH_CLIENT_ID", "YOUTUBE_OAUTH_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"]
    doc = "https://developers.google.com/youtube/v3/guides/uploading_a_video — OAuth 2.0 with youtube.upload scope"
    why = "an API key alone cannot upload; uploads need user OAuth"
