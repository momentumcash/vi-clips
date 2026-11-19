"""The ten stages and their artifact validators, plus clip-level render / variants / edits.

Shared artifacts (work/<source_id>/): verify.json, probe.json, audio.wav, transcript.json (+ .partial.jsonl while
running), signals.json, frames_db.npy, strip/*.jpg. Job artifacts (work/<source_id>/job_<id>/): detect.json,
scored.json, dedupe.json, clip_<id>/ (reframe samples, .ass).
"""
import concurrent.futures as cf
import os
import shutil
import threading
import time

import numpy as np

import boundaries
import captions as capm
import db as dbm
import dedupe as dedupem
import detect as detectm
import llm
import media
import qc as qcm
import reframe
import render as renderm
import score as scorem
import signals as sigm
import transcribe as trm
from config import TARGETS, EXPORT_RIGHTS
from jobs import StageFatal, load_presets


# ---------------------------------------------------------------------------------------------- helpers
def _verify_payload(path):
    st = os.stat(path)
    return {"path": path, "size": st.st_size, "mtime": int(st.st_mtime)}


def _band(target):
    return TARGETS.get(target, TARGETS["auto"])


def clip_words(words, a, b):
    return [w for w in words if w["start"] >= a - 0.02 and w["end"] <= b + 0.02]


def make_captions(words, a, b, preset, keywords):
    ws = [dict(w, start=round(w["start"] - a, 3), end=round(w["end"] - a, 3)) for w in clip_words(words, a, b)]
    return capm.build_beats(ws, preset, b - a, keywords)


def excerpt(words, a, b):
    return " ".join(w["word"] for w in clip_words(words, a, b))


def attribution(source, creator):
    return f"Source: {creator['name'] if creator else '?'} — {source['title']}"


# ---------------------------------------------------------------------------------------------- stages
def stage_verify(ctx):
    p = ctx.src_path
    if not p or not os.path.exists(p):
        raise StageFatal(f"source file not found: {p} — reconnect the drive or re-import")
    with open(p, "rb") as f:
        f.read(1 << 20)
    rights = ctx.source.get("rights_status")
    if rights == "denied":
        raise StageFatal("rights_status is 'denied' — processing blocked")
    media.write_json_atomic(ctx.p("verify.json"), _verify_payload(p))
    note = "" if rights in EXPORT_RIGHTS else " (private review only — export blocked until rights are cleared)"
    return f"file ok · {os.path.getsize(p) / 1e6:.0f} MB · rights {rights}{note}"


def valid_verify(ctx):
    v = media.read_json(ctx.p("verify.json"))
    return bool(v) and v == _verify_payload(ctx.src_path)


def stage_probe(ctx):
    pr = media.probe(ctx.src_path)
    if not pr.get("duration"):
        raise StageFatal("could not read the duration")
    media.write_json_atomic(ctx.p("probe.json"), pr)
    ctx.app.db.update("sources", ctx.source["id"], {"duration_s": round(pr["duration"], 3), "width": pr["width"], "height": pr["height"],
                                                     "fps": pr["fps"], "has_audio": 1 if pr["has_audio"] else 0})
    if not ctx.source.get("thumb_key") or not ctx.app.storage.exists(ctx.source["thumb_key"]):
        from sources.local import make_source_thumb
        try:
            ctx.app.db.update("sources", ctx.source["id"], {"thumb_key": make_source_thumb(ctx.app, ctx.source["id"], ctx.src_path, pr)})
        except media.MediaError:
            pass
    hdr = f" · {pr['transfer'].upper()} → SDR" if pr.get("transfer") else ""
    return f"{pr['width']}x{pr['height']} · {pr['fps']:.2f} fps · {pr['duration'] / 60:.1f} min · audio {'yes' if pr['has_audio'] else 'no'}{hdr}"


def valid_probe(ctx):
    return valid_verify(ctx) and bool(media.read_json(ctx.p("probe.json")))


def stage_extract_audio(ctx):
    pr = ctx.probe()
    if not pr.get("has_audio"):
        raise StageFatal("no decodable audio track — ClipForge finds moments from speech, so this source can't be processed")
    media.extract_audio(ctx.src_path, pr, ctx.p("audio.wav"), cancel=ctx.cancel, progress=ctx.progress)
    return f"16 kHz mono WAV · {media.wav_duration(ctx.p('audio.wav')):.1f}s"


def valid_extract_audio(ctx):
    pr = ctx.probe()
    p = ctx.p("audio.wav")
    return bool(pr) and os.path.exists(p) and abs(media.wav_duration(p) - pr["duration"]) < 1.5


