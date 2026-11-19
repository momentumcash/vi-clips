import unittest

import util  # noqa: F401  (sets sys.path)
import score
from config import WEIGHTS, SCORE_KEYS


class ScoringTest(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=9)
        self.assertEqual(set(WEIGHTS), set(SCORE_KEYS))

    def test_overall_math(self):
        self.assertEqual(score.overall({k: 10 for k in SCORE_KEYS}), 100)
        self.assertEqual(score.overall({k: 0 for k in SCORE_KEYS}), 0)
        s = {k: 5 for k in SCORE_KEYS}
        s["hook"] = 10  # +5 * .18 * 10 = +9
        self.assertEqual(score.overall(s), 59)

    def test_blend_and_basis(self):
        model = {k: 6 for k in SCORE_KEYS}
        meas = {"pacing": (10.0, {}, "fast"), "clarity": (4.0, {}, "muddy"), "open_frame": (2.0, {}, "black-ish")}
        sc, rs, basis = score.blend(model, {k: "r" for k in SCORE_KEYS}, meas)
        self.assertEqual(sc["pacing"], 8.0)
        self.assertEqual(sc["clarity"], 5.0)
        self.assertEqual(sc["open_frame"], 2.0)  # measured only
        self.assertEqual(basis["pacing"], "blend")
        self.assertEqual(basis["open_frame"], "measured")
        self.assertEqual(basis["hook"], "model")
        self.assertIn("Measured", rs["pacing"])

    def test_pacing_measured(self):
        brisk = [{"word": "w", "start": i * 0.35, "end": i * 0.35 + 0.3} for i in range(60)]  # ~2.9 w/s
        s1, d1 = score.pacing_measured(brisk, 0, 21)
        slow = [{"word": "w", "start": i * 1.5, "end": i * 1.5 + 0.3} for i in range(14)]  # long gaps
        s2, d2 = score.pacing_measured(slow, 0, 21)
        self.assertGreater(s1, 8)
        self.assertLess(s2, 4)
        self.assertGreater(d2["dead_air"], d1["dead_air"])

    def test_open_frame_black(self):
        s, d = score.open_frame_measured({"faces": [], "sharpness": 0.0, "luma": 3.0})
        self.assertEqual(s, 0.0)
        self.assertTrue(d["black"])
        s2, _ = score.open_frame_measured({"faces": [[0.4, 0.3, 0.2, 0.2, 0.9]], "sharpness": 500.0, "luma": 120.0})
        self.assertEqual(s2, 10.0)


if __name__ == "__main__":
    unittest.main()
