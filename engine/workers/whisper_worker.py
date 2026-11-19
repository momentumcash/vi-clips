#!/usr/bin/env python3
"""Whisper worker (separate process so it can be niced, killed and resumed).

Usage: whisper_worker.py <audio.wav> <out_dir> <model> [duration] [prompt.json]
prompt.json (optional): {"initial_prompt": str, "hotwords": str, "sig": str} — names/vocabulary that bias recognition
(initial_prompt conditions the first window; hotwords are applied to every window).
Appends finished segments to <out_dir>/transcript.partial.jsonl as they complete. On start it reads that file and
resumes from the end of the last completed segment (faster-whisper clip_timestamps), so a kill mid-transcribe loses at
most one segment. Prints `PROGRESS <0-1>` lines. Writes transcript.json (atomic) when the whole file is done.
"""
import json
import os
import sys


def merge_fragments(words):
    """Whisper splits hyphenated/clitic words into tokens like 'co' + '-producers' or '110' + '-year'. Glue tokens that
    start with '-' or an apostrophe, or are bare punctuation, onto the previous word."""
    out = []
    for w in words:
        t = w["word"]
        glue = t[:1] in "-'’%" or not any(ch.isalnum() for ch in t) or (
            t[:1] in ",." and t[1:2].isdigit() and bool(out) and out[-1]["word"][-1:].isdigit())
        if out and glue and w["start"] - out[-1]["end"] < 0.3:
            prev = out[-1]
            prev["word"] = prev["word"] + t
            prev["end"] = max(prev["end"], w["end"])
            prev["p"] = min(prev.get("p", 1.0), w.get("p", 1.0))
            continue
        out.append(dict(w))
    return out


# Optional GPU transcription: a whisper.cpp `whisper-server` (Metal) on this address. Nothing there -> CPU faster-whisper.
# Set VI_WHISPER_SERVER=off to never try it.
SERVER = os.environ.get("VI_WHISPER_SERVER") or os.environ.get("CLIPFORGE_WHISPER_SERVER") or "http://127.0.0.1:8879"
WIN = 150.0   # seconds of audio per request to the GPU server (keeps progress + resume granular)
EDGE = 2.0    # a segment ending this close to a window edge is redone by the next window


def _server_ok():
    import urllib.request
    if SERVER.lower() in ("off", "none", "0", ""):
        return False
    try:
        urllib.request.urlopen(SERVER + "/", timeout=2).read(64)
        return True
    except Exception:
        return False


