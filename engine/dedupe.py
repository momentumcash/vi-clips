"""Dedupe: overlapping ranges (IoU > 0.3) or the same conversation (> 50 % shared transcript tokens) → keep the higher
overall, mark the other duplicate_of."""
import re

STOP = set("the a an and or but so to of in on at for is it its it's i you he she we they that this was were be been "
           "are am do did does have has had not no yes yeah like just um uh oh okay ok really very with as my your our "
           "me him her them what when where who how why if then there here".split())


def iou(a, b):
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in STOP and len(w) > 2}


def shared(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def dedupe(items, key_range=lambda x: (x["start"], x["end"]), key_text=lambda x: x.get("text", ""),
           key_score=lambda x: x["overall"], iou_th=0.3, tok_th=0.5):
    """items: list of dicts. Returns {index: index_of_kept_duplicate} for the ones to mark as duplicates."""
    order = sorted(range(len(items)), key=lambda i: -key_score(items[i]))
    kept, dup = [], {}
    for i in order:
        hit = None
        for k in kept:
            if iou(key_range(items[i]), key_range(items[k])) > iou_th or shared(key_text(items[i]), key_text(items[k])) > tok_th:
                hit = k
                break
        if hit is None:
            kept.append(i)
        else:
            dup[i] = hit
    return dup