def stage_transcribe(ctx):
    pr = ctx.probe()
    model = ctx.settings["whisper_model"]
    prompt = trm.build_prompt(ctx.creator, ctx.source)
    old = media.read_json(ctx.p("transcript.json"))
    if old and (old.get("model") != model or old.get("prompt_sig", "") != prompt["sig"]):
        os.replace(ctx.p("transcript.json"), ctx.p(f"transcript.{old.get('model')}.{old.get('prompt_sig') or 'noprompt'}.json"))
    t0 = time.time()
    tr, resumed = trm.transcribe(ctx.p("audio.wav"), ctx.wd, model, pr["duration"], cancel=ctx.cancel,
                                 progress=lambda f: ctx.progress(f, f"{model} · {f * 100:.0f}% of audio"), prompt=prompt,
                                 status=lambda msg: ctx.progress(ctx.stage["progress"] if ctx.stage else 0.0, msg, force=True))
    for f in ("transcript.partial.jsonl", "transcript.partial.meta.json"):
        if os.path.exists(ctx.p(f)):
            os.remove(ctx.p(f))
    n = len(tr["words"])
    if n == 0:
        raise StageFatal("no speech detected in the audio")
    return f"{n} words · {model} · {time.time() - t0:.0f}s" + (f" · {resumed.lower()}" if resumed else "")


def valid_transcribe(ctx):
    tr = media.read_json(ctx.p("transcript.json"))
    return (bool(tr) and tr.get("model") == ctx.settings["whisper_model"] and len(tr.get("words", [])) > 0
            and tr.get("prompt_sig", "") == trm.build_prompt(ctx.creator, ctx.source)["sig"])


def stage_signals(ctx):
    pr = ctx.probe()
    tr = media.read_json(ctx.p("transcript.json"))
    sig, frames_db = sigm.compute(ctx.p("audio.wav"), tr["words"], pr["duration"], cancel=ctx.cancel,
                                  progress=lambda f: ctx.progress(0.35 * f, "audio loudness / bursts"))
    np.save(ctx.p("frames_db.npy.tmp.npy"), frames_db.astype(np.float32))
    os.replace(ctx.p("frames_db.npy.tmp.npy"), ctx.p("frames_db.npy"))
    strip = ctx.p("strip")
    if os.path.isdir(strip):
        shutil.rmtree(strip)  # inside data/work only
    sig["scene_cuts"] = sigm.scene_and_strip(ctx.src_path, pr, strip, cancel=ctx.cancel,
                                             progress=lambda f: ctx.progress(0.35 + 0.65 * f, "scene cuts + filmstrip"))
    media.write_json_atomic(ctx.p("signals.json"), sig)
    return (f"{len(sig['loud_bursts'])} loud bursts · {len(sig['laughter_like'])} laughter-like · {len(sig['pauses'])} pauses · "
            f"{len(sig['scene_cuts'])} scene cuts")


def valid_signals(ctx):
    s = media.read_json(ctx.p("signals.json"))
    return bool(s) and s.get("version") == sigm.VERSION and os.path.exists(ctx.p("frames_db.npy"))


def stage_detect(ctx):
    pr = ctx.probe()
    tr = media.read_json(ctx.p("transcript.json"))
    sig = media.read_json(ctx.p("signals.json"))
    try:
        provider = llm.get_provider(ctx.app.settings())
    except llm.LLMError as e:
        raise StageFatal(str(e))
    desc = f"“{ctx.source['title']}” by {ctx.creator['name'] if ctx.creator else 'unknown'}"
    if ctx.creator and ctx.creator.get("categories"):
        desc += f" (categories: {', '.join(ctx.creator['categories'])})"
    try:
        res = detectm.detect(provider, tr, sig, pr["duration"], ctx.settings["target"], int(ctx.settings["max_clips"]), desc,
                             cancel=ctx.cancel, log=lambda f, d: ctx.progress(f, d))
    except llm.Cancelled:
        raise media.Cancelled()
    except llm.LLMError as e:
        ctx.add_llm(getattr(e, "metas", []))
        raise
    except ValueError as e:
        raise StageFatal(str(e))
    ctx.add_llm(res["metas"])
    media.write_json_atomic(ctx.jp("detect.json"), res)
    cost = sum(float(m.get("cost_usd") or 0) for m in res["metas"])
    return (f"{len(res['candidates'])} verified moment(s), {len(res['discarded'])} discarded · {len(res['metas'])} LLM call(s) · "
            f"${cost:.3f}")


def valid_detect(ctx):
    return bool(media.read_json(ctx.jp("detect.json")))


