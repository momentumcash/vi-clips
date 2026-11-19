import os
import subprocess
import tempfile
import unittest

import util
import captions as cap
from config import FONTS

LONG = ("Absolutely incomprehensible internationalization responsibilities, honestly, my father said I'd be back within "
        "a month — 2,000,000 views later the studio was born. Uncharacteristically extraordinary!").split()


def mk_words(tokens, t0=0.0, step=0.32):
    return [{"word": w, "start": round(t0 + i * step, 3), "end": round(t0 + i * step + 0.28, 3)} for i, w in enumerate(tokens)]


class CaptionTest(unittest.TestCase):
    def setUp(self):
        self.presets = cap.load_presets()

    def test_five_presets_distinct(self):
        self.assertEqual(set(self.presets), {"clean_podcast", "gaming", "bold_entertainment", "minimal_cinematic", "high_energy"})
        sig = {(p["font_file"], p["case"], p["accent"], p["words_per_beat"], p["animation"]) for p in self.presets.values()}
        self.assertEqual(len(sig), 5)
        for p in self.presets.values():
            self.assertTrue(os.path.exists(os.path.join(FONTS, p["font_file"])), p["font_file"])
            self.assertTrue(0.55 <= p["position"] <= 0.72)

    def test_safe_zone_and_no_overlap_all_presets(self):
        words = mk_words(LONG * 3)
        dur = words[-1]["end"] + 0.5
        for name, p in self.presets.items():
            caps = cap.build_beats(words, p, dur, keywords=["studio"])
            self.assertTrue(caps, name)
            self.assertEqual(cap.check_safe(caps, p), [], name)
            self.assertEqual(cap.check_timing(caps, dur), [], name)
            for c in caps:
                self.assertLessEqual(len(c["emphasis"]), 1)
                self.assertLessEqual(len(c["text"].split("\n")), p["max_lines"])

    def test_emphasis_prefers_numbers_and_keywords(self):
        p = self.presets["bold_entertainment"]
        caps = cap.build_beats(mk_words("we hit 2,000,000 views".split()), p, 3.0)
        hit = [c for c in caps if "2,000,000" in c["text"]][0]
        self.assertEqual(hit["text"].split()[hit["emphasis"][0]].upper(), "2,000,000")

    def test_casing(self):
        p = self.presets["clean_podcast"]
        caps = cap.build_beats(mk_words("so i said yes. then i'm gone".split()), p, 4.0)
        txt = " ".join(c["text"] for c in caps).replace("\n", " ")
        self.assertIn("So I said yes.", txt)
        self.assertIn("Then I'm gone", txt)

    def test_captions_inside_clip_time(self):
        words = mk_words(LONG, t0=-0.5)  # words that start before the clip are clamped
        p = self.presets["gaming"]
        caps = cap.build_beats(words, p, 5.0)
        self.assertEqual(cap.check_timing(caps, 5.0), [])

    def test_ass_renders_with_bundled_font(self):
        """The fontsdir trap: libass must load the bundled font, not fall back to Helvetica."""
        p = self.presets["bold_entertainment"]
        caps = cap.build_beats(mk_words("this is the moment".split()), p, 2.0)
        with tempfile.TemporaryDirectory() as td:
            ass = os.path.join(td, "c.ass")
            with open(ass, "w") as f:
                f.write(cap.to_ass(caps, p, 2.0, context_note="Context line"))
            r = subprocess.run([util.FF, "-hide_banner", "-v", "verbose", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:d=1",
                                "-vf", f"ass=filename={ass}:fontsdir={FONTS}", "-frames:v", "1", "-f", "null", "-"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertRegex(r.stderr, r"Anton")
            self.assertNotRegex(r.stderr, r"fontselect:.*-> Helvetica")


if __name__ == "__main__":
    unittest.main()
