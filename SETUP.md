# SETUP (for your AI agent)

You are setting up VI Clips on the user's Mac. Work through these steps in order. Run the checks yourself; only ask the user when something truly needs them (a password, a sign-in, a choice). Keep questions short.

## 0. Requirements
- macOS on Apple Silicon (`uname -m` = `arm64`). If not, stop and tell the user it's macOS-only.
- Run everything from the repo root (the folder with `start.sh`).

## 1. Check what's installed
```
which brew python3.12 ffmpeg yt-dlp claude swiftc
```
- Missing Homebrew: tell the user to install it from https://brew.sh (needs their password).
- Missing python3.12 / ffmpeg / yt-dlp: `brew install python@3.12 ffmpeg yt-dlp`
- Missing `claude` (Claude Code CLI): needed to pick moments, for typed edits and Knowledge updates. Install it (https://docs.claude.com/claude-code) and have the user run `claude` once to sign in. Check with `claude -p "say ok"`.
- Missing `swiftc`: `xcode-select --install` (face tracking uses Apple Vision; without it clips still render, framed by the centre).

## 2. Install
```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python engine/tools/get_models.py small
```
Optional, better captions on sources up to 30 min (~1.6 GB): `.venv/bin/python engine/tools/get_models.py large-v3-turbo`

## 3. Optional: GPU transcription
The engine tries a whisper.cpp server at `http://127.0.0.1:8879` first and falls back to CPU. Only set this up if the user wants it faster:
```
brew install whisper-cpp
mkdir -p ~/whisper-models && curl -L -o ~/whisper-models/ggml-large-v3-turbo.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
whisper-server -m ~/whisper-models/ggml-large-v3-turbo.bin --port 8879
```
If port 8879 is used by something else, set `VI_WHISPER_SERVER=off` (or another URL).

## 4. Live clipping (optional, one time)
YouTube sometimes asks for a sign-in when recording live streams. By default yt-dlp reads cookies from Chrome (`VI_BROWSER` to change). If that fails, export a cookie file once:
```
yt-dlp --cookies-from-browser chrome --cookies data/app/yt-cookies.txt --skip-download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```
That file stays in `data/` (git-ignored). Never commit it.

## 5. Start
```
./start.sh
```
If 8870 or 8885 are taken, pick others: `VI_ENGINE_PORT=18870 VI_SERVER_PORT=18885 ./start.sh`

## 6. Verify
```
curl -s http://127.0.0.1:8870/api/ping          # {"ok": true, ...}
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8885/app/index.html   # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8870/api/jobs         # 200
```
If something fails, read `data/logs/engine.log` and `data/logs/server.log` and fix it.

## 7. Hand over
Tell the user, in a few lines: the app URL, "paste a link in Content House", `./stop.sh` to stop, and that they should only clip videos they have permission to repost.
