"""ffmpeg plumbing. There is no ffprobe on this machine: streams/duration are read from `ffmpeg -i` stderr.

Every heavy child process is started through `run()` so it is niced, can be cancelled, and is killed when the
engine shuts down.
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover - runtime contract says the venv has it
    FF = "ffmpeg"

_children = set()
_children_lock = threading.Lock()


class Cancelled(Exception):
    pass


class MediaError(Exception):
    pass


def _pidfile():
    from config import DATA
    d = os.path.join(os.environ.get("CLIPFORGE_DATA") or DATA, "run")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "children.txt")


def _persist():
    try:
        with open(_pidfile(), "w") as f:
            f.write("\n".join(str(p.pid) for p in _children))
    except OSError:
        pass


def _register(p):
    with _children_lock:
        _children.add(p)
        _persist()


def _unregister(p):
    with _children_lock:
        _children.discard(p)
        _persist()


def reap_orphans():
    """After a crash, children (ffmpeg / whisper worker) of the dead engine may still run and write into our work dirs.
    Kill the ones we recorded, but only if their command line is still ours."""
    killed = []
    try:
        pids = [int(x) for x in open(_pidfile()).read().split() if x.strip().isdigit()]
    except OSError:
        return killed
    for pid in pids:
        try:
            cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
        except OSError:
            continue
        if cmd and ("ffmpeg" in cmd or "whisper_worker" in cmd or "facedetect" in cmd):
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            killed.append(pid)
    try:
        os.remove(_pidfile())
    except OSError:
        pass
    return killed


def kill_all_children():
    with _children_lock:
        ps = list(_children)
    for p in ps:
        try:
            p.kill()
        except Exception:
            pass


GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workers", "guard.py")


def run(cmd, cancel=None, progress=None, duration=None, nice=True, timeout=None, cwd=None, stdout_lines=None, guard=True,
        status=None):
    """Run a command. `progress(fraction)` is fed from ffmpeg `-progress pipe:1` output (out_time_us) or from
    `PROGRESS <f>` lines (our workers). Returns (returncode, stderr_text). Raises Cancelled if `cancel` is set."""
    if nice:
        cmd = ["nice", "-n", "10"] + list(cmd)
    if guard and GUARD:
        cmd = [sys.executable, "-I", "-S", GUARD] + list(cmd)  # child dies with the engine (no orphan renders)
    errf = tempfile.TemporaryFile()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL, cwd=cwd,
                         start_new_session=True)
    _register(p)
    t0 = time.time()
    try:
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if stdout_lines is not None:
                stdout_lines.append(line)
            if status and line.startswith("STATUS "):
                status(line[7:])
            if progress:
                if line.startswith("out_time_us=") and duration:
                    try:
                        us = int(line.split("=", 1)[1])
                        progress(max(0.0, min(1.0, us / 1e6 / duration)))
                    except ValueError:
                        pass
                elif line.startswith("PROGRESS "):
                    try:
                        progress(float(line.split()[1]))
                    except (ValueError, IndexError):
                        pass
            if cancel is not None and cancel.is_set():
                _terminate(p)
                raise Cancelled()
            if timeout and time.time() - t0 > timeout:
                _terminate(p)
                raise MediaError(f"timed out after {timeout}s")
        while p.poll() is None:
            if cancel is not None and cancel.is_set():
                _terminate(p)
                raise Cancelled()
            time.sleep(0.05)
        errf.seek(0)
        err = errf.read().decode("utf-8", "replace")
        return p.returncode, err
    finally:
        _unregister(p)
        errf.close()
        try:
            p.stdout.close()
        except Exception:
            pass


def _terminate(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except Exception:
        try:
            p.terminate()
        except Exception:
            pass
    try:
        p.wait(timeout=3)
    except Exception:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            p.kill()


def ffmpeg(args, **kw):
    """Run ffmpeg with progress reporting; raise MediaError with the tail of stderr on failure."""
    cmd = [FF, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats"] + list(args)
    rc, err = run(cmd, **kw)
    if rc != 0:
        tail = "\n".join(l for l in err.strip().splitlines()[-6:])
        raise MediaError(f"ffmpeg failed ({rc}): {tail}")
    return err


# ---------------------------------------------------------------------------------------------- probe
_DUR = re.compile(r"Duration: (\d+):(\d+):([\d.]+)")
_STREAM = re.compile(r"Stream #0:(\d+)(?:\[[^\]]*\])?(?:\([^)]*\))?: (Video|Audio|Data|Subtitle): (.*)")


def probe(path):
    """Return {duration, width, height, fps, has_audio, audio_index, rotation, transfer, vcodec, pix_fmt, streams}.
    width/height are DISPLAY dimensions (rotation applied). audio_index is the N in `0:a:N` of the first
    decodable audio stream (iPhone spatial-audio 'apac' tracks are not decodable and are skipped)."""
    if not os.path.exists(path):
        raise MediaError(f"file not found: {path}")
    r = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace")
    err = r.stderr
    if "Invalid data found" in err or "moov atom not found" in err:
        raise MediaError("file is not a readable video (" + err.strip().splitlines()[-1] + ")")
    out = {"duration": None, "width": 0, "height": 0, "fps": 0.0, "has_audio": False, "audio_index": None,
           "rotation": 0, "transfer": None, "vcodec": None, "pix_fmt": None, "streams": []}
    m = _DUR.search(err)
    if m:
        out["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    a_n = 0
    lines = err.splitlines()
    vseen = False
    for i, line in enumerate(lines):
        s = _STREAM.search(line)
        if not s:
            rot = re.search(r"rotation of (-?[\d.]+) degrees", line)
            if rot and vseen and out["rotation"] == 0:
                out["rotation"] = int(round(float(rot.group(1))))
            continue
        kind, desc = s.group(2), s.group(3)
        out["streams"].append({"index": int(s.group(1)), "kind": kind.lower(), "desc": desc[:120]})
        if kind == "Video" and not vseen and "attached pic" not in desc:
            vseen = True
            mm = re.search(r", (\d{2,5})x(\d{2,5})", desc)
            if mm:
                out["width"], out["height"] = int(mm.group(1)), int(mm.group(2))
            f = re.search(r"([\d.]+) fps", desc) or re.search(r"([\d.]+) tbr", desc)
            if f:
                out["fps"] = float(f.group(1))
            out["vcodec"] = desc.split()[0]
            pf = re.search(r"\), (yuv\w+|rgb\w+|nv12|p010\w*)", desc) or re.search(r", (yuv\w+)", desc)
            if pf:
                out["pix_fmt"] = pf.group(1)
            if "arib-std-b67" in desc:
                out["transfer"] = "hlg"
            elif "smpte2084" in desc:
                out["transfer"] = "pq"
        elif kind == "Audio":
            decodable = not desc.startswith("none")
            if decodable and out["audio_index"] is None:
                out["audio_index"] = a_n
                out["has_audio"] = True
            a_n += 1
    if out["rotation"] in (90, -90, 270, -270):
        out["width"], out["height"] = out["height"], out["width"]
    if not vseen:
        raise MediaError("no video stream found")
    return out


def sdr_filter(pr):
    """Filter chain that converts HDR (HLG/PQ, e.g. iPhone Dolby Vision 8.4) to SDR bt709 8-bit; '' for SDR.
    Apply AFTER downscaling so the float tonemap runs on few pixels."""
    t = pr.get("transfer")
    if t not in ("hlg", "pq"):
        return ""
    tin = "arib-std-b67" if t == "hlg" else "smpte2084"
    return (f"zscale=tin={tin}:min=bt2020nc:pin=bt2020:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            f"tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def vmap(pr):
    return "0:v:0"


def amap(pr):
    return f"0:a:{pr['audio_index']}" if pr.get("audio_index") is not None else None


def extract_audio(src, pr, wav_out, cancel=None, progress=None):
    """Mono 16 kHz PCM WAV, written to a temp name and renamed so a half-written file is never 'valid'."""
    if pr.get("audio_index") is None:
        raise MediaError("source has no decodable audio track")
    tmp = f"{wav_out}.{os.getpid()}.part.wav"
    ffmpeg(["-i", src, "-map", amap(pr), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp],
           cancel=cancel, progress=progress, duration=pr["duration"])
    os.replace(tmp, wav_out)
    return wav_out


def wav_duration(path):
    import wave
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def grab_frame(src, t, out_jpg, pr, width=720, height=None, cancel=None):
    """One JPG at time t (fast seek)."""
    sc = f"scale={width}:{height}" if height else f"scale={width}:-2"
    chain = sc + ("," + sdr_filter(pr) if sdr_filter(pr) else "")
    tmp = f"{out_jpg}.{os.getpid()}.{threading.get_ident()}.part.jpg"
    ffmpeg(["-ss", f"{max(0.0, t):.3f}", "-i", src, "-map", vmap(pr), "-frames:v", "1", "-vf", chain, "-q:v", "3", tmp],
           cancel=cancel)
    os.replace(tmp, out_jpg)
    return out_jpg


def metadata_tags(path):
    """Container-level metadata tags (title/comment/artist…) from `ffmpeg -i`."""
    r = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace")
    tags = {}
    in_meta = False
    for line in r.stderr.splitlines():
        if line.startswith("  Metadata:"):
            in_meta = True
            continue
        if in_meta:
            m = re.match(r"\s{4}(\w[\w.-]*)\s*: (.*)", line)
            if m:
                tags[m.group(1).lower()] = m.group(2)
                continue
            in_meta = False
            if line.startswith("  Duration"):
                break
    return tags


def write_json_atomic(path, obj):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.part"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default
