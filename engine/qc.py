"""QC every render: one decode pass (blackdetect + ebur128 + volumedetect) plus metadata/caption/duplicate checks.

Blocking checks decide `qc.pass`. The export-rights check is informational for rendering (private review is allowed)
and is enforced at export time.
"""
import re

import captions as capm
import media
from config import BAND_TOLERANCE, EXPORT_RIGHTS, TARGETS


def analyse(mp4, cancel=None):
    rc, err = media.run([media.FF, "-hide_banner", "-nostdin", "-v", "info", "-i", mp4,
                         "-vf", "blackdetect=d=0.3:pix_th=0.10", "-af", "ebur128=framelog=quiet,volumedetect",
                         "-f", "null", "-"], cancel=cancel)
    out = {"rc": rc, "decode_errors": [], "black": [], "lufs": None, "mean_db": None}
    for line in err.splitlines():
        if re.search(r"(error|corrupt|invalid|non-existing)", line, re.I) and "blackdetect" not in line:
            out["decode_errors"].append(line.strip()[:200])
        m = re.search(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", line)
        if m:
            out["black"].append([float(m.group(1)), float(m.group(2)), float(m.group(3))])
        m = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", line)
        if m:
            out["mean_db"] = -200.0 if m.group(1) == "-inf" else float(m.group(1))
    m = re.findall(r"\bI:\s+(-?[\d.]+|-inf) LUFS", err)
    if m:
        out["lufs"] = -200.0 if m[-1] == "-inf" else float(m[-1])
    return out


def run_qc(app, clip, source, creator, settings, cancel=None):
    path = app.storage.path(clip["file_key"]) if clip.get("file_key") else None
    checks = []

    def add(name, ok, detail, blocking=True):
        checks.append({"name": name, "pass": bool(ok), "detail": detail, "blocking": blocking})

    import os
    if not path or not os.path.exists(path):
        add("file_exists", False, "rendered file is missing")
        return {"pass": False, "checks": checks}
    try:
        pr = media.probe(path)
    except media.MediaError as e:
        add("decodes", False, str(e))
        return {"pass": False, "checks": checks}
    a = analyse(path, cancel=cancel)
    add("decodes", a["rc"] == 0 and not a["decode_errors"],
        "decoded end-to-end" if a["rc"] == 0 and not a["decode_errors"] else "; ".join(a["decode_errors"][:3]) or f"ffmpeg rc {a['rc']}")
    add("size_1080x1920", (pr["width"], pr["height"]) == (1080, 1920), f"{pr['width']}x{pr['height']}")
    plan = clip["end_s"] - clip["start_s"]
    dur = pr["duration"] or 0
    add("duration_matches_plan", abs(dur - plan) <= 0.1, f"{dur:.2f}s vs plan {plan:.2f}s")
    target = (settings or {}).get("target", "auto")
    lo, hi = TARGETS.get(target, TARGETS["auto"])
    if clip.get("variant_of") or clip.get("manual_range"):
        lo = min(lo, 5.0)
    add("in_target_band", lo - BAND_TOLERANCE <= dur <= hi + BAND_TOLERANCE, f"{dur:.1f}s in {target} ({lo:.0f}-{hi:.0f}s ±{BAND_TOLERANCE:.0f})")
    if pr["has_audio"]:
        md = a["mean_db"]
        add("audio_not_silent", md is not None and md > -50, f"mean {md} dB")
        lufs = a["lufs"]
        add("loudness_-16_to_-12", lufs is not None and -16 <= lufs <= -12, f"integrated {lufs} LUFS")
    else:
        add("audio_present", False, "no audio stream in the render")
    long_black = [b for b in a["black"] if b[2] > 0.3]
    add("no_black_gaps", not long_black, "none" if not long_black else
        "; ".join(f"{b[0]:.2f}-{b[1]:.2f}s" for b in long_black[:4]))
    bad = capm.check_timing(clip.get("captions") or [], plan)
    add("captions_timing", not bad, "ok" if not bad else "; ".join(bad[:4]))
    if not clip.get("captions"):
        checks[-1]["detail"] = "no captions (no speech in range)"
    preset = capm.load_presets().get(clip.get("caption_preset") or "", None)
    if preset:
        sbad = capm.check_safe(clip.get("captions") or [], preset)
        add("captions_safe_zone", not sbad, "inside 55–72% centre band, ≤86% width" if not sbad else "; ".join(sbad[:3]))
    add("not_duplicate", not clip.get("duplicate_of"), "unique" if not clip.get("duplicate_of") else f"duplicate of #{clip['duplicate_of']}")
    tags = media.metadata_tags(path)
    want = f"Source: {creator['name'] if creator else '?'} — {source['title']}"
    add("attribution_metadata", tags.get("comment") == want, f"comment = {tags.get('comment')!r}")
    rights = source.get("rights_status")
    add("export_rights", rights in EXPORT_RIGHTS, f"rights_status '{rights}'" + ("" if rights in EXPORT_RIGHTS else
        " — rendering for private review is allowed; export is blocked until rights are owned/licensed/permission_granted"),
        blocking=False)
    ok = all(c["pass"] for c in checks if c["blocking"])
    return {"pass": ok, "checks": checks, "measured": {"lufs": a["lufs"], "mean_db": a["mean_db"], "duration": dur}}