def measure(src_path, pr, tr, frames_db, a, b, best_open, work, cancel=None):
    """Measured score inputs for [a, b]: pacing, clarity, open_frame → {key: (score, details, reason)}."""
    words = tr["words"]
    pac, pac_d = scorem.pacing_measured(words, a, b)
    lvl = sigm.clip_level_db(frames_db, a, b)
    cla, cla_d = scorem.clarity_measured(tr["segments"], lvl, a, b)
    try:
        infos = reframe.open_frames(src_path, pr, best_open, work, cancel=cancel)
    except media.MediaError:
        infos = []
    of, of_d = scorem.open_frame_min(infos)
    return {
        "pacing": (pac, pac_d, f"{pac_d['wps']} words/s, {pac_d['dead_air'] * 100:.0f}% dead air."),
        "clarity": (cla, cla_d, f"whisper logprob {cla_d['avg_logprob']}, no-speech {cla_d['no_speech']}, level {cla_d['level_db']} dBFS."),
        "open_frame": (of, of_d, (("black frame in the first second." if of_d.get("black") else
                                   f"weakest of {of_d.get('frames')} frames in the first second (at +{(of_d.get('t') or best_open) - best_open:.1f}s): "
                                   f"{'face' if of_d.get('face') else 'no face'}, sharpness {of_d.get('sharpness')}, luma {of_d.get('luma')}"
                                   + (", burned-in text a 9:16 crop would slice" if of_d.get("text_cut") else "") + ".")
                                  if of is not None else "")),
    }


def rescore(app, clip_id):
    """Recompute the measured keys for the clip's current range and re-blend with the model's scores."""
    from jobs import JobError
    c = app.db.get("clips", clip_id)
    if not c:
        raise JobError(404, "clip not found")
    source = app.db.get("sources", c["source_id"])
    wd = app.storage.path(f"work/{source['id']}")
    tr = media.read_json(os.path.join(wd, "transcript.json"))
    pr = media.read_json(os.path.join(wd, "probe.json"))
    if not tr or not pr or not os.path.exists(os.path.join(wd, "frames_db.npy")):
        raise JobError(409, "source not analysed", "transcript/signals missing", "process the source first")
    frames_db = np.load(os.path.join(wd, "frames_db.npy"))
    a, b = c["start_s"], c["end_s"]
    bo = c["best_open_s"] if c.get("best_open_s") is not None and a <= c["best_open_s"] < b else a
    measured = measure(app.source_path(source), pr, tr, frames_db, a, b, bo,
                       app.storage.ensure_dir(f"work/{source['id']}/clip_{clip_id}/open"))
    model = c.get("model_scores") or {}
    approx = not model
    if approx:  # clips scored before model_scores were stored: treat current scores as the model's
        model = {k: v for k, v in (c.get("scores") or {}).items()}
    reasons_in = {k: (r.split(" Measured: ")[0] if isinstance(r, str) else r) for k, r in (c.get("reasons") or {}).items()}
    scores, reasons, basis = scorem.blend(model, reasons_in, measured)
    f = {"scores": scores, "reasons": reasons, "score_basis": basis, "overall": scorem.overall(scores), "scores_stale": 0,
         "best_open_s": bo, "updated_at": dbm.now()}
    app.db.update("clips", clip_id, f)
    out = app.db.get("clips", clip_id)
    app.db.bus.publish("clip", app.clip_view(dict(out)))
    return out


def stage_score(ctx):
    pr = ctx.probe()
    tr = media.read_json(ctx.p("transcript.json"))
    det = media.read_json(ctx.jp("detect.json"))
    frames_db = np.load(ctx.p("frames_db.npy"))
    words = tr["words"]
    band = _band(ctx.settings["target"])
    scored, rejected = [], []
    cands = det["candidates"]
    for i, c in enumerate(cands):
        ctx.check()
        ctx.progress(i / max(1, len(cands)), f"scoring {i + 1}/{len(cands)}")
        try:
            sn = boundaries.snap(words, c["raw_start"], c["raw_end"], band, pr["duration"])
        except boundaries.SnapReject as e:
            rejected.append({"title": c.get("title"), "reason": str(e), "raw": [c["raw_start"], c["raw_end"]]})
            continue
        a, b = sn["start"], sn["end"]
        best_open = c.get("best_open_s", a)
        best_open = a if not (a <= best_open < b) else best_open
        measured = measure(ctx.src_path, pr, tr, frames_db, a, b, best_open, os.path.join(ctx.jd, "open"), cancel=ctx.cancel)
        scores, reasons, basis = scorem.blend(c.get("scores") or {}, c.get("reasons") or {}, measured)
        scored.append({**c, "start": a, "end": b, "snap_notes": sn["notes"], "best_open_s": best_open, "scores": scores,
                       "model_scores": c.get("scores") or {}, "model_reasons": c.get("reasons") or {},
                       "reasons": reasons, "score_basis": basis, "overall": scorem.overall(scores),
                       "measured": {k: v[1] for k, v in measured.items()},
                       "text": excerpt(words, a, b)})
    media.write_json_atomic(ctx.jp("scored.json"), {"scored": scored, "rejected": rejected})
    return f"{len(scored)} scored · {len(rejected)} rejected at snapping"