def _post_window(wav, t0, t1, prompt_text):
    """Send audio [t0, t1) to whisper.cpp server (Metal GPU, large-v3-turbo) and return its verbose_json."""
    import io
    import urllib.request
    import uuid
    import wave
    with wave.open(wav, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        w.setpos(min(int(t0 * sr), w.getnframes()))
        frames = w.readframes(max(0, int((t1 - t0) * sr)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as o:
        o.setnchannels(ch)
        o.setsampwidth(sw)
        o.setframerate(sr)
        o.writeframes(frames)
    b = uuid.uuid4().hex
    fields = {"response_format": "verbose_json", "temperature": "0", "temperature_inc": "0.2"}
    if prompt_text:
        fields["prompt"] = prompt_text
    body = b""
    for k, v in fields.items():
        body += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"w.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
    body += buf.getvalue() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(SERVER + "/inference", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def transcribe_server(wav, duration, resume_at, prompt, part, segs):
    """GPU path: ~5x realtime with large-v3-turbo vs ~0.3-1x on CPU. Same record format as the CPU path."""
    import wave
    if not duration:
        with wave.open(wav, "rb") as w:
            duration = w.getnframes() / float(w.getframerate())
    ptxt = " ".join(x for x in (prompt.get("initial_prompt"), prompt.get("hotwords")) if x)[:600] or None
    t = resume_at
    print("STATUS Transcribing on GPU (whisper-server, large-v3-turbo)…", flush=True)
    while t < duration - 0.5:
        end = min(duration, t + WIN)
        final = end >= duration - 0.01
        d = _post_window(wav, t, end, ptxt)
        kept = []
        for s in d.get("segments") or []:
            ss, se = t + float(s["start"]), t + float(s["end"])
            if not final and se > end - EDGE:
                break
            text = (s.get("text") or "").strip()
            if not text or float(s.get("no_speech_prob", 0)) > 0.8:
                continue
            words = []
            for w in s.get("words") or []:
                ww = (w.get("word") or "").strip()
                if not ww or ww.startswith("[") or ww.startswith("<|"):
                    continue
                words.append({"word": ww, "start": round(t + float(w["start"]), 3),
                              "end": round(t + float(w["end"]), 3), "p": round(float(w.get("probability", 1.0)), 3)})
            kept.append({"start": round(ss, 3), "end": round(se, 3), "text": text,
                         "avg_logprob": round(float(s.get("avg_logprob", 0)), 4),
                         "no_speech_prob": round(float(s.get("no_speech_prob", 0)), 4), "words": words})
        with open(part, "a") as f:
            for rec in kept:
                f.write(json.dumps(rec) + "\n")
            f.flush()
        segs.extend(kept)
        nt = end if final else (kept[-1]["end"] if kept else end - EDGE)
        t = max(nt, t + 1.0)
        print(f"PROGRESS {min(0.999, t / duration):.4f}", flush=True)
    return d.get("language") if "d" in locals() else None


def _watch_parent():
    """Exit if the engine dies (we'd be re-parented to launchd): a crashed engine must not leave a worker writing."""
    import threading
    import time
    ppid = os.getppid()

    def loop():
        while True:
            time.sleep(1.0)
            if os.getppid() != ppid:
                os._exit(3)
    threading.Thread(target=loop, daemon=True).start()


def main():
    _watch_parent()
    wav, out_dir, model_name = sys.argv[1], sys.argv[2], sys.argv[3]
    duration = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    prompt = {}
    if len(sys.argv) > 5 and os.path.exists(sys.argv[5]):
        with open(sys.argv[5]) as f:
            prompt = json.load(f)
    psig = prompt.get("sig") or ""
    part = os.path.join(out_dir, "transcript.partial.jsonl")
    meta_p = os.path.join(out_dir, "transcript.partial.meta.json")
    segs = []
    if os.path.exists(part) and os.path.exists(meta_p):
        with open(meta_p) as f:
            meta = json.load(f)
        if meta.get("model") == model_name and meta.get("prompt_sig", "") == psig:
            with open(part) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        segs.append(json.loads(line))
                    except ValueError:
                        break  # torn last line from a kill: drop it
        else:
            segs = []
    if not segs:
        with open(part, "w"):
            pass
    with open(meta_p, "w") as f:
        json.dump({"model": model_name, "prompt_sig": psig}, f)
    # rewrite the partial file cleanly (drops a torn last line)
    with open(part, "w") as f:
        for s in segs:
            f.write(json.dumps(s) + "\n")
    resume_at = segs[-1]["end"] if segs else 0.0
    if resume_at:
        print(f"RESUME {resume_at:.2f} ({len(segs)} segments kept)", flush=True)

    language = None
    use_gpu = os.environ.get("CLIPFORGE_WHISPER_CPU") != "1" and _server_ok()
    if use_gpu and (not duration or resume_at < duration - 0.5):
        try:
            language = transcribe_server(wav, duration, resume_at, prompt, part, segs)
            resume_at = duration or resume_at
        except Exception as e:  # GPU server failed mid-way: CPU path resumes from the last saved segment
            print(f"STATUS GPU whisper failed ({e}); falling back to CPU", flush=True)
            use_gpu = False
            resume_at = segs[-1]["end"] if segs else 0.0
    if use_gpu:
        _finish(segs, model_name, language, duration, psig, prompt, out_dir, 0.0)
        return
    print(f"STATUS Loading model {model_name}…", flush=True)
    from faster_whisper import WhisperModel
    threads = max(2, min(6, (os.cpu_count() or 4) - 2))
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads, local_files_only=True)
    print(f"STATUS Transcribing with {model_name}…", flush=True)
    kw = dict(word_timestamps=True, beam_size=5, condition_on_previous_text=False, vad_filter=True,
              vad_parameters={"min_silence_duration_ms": 700})
    if prompt.get("initial_prompt"):
        kw["initial_prompt"] = prompt["initial_prompt"]
    if prompt.get("hotwords"):
        kw["hotwords"] = prompt["hotwords"]
    if resume_at > 0 and (not duration or resume_at < duration - 0.5):
        kw.pop("vad_filter")
        kw.pop("vad_parameters")
        kw["clip_timestamps"] = [resume_at]
    if not duration or resume_at < duration - 0.5:
        segments, info = model.transcribe(wav, **kw)
        language = info.language
        with open(part, "a") as f:
            for s in segments:
                if s.end <= resume_at + 0.01:
                    continue
                words = [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
                          "p": round(w.probability, 3)} for w in (s.words or []) if w.word.strip() and w.start >= resume_at - 0.01]
                rec = {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(),
                       "avg_logprob": round(s.avg_logprob, 4), "no_speech_prob": round(s.no_speech_prob, 4), "words": words}
                f.write(json.dumps(rec) + "\n")
                f.flush()
                segs.append(rec)
                if duration:
                    print(f"PROGRESS {min(0.999, s.end / duration):.4f}", flush=True)
    _finish(segs, model_name, language, duration, psig, prompt, out_dir, resume_at)


def _finish(segs, model_name, language, duration, psig, prompt, out_dir, resume_at):
    words = [w for s in segs for w in s["words"]]
    words.sort(key=lambda w: w["start"])
    words = merge_fragments(words)
    out = {"model": model_name, "language": language, "duration": duration, "prompt_sig": psig,
           "initial_prompt": prompt.get("initial_prompt"), "hotwords": prompt.get("hotwords"),
           "segments": [{k: s[k] for k in ("start", "end", "text", "avg_logprob", "no_speech_prob")} for s in segs],
           "words": words, "resumed_from": resume_at}
    tmp = os.path.join(out_dir, "transcript.json.part")
    with open(tmp, "w") as f:
        json.dump(out, f)
    os.replace(tmp, os.path.join(out_dir, "transcript.json"))
    print("PROGRESS 1.0", flush=True)


if __name__ == "__main__":
    main()
