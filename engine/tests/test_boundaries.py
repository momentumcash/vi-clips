import unittest

import util  # noqa: F401
import boundaries as b


def words_seq(n, start=0.0, dur=0.3, gap=0.1, punct_every=8):
    out, t = [], start
    for i in range(n):
        w = f"w{i}" + ("." if (i + 1) % punct_every == 0 else "")
        out.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + gap
    return out


class BoundaryTest(unittest.TestCase):
    def test_never_mid_word(self):
        ws = words_seq(120)
        for rs in (3.05, 7.13, 10.0, 12.21):
            for re_ in (rs + 18.07, rs + 22.33):
                r = b.snap(ws, rs, re_, (15, 30), 200)
                self.assertFalse(b.is_mid_word(ws, r["start"]), r)
                self.assertFalse(b.is_mid_word(ws, r["end"]), r)

    def test_pre_pad_and_payoff_tail(self):
        ws = words_seq(80, gap=0.6)  # 0.6 s gaps leave room for the full tail
        r = b.snap(ws, ws[10]["start"], ws[50]["end"], (15, 60), 200)
        self.assertLessEqual(ws[r["i0"]]["start"] - r["start"], b.PRE_PAD + 1e-6)
        self.assertGreaterEqual(r["start"], ws[r["i0"] - 1]["end"])
        tail = r["end"] - ws[r["i1"]]["end"]
        self.assertGreaterEqual(tail, b.TAIL_MIN - 1e-6)
        self.assertLessEqual(tail, b.TAIL_MAX + 1e-6)
        self.assertEqual(r["i1"], 50)  # payoff word kept

    def test_tail_never_enters_next_word(self):
        ws = words_seq(80, gap=0.05)
        r = b.snap(ws, ws[5]["start"], ws[60]["end"], (15, 60), 200)
        self.assertLessEqual(r["end"], ws[r["i1"] + 1]["start"])
        self.assertGreaterEqual(r["end"], ws[r["i1"]]["end"])

    def test_too_short_adds_setup_not_tail(self):
        ws = words_seq(100)
        r = b.snap(ws, ws[56]["start"], ws[63]["end"], (15, 30), 200)
        self.assertGreaterEqual(r["end"] - r["start"], 14.0)
        self.assertEqual(r["i1"], 63)  # still ends on the payoff word: never stretched past it
        self.assertLess(r["i0"], 56)
        self.assertTrue(r["notes"])

    def test_too_short_without_setup_is_rejected(self):
        ws = words_seq(100)
        with self.assertRaises(b.SnapReject):
            b.snap(ws, ws[0]["start"], ws[10]["end"], (15, 30), 200)

    def test_leading_filler_trimmed(self):
        toks = ["Okay,", "so", "this", "is", "the", "real", "start", "of", "the", "story."] + [f"w{i}" for i in range(40)]
        ws = [{"word": w, "start": round(i * 0.4, 3), "end": round(i * 0.4 + 0.3, 3)} for i, w in enumerate(toks)]
        r = b.snap(ws, 0.0, ws[45]["end"], (5, 30), 60)
        self.assertEqual(ws[r["i0"]]["word"], "this")
        self.assertGreaterEqual(r["start"], ws[1]["end"])  # no filler audio left in

    def test_too_long_rejected(self):
        ws = words_seq(300)
        with self.assertRaises(b.SnapReject):
            b.snap(ws, ws[0]["start"], ws[200]["end"], (15, 30), 400)

    def test_isolated_short_rejected(self):
        ws = words_seq(10) + [{"word": "later", "start": 100.0, "end": 100.3}]
        with self.assertRaises(b.SnapReject):
            b.snap(ws, ws[0]["start"], ws[9]["end"], (15, 30), 200)


if __name__ == "__main__":
    unittest.main()


class SentenceStartTest(unittest.TestCase):
    def test_moves_back_to_sentence_start(self):
        toks = "we walked into the room. creating chaos around all four cars, three birds playing, it was loud".split()
        ws = [{"word": w, "start": round(i * 0.4, 3), "end": round(i * 0.4 + 0.3, 3)} for i, w in enumerate(toks)]
        i_three = toks.index("three")
        r = b.snap(ws * 1, ws[i_three]["start"], ws[-1]["end"], (1, 30), 60)
        self.assertEqual(ws[r["i0"]]["word"], "creating")
        self.assertTrue(any("sentence start" in n for n in r["notes"]))

    def test_pause_counts_as_sentence_start(self):
        ws = [{"word": "a", "start": 0.0, "end": 0.3}, {"word": "b", "start": 0.4, "end": 0.7},
              {"word": "after", "start": 1.2, "end": 1.5}] + \
             [{"word": f"w{i}", "start": 1.6 + i * 0.4, "end": 1.9 + i * 0.4} for i in range(60)]
        r = b.snap(ws, ws[4]["start"], ws[40]["end"], (5, 30), 60)
        self.assertEqual(ws[r["i0"]]["word"], "after")  # 0.5 s pause before it ≥ 0.35 s

    def test_snap_time_manual_edits(self):
        ws = [{"word": "this", "start": 66.28, "end": 66.44}, {"word": "is", "start": 66.6, "end": 66.7}]
        self.assertEqual(b.snap_time(ws, 66.3), 66.28)
        self.assertEqual(b.snap_time(ws, 66.42), 66.44)
        self.assertEqual(b.snap_time(ws, 66.5), 66.5)  # in a gap: kept