def valid_score(ctx):
    return bool(media.read_json(ctx.jp("scored.json")))


BELOW_BAR_N = 2
BELOW_BAR_FLOOR = 35
CLIP_FIELDS = ("title", "description", "hook", "payoff", "category", "concerns", "keywords")


def stage_dedupe(ctx):
    app = ctx.app
    sc = media.read_json(ctx.jp("scored.json"))["scored"]
    tr = media.read_json(ctx.p("transcript.json"))
    presets = load_presets()
    preset_name = ctx.settings["caption_preset"]
    preset = presets[preset_name]
    dups = dedupem.dedupe(sc)
    min_score = float(ctx.settings["min_score"])
    max_clips = int(ctx.settings["max_clips"])
    # remove clip rows this job created earlier (only un-rendered ones can exist before the render stage)
    for old in app.db.query("clips", "SELECT id, file_key FROM clips WHERE job_id=?", (ctx.job["id"],)):
        app.db.delete("clips", old["id"])
    order = sorted(range(len(sc)), key=lambda i: -sc[i]["overall"])
    # nothing clears the bar → still render the best 1–2 (non-duplicates), clearly flagged below_bar
    cleared = [i for i in order if i not in dups and sc[i]["overall"] >= min_score]
    fallback = [] if cleared else [i for i in order if i not in dups and sc[i]["overall"] >= BELOW_BAR_FLOOR][:BELOW_BAR_N]
    ids = {}
    rank = 0
    now = dbm.now()
    for i in order + [i for i in dups]:  # kept first so duplicates can point at them
        if i in ids:
            continue
        c = sc[i]
        if i in dups and dups[i] not in ids:
            continue
        status, reason, dup_of = "candidate", None, None
        if i in dups:
            status, reason, dup_of = "rejected", f"duplicate of clip #{ids[dups[i]]}", ids[dups[i]]
        elif c["overall"] < min_score and i not in fallback:
            status, reason = "rejected", f"score {c['overall']} below min_score {min_score:.0f}"
        elif rank >= max_clips:
            status, reason = "rejected", f"beyond max_clips ({max_clips}); score {c['overall']}"
        if status == "candidate":
            rank += 1
        caps = make_captions(tr["words"], c["start"], c["end"], preset, c.get("keywords") or []) if status == "candidate" else []
        row = {k: c.get(k) for k in CLIP_FIELDS}
        row.update({"source_id": ctx.source["id"], "job_id": ctx.job["id"], "rank": rank if status == "candidate" else None,
                    "start_s": c["start"], "end_s": c["end"], "needs_context": 1 if c.get("needs_context") else 0,
                    "context_note": c.get("context_note") or "", "best_open_s": c["best_open_s"], "scores": c["scores"],
                    "reasons": c["reasons"], "score_basis": c["score_basis"], "overall": c["overall"],
                    "concerns": c.get("concerns") or [], "caption_preset": preset_name, "captions": caps, "status": status,
                    "transcript_excerpt": c["text"][:4000], "duplicate_of": dup_of, "reject_reason": reason,
                    "keywords": c.get("keywords") or [], "crop": None, "created_at": now, "updated_at": now,
                    "model_scores": c.get("model_scores") or {}, "below_bar": 1 if i in fallback else 0,
                    "below_bar_reason": (f"score {c['overall']} is below min_score {min_score:.0f}; rendered anyway because "
                                         f"nothing in this source cleared the bar") if i in fallback else None})
        ids[i] = app.db.insert("clips", row)
    media.write_json_atomic(ctx.jp("dedupe.json"), {"clip_ids": list(ids.values()), "selected": rank, "duplicates": len(dups)})
    app.db.bus.publish("clips", {"source_id": ctx.source["id"], "job_id": ctx.job["id"], "count": len(ids)})
    return (f"{rank} selected" + (f" ({len(fallback)} below bar — nothing cleared {min_score:.0f})" if fallback else "")
            + f" · {len(dups)} duplicate(s) · {len(ids) - rank - len(dups)} rejected")


def valid_dedupe(ctx):
    d = media.read_json(ctx.jp("dedupe.json"))
    if not d:
        return False
    n = ctx.app.db.scalar("SELECT COUNT(*) FROM clips WHERE job_id=?", (ctx.job["id"],))
    return n == len(d["clip_ids"])


def _job_clips(app, job_id, statuses):
    q = ",".join("?" * len(statuses))
    return app.db.query("clips", f"SELECT * FROM clips WHERE job_id=? AND variant_of IS NULL AND status IN ({q}) ORDER BY rank", (job_id, *statuses))


