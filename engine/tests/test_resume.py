"""Kill the engine (SIGKILL) mid-transcribe and again mid-render; restart; the job must continue from that stage,
reuse finished artifacts, and finish with QC-passed renders. Uses the TEST-ONLY fake LLM via tests/harness_engine.py."""
import os
import shutil
import signal
import tempfile
import time
import unittest

import util


def job(api, jid):
    return api.get(f"/api/jobs/{jid}")[1]


def stage(j, name):
    return next(s for s in j["stages"] if s["name"] == name)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.vid = util.speech_video(os.path.join(self.td, "talk.mp4"), repeats=4)
        self.proc = None

    def tearDown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(10)
        shutil.rmtree(self.td, ignore_errors=True)

    def _start(self, port):
        self.proc, api = util.start_server(self.td, port=port, harness=True)
        return api

    def test_resume_after_crash(self):
        port = util.free_port()
        api = self._start(port)
        _, c, _ = api.post("/api/creators", {"name": "Resume", "rights_status": "owned"})
        _, src, _ = api.post("/api/sources/import", {"path": self.vid, "creator_id": c["id"]})
        _, j, _ = api.post("/api/jobs", {"source_id": src["id"], "settings": {"target": "15-30", "whisper_model": "base",
                                                                               "min_score": 0, "max_clips": 2}})
        jid = j["id"]
        part = os.path.join(self.td, "media", "work", str(src["id"]), "transcript.partial.jsonl")
        # wait until whisper has written at least one finished segment, then crash the engine hard
        util.wait(lambda: os.path.exists(part) and os.path.getsize(part) > 0, timeout=180, step=0.1)
        before = job(api, jid)
        self.assertEqual(before["current_stage"], "transcribe")
        early = {n: stage(before, n)["started_at"] for n in ("verify", "probe", "extract_audio")}
        os.kill(self.proc.pid, signal.SIGKILL)
        self.proc.wait(10)
        time.sleep(1.5)

        api = self._start(port)
        # job resumes from transcribe: earlier stages are not re-run
        util.wait(lambda: stage(job(api, jid), "transcribe")["status"] == "done", timeout=300)
        j = job(api, jid)
        for n, ts in early.items():
            self.assertEqual(stage(j, n)["started_at"], ts, f"{n} was re-run")
            self.assertEqual(stage(j, n)["attempts"], 1)
        self.assertIn("resume", stage(j, "transcribe")["detail"].lower())
        # crash again once the first clip render has started writing
        util.wait(lambda: job(api, jid)["current_stage"] == "render", timeout=300, step=0.2)
        util.wait(lambda: any(f.endswith(".part.mp4") or f == "clip.mp4"
                              for d, _, fs in os.walk(os.path.join(self.td, "media", "clips")) for f in fs), timeout=300, step=0.2)
        os.kill(self.proc.pid, signal.SIGKILL)
        self.proc.wait(10)
        time.sleep(1.5)
        api = self._start(port)
        util.wait(lambda: job(api, jid)["status"] in ("done", "failed"), timeout=600, step=1)
        j = job(api, jid)
        self.assertEqual(j["status"], "done", j.get("error"))
        self.assertEqual(stage(j, "detect")["attempts"], 1)  # detect ran once (before the 2nd crash), not again
        _, clips, _ = api.get(f"/api/clips?source_id={src['id']}")
        rendered = [c for c in clips if c["status"] == "rendered"]
        self.assertGreaterEqual(len(rendered), 1)
        for c in rendered:
            self.assertTrue(c["qc"]["pass"], c["qc"])
        _, allc, _ = api.get(f"/api/clips?source_id={src['id']}&status=all")
        # the fake provider's hallucinated candidate must have been discarded by verification
        self.assertFalse(any(c["title"] == "Hallucinated" for c in allc))
        # no stray temp files left from killed renders after success
        _, ev, _ = api.get("/api/events")
        self.assertTrue(any("resume" in e["message"] for e in ev))


if __name__ == "__main__":
    unittest.main()
