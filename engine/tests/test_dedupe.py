import unittest

import util  # noqa: F401
import dedupe


class DedupeTest(unittest.TestCase):
    def test_iou_overlap(self):
        items = [{"start": 10, "end": 40, "overall": 70, "text": "alpha beta gamma"},
                 {"start": 15, "end": 42, "overall": 80, "text": "delta epsilon"},
                 {"start": 100, "end": 130, "overall": 60, "text": "zeta theta"}]
        d = dedupe.dedupe(items)
        self.assertEqual(d, {0: 1})  # lower overall is the duplicate

    def test_same_conversation_tokens(self):
        t = "the old warehouse downtown will become an immersive music venue with twenty five foot walls"
        items = [{"start": 0, "end": 20, "overall": 65, "text": t},
                 {"start": 50, "end": 70, "overall": 75, "text": t + " and a back alley bar"}]
        self.assertEqual(dedupe.dedupe(items), {0: 1})

    def test_distinct_kept(self):
        items = [{"start": 0, "end": 20, "overall": 65, "text": "swimming open water pacing tricks"},
                 {"start": 30, "end": 50, "overall": 75, "text": "bike crash recovery hospital"}]
        self.assertEqual(dedupe.dedupe(items), {})

    def test_iou_math(self):
        self.assertAlmostEqual(dedupe.iou((0, 10), (5, 15)), 5 / 15)
        self.assertEqual(dedupe.iou((0, 10), (10, 20)), 0.0)


if __name__ == "__main__":
    unittest.main()