def with_layout(settings, creator):
    """Streamers (YouTube/Twitch/Kick sources): full 16:9 frame over a blurred fill, close-ups only on reactions."""
    plat = (creator or {}).get("platform") or ""
    return dict(settings, layout="stream" if plat in ("youtube", "twitch", "kick") else settings.get("layout", "crop"))


def render_clip(app, clip, source, creator, pr, job_settings, cancel=None, progress=None, stopping=None):
    """Reframe (if needed) → captions (if missing) → render → thumbnail → QC. Updates the clip row."""
    job_settings = with_layout(job_settings, creator)
    presets = load_presets()
    preset_name = clip.get("caption_preset") or job_settings.get("caption_preset") or "bold_entertainment"
    preset = presets.get(preset_name) or presets["bold_entertainment"]
    src = app.source_path(source)
    work = app.storage.ensure_dir(f"work/{source['id']}/clip_{clip['id']}")
    crop = clip.get("crop")
    if job_settings.get("layout") == "stream":
        # split layout (face cam on top) needs the streamer's face box; find it once per clip range
        need = not (crop and "cam_box" in crop and crop.get("range") == [clip["start_s"], clip["end_s"]])
    else:
        need = not crop or (crop.get("mode") == "auto" and crop.get("range") != [clip["start_s"], clip["end_s"]])
    if need:
        sig = media.read_json(os.path.join(app.storage.path(f"work/{source['id']}"), "signals.json")) or {}
        crop = reframe.analyze(src, pr, clip["start_s"], clip["end_s"], os.path.join(work, "samples"), cancel=cancel,
                               progress=(lambda f: progress(0.3 * f)) if progress else None,
                               scene_cuts=[c["t"] for c in sig.get("scene_cuts", [])])
        crop["range"] = [clip["start_s"], clip["end_s"]]
        clip["crop"] = crop
        upd = {"crop": crop}
        concerns = [x for x in (clip.get("concerns") or []) if not str(x).startswith("burned-in text")]
        if crop.get("text_cut"):
            concerns.append(f"burned-in text near the crop edge at {', '.join(f'{t:.1f}s' for t in crop['text_cut'])} "
                            "(heuristic edge check, no OCR) — those spans are rendered fit-to-width over a blurred fill")
        if concerns != (clip.get("concerns") or []):
            clip["concerns"] = upd["concerns"] = concerns
        app.db.update("clips", clip["id"], upd)
    if not clip.get("captions") and not clip.get("captions_edited"):
        tr = load_transcript_file(app, source["id"])
        if tr:
            clip["captions"] = make_captions(tr["words"], clip["start_s"], clip["end_s"], preset, clip.get("keywords") or [])
            app.db.update("clips", clip["id"], {"captions": clip["captions"]})
    key = f"clips/{clip['id']}/clip.mp4"
    out = app.storage.path(key)
    renderm.render(src, pr, clip, preset_name, preset, job_settings, attribution(source, creator), out, work, cancel=cancel,
                   progress=(lambda f: progress(0.3 + 0.6 * f)) if progress else None)
    tkey = f"clips/{clip['id']}/thumb.jpg"
    renderm.thumbnail(out, (clip.get("best_open_s") or clip["start_s"]) - clip["start_s"] + 0.15, app.storage.path(tkey), cancel=cancel)
    sig = renderm.signature(clip, preset_name, job_settings)
    clip.update(file_key=key, thumb_key=tkey, render_sig=sig, caption_preset=preset_name)
    q = qcm.run_qc(app, clip, source, creator, job_settings, cancel=cancel)
    status = "rendered" if q["pass"] else "failed"
    err = None if q["pass"] else "QC failed: " + "; ".join(f"{c['name']} ({c['detail']})" for c in q["checks"] if c["blocking"] and not c["pass"])
    app.db.update("clips", clip["id"], {"file_key": key, "thumb_key": tkey, "render_sig": sig, "qc": q, "status": status,
                                        "caption_preset": preset_name, "error": err, "updated_at": dbm.now()})
    if progress:
        progress(1.0)
    view = app.clip_view(app.db.get("clips", clip["id"]))
    app.db.bus.publish("clip", view)
    if q["pass"]:
        app.db.event("clip", f"Clip ready: “{clip.get('title')}” ({clip['end_s'] - clip['start_s']:.0f}s, score {clip.get('overall')})",
                     ref={"clip_id": clip["id"], "source_id": source["id"]})
    else:
        app.db.event("clip", f"Clip #{clip['id']} failed QC — {err}", level="warn", ref={"clip_id": clip["id"]})
    return q


def load_transcript_file(app, source_id):
    return media.read_json(os.path.join(app.storage.path(f"work/{source_id}"), "transcript.json"))


def _render_valid(app, clip, settings):
    if not clip.get("file_key") or not app.storage.exists(clip["file_key"]):
        return False
    return clip.get("render_sig") == renderm.signature(clip, clip.get("caption_preset"), settings) and clip.get("qc") is not None


