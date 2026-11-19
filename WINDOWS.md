# VI Clips on Windows (or Linux)

VI Clips is built and tested on Apple Silicon Macs. It can run on Windows or Linux with a few small changes, and your AI agent can make them for you.

## Let your AI do it

Open Claude Code in the folder where you want it and paste this:

```
Clone https://github.com/momentumcash/vi-clips into ./vi-clips. It was built for macOS. Port it to my OS (Windows or Linux) by following vi-clips/WINDOWS.md, section "What to change", then follow vi-clips/SETUP.md (using the Windows/Linux equivalents of each command) to install, start and verify it. Make one clip from a short test video to prove it works. Only ask me something if you truly can't continue without it.
```

## What to change (for the agent)

1. **Video encoding** (`engine/render.py`, `engine/signals.py`, `engine/reframe.py`)
   - Remove every `"-hwaccel", "videotoolbox"` pair from the ffmpeg args.
   - Replace `h264_videotoolbox` with the encoder the machine has. Check `ffmpeg -encoders`:
     - NVIDIA: `h264_nvenc` (use `-cq 23` in place of `-q:v 65`).
     - AMD: `h264_amf`. Intel: `h264_qsv`.
     - Anything else: `libx264 -preset veryfast -crf 20`.
   - Drop `-allow_sw 1`, which only applies to VideoToolbox.
   - Best: put the encoder in one setting (e.g. env `VI_ENCODER`) and choose it at startup.

2. **Face tracking** (`engine/reframe.py`)
   - Apple Vision (`engine/bin/facedetect.swift`) needs `swiftc`. Without it, the engine already falls back to a scikit-image LBP cascade.
   - Add `scikit-image` to `requirements.txt` so that fallback works.
   - Optional, better: add an OpenCV detector (`opencv-python`, Haar or YuNet). Return the same `[cx, cy, w, h, score]` normalised format.

3. **"Show in folder" on Export** (`server/vi_server.py`)
   - `open -R <file>` is macOS only.
   - Windows: `explorer /select,"<file>"`. Linux: `xdg-open <folder>`.

4. **Start and stop scripts**
   - `start.sh`/`stop.sh` work as-is on Linux and in Git Bash or WSL.
   - For plain Windows, add `start.ps1` that does the same:
     - use `.venv\Scripts\python.exe`;
     - start `engine/server.py` and `server/vi_server.py`;
     - save their PIDs;
     - open `http://127.0.0.1:8885/app/index.html`.

5. **Installing tools**
   - Windows: `winget install Python.Python.3.12 Gyan.FFmpeg yt-dlp.yt-dlp`, plus Claude Code.
   - Linux: your package manager (`python3.12`, `ffmpeg`, `yt-dlp`).
   - The ffmpeg build must include libass, which the caption burn-in needs. Check with `ffmpeg -filters | grep ass`.

6. **Fonts and emoji**
   - The hook box draws emoji with Apple Color Emoji. On Windows, point `engine/hookbar.py` at `C:\Windows\Fonts\seguiemj.ttf`.
   - On Linux, use Noto Color Emoji.

7. **Optional fast transcription**
   - A whisper.cpp `whisper-server` on port 8879 also works on Windows (CUDA build) and Linux.
   - Without it, the CPU faster-whisper path runs everywhere.

Everything else (Python, the app, the prompts, live clipping with yt-dlp) is cross-platform already.
