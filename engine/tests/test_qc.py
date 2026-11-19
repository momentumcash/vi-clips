import os
import shutil
import tempfile
import unittest

import util
import db as dbm
import qc
import server


class QCTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp()
        cls.app = server.App(data_dir=cls.td, start_runner=False)
        cls.creator = {"id": 1, "name": "Test Creator"}
        cls.source = {"id": 1, "title": "Synthetic", "rights_status": "owned"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def _clip(self, name, **kw):
        """Render a synthetic file with the attribution tag into storage and return a clip dict for it."""
        key = f"clips/{name}/clip.mp4"
        path = self.app.storage.path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        raw = os.path.join(self.td, name + "_raw.mp4")
        util.lavfi_video(raw, **kw)
        import subprocess
        subprocess.run([util.FF, "-v", "error", "-y", "-i", raw, "-c", "copy", "-metadata",
                        "comment=Source: Test Creator — Synthetic", path], check=True)
        dur = kw.get("dur", 4)
        return {"id": 1, "file_key": key, "start_s": 10.0, "end_s": 10.0 + dur, "captions": [
            {"start": 0.2, "end": 1.0, "text": "HELLO", "emphasis": []}], "caption_preset": "bold_entertainment",
            "duplicate_of": None, "manual_range": 1}

    def _checks(self, q):
        return {c["name"]: c for c in q["checks"]}

    def test_good_clip_passes(self):
        q = qc.run_qc(self.app, self._clip("good", dur=6), self.source, self.creator, {"target": "auto"})
        failed = [c for c in q["checks"] if not c["pass"]]
        self.assertTrue(q["pass"], failed)

    def test_detects_black_gap(self):
        q = qc.run_qc(self.app, self._clip("black", dur=6, black_from=2, black_to=3), self.source, self.creator, {"target": "auto"})
        self.assertFalse(self._checks(q)["no_black_gaps"]["pass"])
        self.assertFalse(q["pass"])

    def test_detects_wrong_size(self):
        q = qc.run_qc(self.app, self._clip("size", dur=6, size="1280x720"), self.source, self.creator, {"target": "auto"})
        self.assertFalse(self._checks(q)["size_1080x1920"]["pass"])

    def test_detects_silent_audio(self):
        q = qc.run_qc(self.app, self._clip("silent", dur=6, silent=True), self.source, self.creator, {"target": "auto"})
        self.assertFalse(self._checks(q)["audio_not_silent"]["pass"])
        self.assertFalse(q["pass"])

    def test_caption_overlap_and_duration(self):
        c = self._clip("caps", dur=6)
        c["captions"] = [{"start": 0, "end": 2, "text": "A", "emphasis": []}, {"start": 1.5, "end": 7, "text": "", "emphasis": []}]
        c["end_s"] = c["start_s"] + 5  # plan says 5 s, file is 6 s
        q = qc.run_qc(self.app, c, self.source, self.creator, {"target": "auto"})
        ch = self._checks(q)
        self.assertFalse(ch["captions_timing"]["pass"])
        self.assertFalse(ch["duration_matches_plan"]["pass"])

    def test_rights_check_is_informational(self):
        q = qc.run_qc(self.app, self._clip("rights", dur=6), dict(self.source, rights_status="pending"), self.creator, {"target": "auto"})
        ch = self._checks(q)
        self.assertFalse(ch["export_rights"]["pass"])
        self.assertFalse(ch["export_rights"]["blocking"])


if __name__ == "__main__":
    unittest.main()
