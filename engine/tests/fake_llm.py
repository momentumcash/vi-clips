"""TEST-ONLY fake LLM provider. It is never imported by the shipped engine — tests patch llm.get_provider with it.

It reads the phrases out of the real detection prompt and proposes deterministic, verifiable candidates: consecutive
phrase runs of ~18-26 s, citing the first/last phrase verbatim, so the code path (verification, snapping, scoring,
rendering) is exercised exactly as with a real model."""
import json
import os
import re

from transcribe import parse_ts


class FakeProvider:
    name = "fake_test_provider"

    def __init__(self, bad_first=False):
        self.calls = 0
        self.bad_first = bad_first

    def complete(self, prompt, system, cancel=None):
        self.calls += 1
        meta = {"provider": self.name, "model": "fake", "cost_usd": 0.0, "input_tokens": len(prompt) // 4, "output_tokens": 0}
        if self.bad_first and self.calls == 1:
            return "not json at all", meta
        if '"openings"' in prompt:
            return json.dumps({"openings": []}), meta
        phs = []
        for m in re.finditer(r"^\[(\d+:\d+\.\d)\] (.+)$", prompt.split("## Transcript", 1)[-1], re.M):
            phs.append((parse_ts(m.group(1)), m.group(1), m.group(2)))
        cands = []
        i = 0
        while i < len(phs) and len(cands) < 3:
            j = i
            while j + 1 < len(phs) and phs[j + 1][0] - phs[i][0] < 20:
                j += 1
            if phs[j][0] - phs[i][0] >= 12:
                sc = {k: int(os.environ.get("CLIPFORGE_FAKE_SCORE", "7")) for k in ("hook", "emotion", "curiosity", "entertainment", "standalone", "pacing", "clarity",
                                     "replay", "share", "open_frame")}
                cands.append({"start": phs[i][1], "end": phs[j][1], "title": f"Test moment {len(cands) + 1}",
                              "hook_line": f"[{phs[i][1]}] {phs[i][2]}", "payoff_line": f"[{phs[j][1]}] {phs[j][2]}",
                              "hook": "test", "payoff": "test", "description": "test", "needs_context": False,
                              "context_note": "", "best_open": phs[i][1], "category": "explainer", "keywords": ["seven"],
                              "scores": sc, "reasons": {k: "fake provider" for k in sc}, "concerns": [], "target": "15-30"})
            i = j + 2
        # one hallucinated candidate that must be discarded by verification
        if phs:
            cands.append({"start": phs[0][1], "end": phs[-1][1], "title": "Hallucinated", "hook_line": "[00:00.0] words never said",
                          "payoff_line": "[00:01.0] also invented", "hook": "", "payoff": "", "description": "",
                          "needs_context": False, "context_note": "", "best_open": phs[0][1], "category": "story",
                          "keywords": [], "scores": {k: 9 for k in ("hook", "emotion", "curiosity", "entertainment", "standalone",
                                                                     "pacing", "clarity", "replay", "share", "open_frame")},
                          "reasons": {}, "concerns": [], "target": "15-30"})
        return json.dumps({"candidates": cands, "passed_over": "fake"}), meta
