"""Fix-round rules: below-bar fallback, manual edits snap to word edges, stale scores + rescore, variant_of on every clip,
duplicate import by fingerprint, folder path error, publish without platform, weighted progress, burned-in text check."""
import os
import shutil
import subprocess
import tempfile
import unittest

import util
import jobs
import reframe


class EngineRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp()
        cls.vid = util.speech_video(os.path.join(cls.td, "talk.mp4"), repeats=2)
        cls.proc, cls.api = util.start_server(cls.td, harness=True, env={"CLIPFORGE_FAKE_SCORE": "4"})
        _, cls.creator, _ = cls.api.post("/api/creators", {"name": "Rules", "rights_status": "owned", "vocab": ["ClipForge"]})
        _, cls.src, _ = cls.api.post("/api/sources/import", {"path": cls.vid, "creator_id": cls.creator["id"]})
        _, j, _ = cls.api.post("/api/jobs", {"source_id": cls.src["id"], "settings": {"target": "15-30", "whisper_model": "base"}})
        util.wait(lambda: cls.api.get(f"/api/jobs/{j['id']}")[1]["status"] in ("done", "failed"), timeout=600, step=1)
        cls.job = cls.api.get(f"/api/jobs/{j['id']}")[1]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(10)
        shutil.rmtree(cls.td, ignore_errors=True)

    def test_below_bar_fallback(self):
        self.assertEqual(self.job["status"], "done", self.job.get("error"))
        _, clips, _ = self.api.get(f"/api/clips?job_id={self.job['id']}&status=all")
        shown = [c for c in clips if c["status"] != "rejected"]
        self.assertTrue(1 <= len(shown) <= 2, [c["status"] for c in clips])
        for c in shown:
            self.assertEqual(c["below_bar"], 1)
            self.assertIn("below min_score", c["below_bar_reason"])
            self.assertEqual(c["status"], "rendered")
            self.assertIn("variant_of", c)  # present (null) on every clip
        tr = os.path.join(self.td, "media", "work", str(self.src["id"]), "transcript.json")
        import json
        t = json.load(open(tr))
        self.assertIn("ClipForge", t["hotwords"])  # creator vocab reached whisper

    def test_patch_snaps_mid_word_and_marks_stale_then_rescore(self):
        _, clips, _ = self.api.get(f"/api/clips?job_id={self.job['id']}")
        c = self.api.get(f"/api/clips/{clips[0]['id']}")[1]
        w = next(x for x in c["words"] if x["start"] > c["start_s"] + 1 and x["end"] - x["start"] > 0.15)
        mid = round((w["start"] + w["end"]) / 2, 3)
        s, p, _ = self.api.patch(f"/api/clips/{c['id']}", {"start_s": mid})
        self.assertEqual(s, 200, p)
        self.assertIn(p["start_s"], (w["start"], w["end"]))
        self.assertEqual(p["scores_stale"], 1)
        s, r, _ = self.api.post(f"/api/clips/{c['id']}/rescore")
        self.assertEqual(s, 200, r)
        self.assertEqual(r["scores_stale"], 0)
        self.assertEqual(r["score_basis"]["open_frame"], "measured")

    def test_duplicate_import_by_fingerprint(self):
        copy = os.path.join(self.td, "same file elsewhere.mp4")
        shutil.copy(self.vid, copy)
        s, b, _ = self.api.post("/api/sources/import", {"path": copy, "creator_id": self.creator["id"]})
        self.assertEqual(s, 200)
        self.assertEqual(b["id"], self.src["id"])
        self.assertTrue(b.get("already_imported"))

    def test_folder_path_and_publish_without_platform(self):
        s, b, _ = self.api.post("/api/sources/import", {"path": self.td, "creator_id": self.creator["id"]})
        self.assertEqual(s, 400)
        self.assertIn("folder", b["error"])
        _, clips, _ = self.api.get(f"/api/clips?job_id={self.job['id']}&status=all")
        s, b, _ = self.api.post(f"/api/publish/{clips[0]['id']}", {})
        self.assertEqual(s, 400)

    def test_weighted_progress(self):
        st = jobs.new_stages()
        for s in st[:3]:
            s["status"] = "done"
        st[3].update(status="running", progress=0.5)
        p = jobs.weighted_progress(st)
        self.assertGreater(p, 0.2)
        self.assertLess(p, 0.3)  # transcribe dominates: half of it ≈ 23 % of the job, not 35 %


class TextCutTest(unittest.TestCase):
    def test_title_across_crop_edge_is_flagged(self):
        from PIL import Image, ImageDraw, ImageFont
        td = tempfile.mkdtemp()
        try:
            noise = (__import__("numpy").random.default_rng(1).normal(110, 6, (270, 480))).clip(0, 255).astype("uint8")
            plain = os.path.join(td, "plain.jpg")
            Image.fromarray(noise).save(plain)
            im = Image.fromarray(noise).convert("RGB")
            f = ImageFont.truetype(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts", "Anton-Regular.ttf"), 34)
            ImageDraw.Draw(im).text((40, 150), "THE CHARLIE DANIELS ROOM", font=f, fill=(255, 255, 255))
            titled = os.path.join(td, "title.jpg")
            im.save(titled)
            cwf = 0.3164
            self.assertEqual(reframe.text_cut_times([(0.0, plain)], [], cwf), [])
            self.assertEqual(reframe.text_cut_times([(0.0, titled)], [], cwf), [0.0])
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