def stage_render(ctx):
    app = ctx.app
    pr = ctx.probe()
    clips = _job_clips(app, ctx.job["id"], ("candidate", "rendering", "rendered", "failed", "approved"))
    todo = [c for c in clips if not _render_valid(app, c, ctx.settings)]
    if not clips:
        return "skipped — no moments cleared the bar"
    done = [0]
    lock = threading.Lock()
    part = {}

    def prog(cid):
        def f(x):
            with lock:
                part[cid] = x
                ctx.progress((done[0] + sum(part.values())) / max(1, len(todo)),
                             f"rendering {done[0] + len(part)}/{len(todo)} (2 at a time)")
        return f

    for c in todo:
        app.db.update("clips", c["id"], {"status": "rendering", "updated_at": dbm.now()})
    errors = []

    def one(c):
        with ctx.runner.render_slots:
            ctx.check()
            try:
                render_clip(app, c, ctx.source, ctx.creator, pr, ctx.settings, cancel=ctx.cancel, progress=prog(c["id"]))
            except (media.Cancelled, Exception) as e:  # noqa: BLE001
                if ctx.runner.stopping.is_set() or isinstance(e, media.Cancelled):
                    raise
                errors.append((c["id"], str(e)))
                app.db.update("clips", c["id"], {"status": "failed", "error": f"render failed: {e}", "updated_at": dbm.now()})
                app.db.bus.publish("clip", app.clip_view(app.db.get("clips", c["id"])))
            finally:
                with lock:
                    part.pop(c["id"], None)
                    done[0] += 1

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(one, c) for c in todo]
        for f in futs:
            f.result()
    ok = app.db.scalar("SELECT COUNT(*) FROM clips WHERE job_id=? AND status='rendered'", (ctx.job["id"],))
    if todo and len(errors) == len(todo):
        raise RuntimeError(f"all {len(todo)} renders failed: {errors[0][1][:300]}")
    return f"{len(todo)} rendered now ({len(clips) - len(todo)} reused) · {ok} passed QC" + (f" · {len(errors)} error(s)" if errors else "")


def valid_render(ctx):
    clips = _job_clips(ctx.app, ctx.job["id"], ("candidate", "rendering", "rendered", "failed", "approved"))
    return all(_render_valid(ctx.app, c, ctx.settings) or (c["status"] == "failed" and c.get("qc")) for c in clips)


def stage_qc(ctx):
    app = ctx.app
    clips = _job_clips(app, ctx.job["id"], ("rendered", "failed", "approved", "rendering", "candidate"))
    for c in clips:
        if c.get("qc") is None and c.get("file_key") and app.storage.exists(c["file_key"]):
            q = qcm.run_qc(app, c, ctx.source, ctx.creator, ctx.settings, cancel=ctx.cancel)
            app.db.update("clips", c["id"], {"qc": q, "status": "rendered" if q["pass"] else "failed", "updated_at": dbm.now()})
    clips = _job_clips(app, ctx.job["id"], ("rendered", "failed", "approved"))
    passed = sum(1 for c in clips if (c.get("qc") or {}).get("pass"))
    if not clips:
        return "skipped — nothing rendered"
    return f"{passed}/{len(clips)} passed QC"


def valid_qc(ctx):
    clips = _job_clips(ctx.app, ctx.job["id"], ("rendered", "failed", "approved", "rendering", "candidate"))
    return all(c.get("qc") is not None for c in clips)


VALID = {"verify": valid_verify, "probe": valid_probe, "extract_audio": valid_extract_audio, "transcribe": valid_transcribe,
         "signals": valid_signals, "detect": valid_detect, "score": valid_score, "dedupe": valid_dedupe, "render": valid_render,
         "qc": valid_qc}


# ---------------------------------------------------------------------------------------------- clip-level
def _clip_ctx(app, clip):
    source = app.db.get("sources", clip["source_id"])
    creator = app.db.get("creators", source["creator_id"]) if source else None
    job = app.db.get("jobs", clip["job_id"]) if clip.get("job_id") else None
    settings = (job or {}).get("settings") or {"target": "auto", "progress_bar": True}
    pr = media.read_json(os.path.join(app.storage.path(f"work/{source['id']}"), "probe.json")) or media.probe(app.source_path(source))
    return source, creator, settings, pr


def render_one(app, clip_id, slots=None, stopping=None):
    clip = app.db.get("clips", clip_id)
    if not clip:
        return
    source, creator, settings, pr = _clip_ctx(app, clip)
    cancel = threading.Event()
    if stopping is not None and stopping.is_set():
        return
    with (slots or threading.Semaphore(1)):
        def prog(f):
            app.db.bus.publish("clip_progress", {"clip_id": clip_id, "progress": round(f, 3)})
        render_clip(app, clip, source, creator, pr, settings, cancel=cancel, progress=prog, stopping=stopping)


