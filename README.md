# VI Clips

Turn long GTA 6 streams and videos into captioned 9:16 clips, on your Mac. Paste a link (or a live stream), it finds the moments worth posting, cuts them with a hook on top, and you approve, rate or edit them in plain words.

by @momentumcash · MIT · **Built for Apple Silicon Macs.** On Windows or Linux? See [WINDOWS.md](WINDOWS.md): one prompt and your AI ports it for you.

> Unofficial fan tool. Not affiliated with or endorsed by Rockstar Games or Take-Two Interactive.

## Set it up with your AI (easiest)

Open Claude Code in an empty folder and paste this:

```
Clone https://github.com/momentumcash/vi-clips into ./vi-clips, then open vi-clips/SETUP.md and follow it step by step: check what I already have, install what's missing, start it, and verify it works. Only ask me something if you truly can't continue without it.
```

## Step by step (if you'd rather do it yourself)

1. Install the tools: `brew install python@3.12 ffmpeg yt-dlp` and [Claude Code](https://docs.claude.com/claude-code) (run `claude` once and sign in).
2. `git clone https://github.com/momentumcash/vi-clips && cd vi-clips`
3. `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. Download the speech model once: `.venv/bin/python engine/tools/get_models.py small` (add `large-v3-turbo` for better captions on videos up to 30 min).
5. `./start.sh` and the app opens at http://127.0.0.1:8885/app/index.html

Optional: faster transcription on the GPU with `brew install whisper-cpp` and a `whisper-server` on port 8879 (see SETUP.md). Without it, transcription runs on the CPU.

## How you use it

| You want to | Do this |
|---|---|
| Clip a video | Content House: paste a YouTube link or drop a file |
| Clip a live stream as it happens | Paste the live link: it records 10-minute chunks and clips each one |
| Find creators worth clipping | Research House → Refresh (mid-size GTA 6 channels + who's live now) |
| Change a clip | Open it → Edit → type "make the hook crazier" or "start 2 seconds later" |
| Teach it your taste | Rate, Approve or Reject with a reason; the next videos are picked with it |
| Read what makes clips work | Knowledge House (Update now pulls new sourced principles with Claude) |
| Get the file | Export: copies the clip to `~/Movies/VI Clips` and shows it in Finder |
| Stop it | `./stop.sh` |

## Use responsibly

Only clip streams and videos you have permission to repost. The engine keeps a rights gate: URL downloads and exports need the creator marked owned, licensed or permission granted. Respect each platform's terms.

## What's in here

```
vi-clips/
├── start.sh / start.command / stop.sh   start both services, open the app / stop them
├── requirements.txt                     Python deps (faster-whisper, numpy, pillow, imageio-ffmpeg)
├── SETUP.md                             instructions your AI agent follows
├── app/                                 the VI Clips app (one HTML file + logo)
├── server/                              app server :8885 (research, knowledge, live, edit, feedback)
├── engine/                              clip engine :8870 (transcribe → detect → score → render → QC)
│   ├── prompts/detect.md                the moment-picking prompt (learns from your feedback)
│   ├── fonts/                           OFL caption fonts + licences
│   └── tests/                           unit tests
├── studio/                              the engine's own detailed editor (http://127.0.0.1:8870)
├── seed/knowledge.json                  starter Knowledge House principles
└── data/                                created at runtime (DB, renders, logs) — git-ignored
```

Settings (env vars): `VI_ENGINE_PORT` (8870), `VI_SERVER_PORT` (8885), `VI_DATA` (./data), `VI_EXPORT_DIR` (~/Movies/VI Clips), `VI_WHISPER_SERVER` (http://127.0.0.1:8879, `off` to disable), `VI_BROWSER` (chrome).
