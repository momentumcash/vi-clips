"""Boundary snapping: clip edges land on word edges and real pauses — never mid-word.

start: at most PRE_PAD (0.15 s) before the first word, never inside the previous word.
end:   the payoff's last word + a 0.25–0.60 s reaction tail, cut short only by the next word (never into it).
If the snapped length misses the target band, try to extend with neighbouring phrases of the same conversation;
otherwise reject with a reason.
"""
from config import BAND_TOLERANCE

PRE_PAD = 0.15
TAIL_MIN, TAIL_MAX = 0.25, 0.60
SAME_CONVO_GAP = 1.5


FILLERS = {"okay", "ok", "so", "um", "uh", "umm", "uhh", "erm", "like", "alright"}


def _bare(w):
    import re
    return re.sub(r"[^a-z']", "", (w or "").lower())


SHORT_FLOOR = 7.0  # seconds; below the band but still a real clip


class SnapReject(Exception):
    pass


def _first_word_at(words, t):
    """Index of the first word whose midpoint is at/after t - 0.05."""
    for i, w in enumerate(words):
        if (w["start"] + w["end"]) / 2 >= t - 0.05:
            return i
    return None


def _last_word_before(words, t):
    """Index of the last word whose midpoint is at/before t + 0.05."""
    idx = None
    for i, w in enumerate(words):
        if (w["start"] + w["end"]) / 2 <= t + 0.05:
            idx = i
        else:
            break
    return idx


def edges(words, i0, i1, duration):
    """Snapped (start, end) for the word index range [i0, i1]."""
    w0, w1 = words[i0], words[i1]
    prev_end = words[i0 - 1]["end"] if i0 > 0 else 0.0
    next_start = words[i1 + 1]["start"] if i1 + 1 < len(words) else (duration or w1["end"] + 1.0)
    start = max(prev_end, w0["start"] - PRE_PAD, 0.0)
    # tail: aim for TAIL_MAX of reaction, but never into the next word
    end = min(w1["end"] + TAIL_MAX, next_start - 0.02)
    end = max(end, w1["end"])  # if the next word starts immediately we end exactly on the word edge
    if duration:
        end = min(end, duration)
    return round(start, 3), round(end, 3)


def is_mid_word(words, t):
    return any(w["start"] + 1e-3 < t < w["end"] - 1e-3 for w in words)


SENTENCE_GAP = 0.35
SENTENCE_BACK_S = 6.0


def is_sentence_start(words, j):
    """Word j opens a sentence: first word, previous word ends with . ? !, or a real pause (≥ 0.35 s) before it."""
    if j <= 0:
        return True
    return words[j - 1]["word"].rstrip()[-1:] in ".?!" or words[j]["start"] - words[j - 1]["end"] >= SENTENCE_GAP


def sentence_start_before(words, i0, limit_s=SENTENCE_BACK_S):
    """Nearest sentence start at or before word i0, at most limit_s earlier; None if there is none."""
    j = i0
    while True:
        if is_sentence_start(words, j):
            return j
        if j == 0 or words[i0]["start"] - words[j - 1]["start"] > limit_s:
            return None
        j -= 1


def snap_time(words, t):
    """A manual edit point inside a word moves to that word's nearest edge; points in gaps are kept."""
    for w in words:
        if w["start"] + 1e-3 < t < w["end"] - 1e-3:
            return round(w["start"] if t - w["start"] <= w["end"] - t else w["end"], 3)
    return round(t, 3)


def snap(words, raw_start, raw_end, band, duration):
    """Returns dict(start, end, i0, i1, notes). Raises SnapReject."""
    if not words:
        raise SnapReject("no words in transcript")
    lo, hi = band
    lo_t, hi_t = lo - BAND_TOLERANCE, hi + BAND_TOLERANCE
    i0 = _first_word_at(words, raw_start)
    i1 = _last_word_before(words, raw_end)
    if i0 is None or i1 is None or i1 < i0:
        raise SnapReject(f"no words between {raw_start:.1f}s and {raw_end:.1f}s")
    notes = []
    # never open mid-sentence: walk back to the sentence start (or a real pause) if the band allows it
    if not is_sentence_start(words, i0):
        j = sentence_start_before(words, i0)
        if j is not None and edges(words, j, i1, duration)[1] - edges(words, j, i1, duration)[0] <= hi_t:
            notes.append(f"start moved back {words[i0]['start'] - words[j]['start']:.1f}s to the sentence start")
            i0 = j
        else:
            notes.append("starts mid-sentence (no sentence start within reach of the band)")
    start, end = edges(words, i0, i1, duration)
    # too short: add the preceding sentence(s) of the same conversation (more setup). Never stretch past the payoff —
    # the clip ends on the payoff line plus its reaction tail.
    while end - start < lo_t:
        prv = i0 - 1
        if prv >= 0 and words[i0]["start"] - words[prv]["end"] <= SAME_CONVO_GAP:
            j = prv
            while j - 1 >= 0 and words[j - 1]["word"][-1:] not in ".?!" and words[j]["start"] - words[j - 1]["end"] <= SAME_CONVO_GAP:
                j -= 1
            i0 = j
            notes.append("extended start back a sentence to reach the target band")
        elif end - start >= SHORT_FLOOR:  # a tight reaction (streams): keep it short rather than throw it away
            notes.append(f"kept short ({end - start:.1f}s): no more setup to add")
            break
        else:
            raise SnapReject(f"too short for the {lo:.0f}-{hi:.0f}s band ({end - start:.1f}s) and there is no more setup to add")
        start, end = edges(words, i0, i1, duration)
    # drop leading filler ("Okay,", "So", "Um") when a real sentence follows
    k = i0
    while k < i1 and _bare(words[k]["word"]) in FILLERS and words[k + 1]["start"] - words[k]["end"] <= 0.6:
        k += 1
    if k != i0:
        s2, e2 = edges(words, k, i1, duration)
        if e2 - s2 >= lo_t:
            notes.append(f"trimmed leading filler: {' '.join(w['word'] for w in words[i0:k])}")
            i0, start, end = k, s2, e2
    if end - start > hi_t:
        raise SnapReject(f"too long for the {lo:.0f}-{hi:.0f}s band ({end - start:.1f}s) without cutting the setup or payoff")
    assert not is_mid_word(words, start) and not is_mid_word(words, end)
    return {"start": start, "end": end, "i0": i0, "i1": i1, "notes": notes}