def rehook_clip(app, clip_id):
    """Hook-only edit in seconds: lay the new pill over the kept base clip. Returns the clip view, or None if a full
    render is needed (no base yet, or something other than the hook changed)."""
    clip = app.db.get("clips", clip_id)
    if not clip or clip["status"] == "rendering":
        return None
    source, creator, settings, pr = _clip_ctx(app, clip)
    settings = with_layout(settings, creator)
    preset_name = clip.get("caption_preset") or settings.get("caption_preset") or "bold_entertainment"
    key = clip.get("file_key") or f"clips/{clip['id']}/clip.mp4"
    out = app.storage.path(key)
    work = app.storage.ensure_dir(f"work/{source['id']}/clip_{clip['id']}")
    if not renderm.rehook(clip, out, work, preset_name, settings):
        return None
    tkey = clip.get("thumb_key") or f"clips/{clip['id']}/thumb.jpg"
    renderm.thumbnail(out, (clip.get("best_open_s") or clip["start_s"]) - clip["start_s"] + 0.15, app.storage.path(tkey))
    sig = renderm.signature(clip, preset_name, settings)
    status = clip["status"] if clip["status"] in ("approved", "exported", "rendered") else "rendered"
    app.db.update("clips", clip["id"], {"file_key": key, "thumb_key": tkey, "render_sig": sig, "status": status, "updated_at": dbm.now()})
    view = app.clip_view(app.db.get("clips", clip["id"]))
    app.db.bus.publish("clip", view)
    return view


def make_variants(app, clip_id, runner):
    clip = app.db.get("clips", clip_id)
    source, creator, settings, pr = _clip_ctx(app, clip)
    tr = load_transcript_file(app, source["id"])
    if not tr:
        raise RuntimeError("no transcript for this source")
    phs = trm.phrases(tr["words"])
    band = (5.0, 90.0)
    provider = llm.get_provider(app.settings())
    opens, metas = detectm.variant_openings(provider, clip, phs, band)
    cost = sum(float(m.get("cost_usd") or 0) for m in metas)
    if clip.get("job_id"):
        j = app.db.get("jobs", clip["job_id"])
        if j:
            app.db.update("jobs", j["id"], {"cost_usd": round((j.get("cost_usd") or 0) + cost, 5)})
    if not opens:
        app.db.event("clip", f"No stronger alternate openings found for clip #{clip_id} (${cost:.3f})", ref={"clip_id": clip_id})
        app.db.bus.publish("variants", {"clip_id": clip_id, "created": []})
        return
    created = []
    words = tr["words"]
    for n, o in enumerate(opens, 1):
        try:
            sn = boundaries.snap(words, o["start"], clip["end_s"] - 0.4, (5.0, 120.0), pr["duration"])
        except boundaries.SnapReject:
            continue
        a, b = sn["start"], max(sn["end"], clip["end_s"])
        preset = load_presets().get(clip.get("caption_preset") or "bold_entertainment")
        row = {k: clip.get(k) for k in ("source_id", "job_id", "title", "description", "payoff", "needs_context", "context_note",
                                       "category", "scores", "reasons", "score_basis", "overall", "concerns", "caption_preset",
                                       "keywords")}
        row.update({"start_s": a, "end_s": b, "best_open_s": a, "hook": f"Alt open: {o['line']} — {o['why']}",
                    "title": f"{clip.get('title') or 'Clip'} (alt open {n})", "variant_of": clip_id, "status": "rendering",
                    "captions": make_captions(words, a, b, preset, clip.get("keywords") or []), "crop": None,
                    "transcript_excerpt": excerpt(words, a, b)[:4000], "rank": None, "created_at": dbm.now(), "updated_at": dbm.now()})
        row["reasons"] = dict(row.get("reasons") or {})
        row["reasons"]["hook"] = f"Alternate opening proposed by the model: {o['why']} (scores copied from the original clip; not re-judged)."
        cid = app.db.insert("clips", row)
        created.append(cid)
        runner.render_q.put(("render", cid))
    app.db.event("clip", f"{len(created)} alternate opening(s) for clip #{clip_id} queued for render (${cost:.3f})", ref={"clip_id": clip_id})
    app.db.bus.publish("variants", {"clip_id": clip_id, "created": created})


class PatchError(Exception):
    pass


def patch_clip(app, clip_id, body):
    from jobs import JobError
    c = app.db.get("clips", clip_id)
    if not c:
        raise JobError(404, "clip not found")
    if c["status"] == "rendering":
        raise JobError(409, "clip is rendering", "", "wait for the render to finish")
    source = app.db.get("sources", c["source_id"])
    dur_src = source.get("duration_s") or 1e9
    f = {}
    a = float(body.get("start_s", c["start_s"]))
    b = float(body.get("end_s", c["end_s"]))
    if "start_s" in body or "end_s" in body:
        tr0 = load_transcript_file(app, c["source_id"])
        if tr0:  # never cut mid-word, manual edits included
            a, b = boundaries.snap_time(tr0["words"], a), boundaries.snap_time(tr0["words"], b)
        if not (0 <= a < b <= dur_src + 0.01):
            raise JobError(400, "invalid range", f"{a:.2f}-{b:.2f} (source is {dur_src:.2f}s)", "keep 0 ≤ start < end ≤ duration")
        if b - a < 3 or b - a > 180:
            raise JobError(400, "clip length out of range", f"{b - a:.1f}s", "keep clips between 3 and 180 seconds")
        f.update(start_s=round(a, 3), end_s=round(b, 3), manual_range=1)
        if abs(a - c["start_s"]) > 1e-3 or abs(b - c["end_s"]) > 1e-3:
            f["scores_stale"] = 1  # measured keys describe the old range until POST /api/clips/:id/rescore
        if c.get("best_open_s") is None or not (a <= c["best_open_s"] < b):
            f["best_open_s"] = round(a, 3)
    if "title" in body:
        f["title"] = str(body["title"])[:200]
    if "context_note" in body:  # the top hook box (the VI Clips app edits it); empty string turns the box off
        note = str(body["context_note"] or "").strip()[:140]
        f["context_note"] = note
        f["needs_context"] = 1 if note else 0
    if "caption_preset" in body:
        if body["caption_preset"] not in load_presets():
            raise JobError(400, "unknown caption preset", str(body["caption_preset"]), f"use one of {', '.join(load_presets())}")
        f["caption_preset"] = body["caption_preset"]
    if "crop" in body:
        cr = body["crop"] or {}
        if cr.get("mode") == "manual":
            x = float(cr.get("x", 0.5))
            if not 0 <= x <= 1:
                raise JobError(400, "crop.x must be 0-1")
            f["crop"] = {"mode": "manual", "x": x, "track": [], "kind": "manual"}
        elif cr.get("mode") == "auto":
            f["crop"] = None  # recomputed at render
        else:
            raise JobError(400, "crop.mode must be 'auto' or 'manual'")
    preset_name = f.get("caption_preset", c.get("caption_preset") or "bold_entertainment")
    preset = load_presets()[preset_name]
    if "captions" in body:
        caps = body["captions"]
        if not isinstance(caps, list) or not all(isinstance(x, dict) and "start" in x and "end" in x and "text" in x for x in caps):
            raise JobError(400, "captions must be a list of {start, end, text, emphasis?}")
        caps = [{"start": round(float(x["start"]), 3), "end": round(float(x["end"]), 3), "text": str(x["text"]),
                 "emphasis": [int(i) for i in (x.get("emphasis") or [])]} for x in caps]
        bad = capm.check_timing(caps, b - a)
        if bad:
            raise JobError(400, "captions invalid", "; ".join(bad[:4]), "captions are seconds relative to the clip start; no overlaps, none empty")
        f["captions"] = caps
        f["captions_edited"] = 1
    elif ("start_s" in f or "end_s" in f or "caption_preset" in f):
        tr = load_transcript_file(app, c["source_id"])
        if c.get("captions_edited") and "caption_preset" not in f:
            # keep the user's text; shift to the new start and drop what falls outside
            d = c["start_s"] - a
            caps = [dict(x, start=round(x["start"] + d, 3), end=round(x["end"] + d, 3)) for x in (c.get("captions") or [])]
            f["captions"] = [x for x in caps if x["end"] > 0 and x["start"] < b - a]
            for x in f["captions"]:
                x["start"], x["end"] = max(0.0, x["start"]), min(b - a, x["end"])
        elif tr:
            f["captions"] = make_captions(tr["words"], a, b, preset, c.get("keywords") or [])
            f["captions_edited"] = 0
        if tr:
            f["transcript_excerpt"] = excerpt(tr["words"], a, b)[:4000]
    if "status" in body:
        st = body["status"]
        if st not in ("approved", "rejected", "candidate"):
            raise JobError(400, "status can be set to approved, rejected or candidate")
        if st == "approved" and c["status"] not in ("rendered", "approved", "exported"):
            raise JobError(409, "only rendered clips can be approved", f"clip is {c['status']}", "render it first")
        f["status"] = st
        if st == "rejected":
            f["reject_reason"] = "rejected by you"
    if f:
        f["updated_at"] = dbm.now()
        app.db.update("clips", clip_id, f)
    out = app.db.get("clips", clip_id)
    app.db.bus.publish("clip", app.clip_view(dict(out)))
    return out
