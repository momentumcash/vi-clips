import math
import os
import re
import subprocess
import tempfile
import unittest

import util
import media
import reframe as rf


def eval_expr(expr, t, iw, ow):
    """Evaluate our ffmpeg crop expression in Python (if/lt/max/min only)."""
    py = expr.replace("iw", str(iw)).replace("ow", str(ow))
    py = re.sub(r"\bif\(", "_if(", py)
    py = re.sub(r"\blt\(", "_lt(", py)
    return eval(py, {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b, "max": max, "min": min, "t": t})


class ReframeTest(unittest.TestCase):
    def test_expression_bounds(self):
        iw, ow = 1920, 608
        tracks = [[], [[0, 0.5]], [[0, -0.4], [1.5, 1.7], [3, 0.2], [6, 0.9]], [[0.2, 0.1], [0.201, 0.95], [9, 0.95]]]
        for tr in tracks:
            e = rf.crop_expr(tr, x_const=1.3)
            for k in range(0, 100):
                t = k * 0.1
                x = eval_expr(e, t, iw, ow)
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x, iw - ow)

    def test_expression_matches_python_twin(self):
        iw, ow = 1920, 608
        tr = [[0, 0.3], [2, 0.6], [2.001, 0.4], [5, 0.45]]
        e = rf.crop_expr(tr)
        for t in (0, 1, 1.999, 2.0005, 3.5, 9):
            want = min(iw - ow, max(0, rf.crop_x_at(tr, t) * iw - ow / 2))
            self.assertAlmostEqual(eval_expr(e, t, iw, ow), want, delta=0.5)

    def test_smooth_dead_zone_speed_cap_and_cut(self):
        cwf = 0.3164
        times = [i / 3 for i in range(60)]
        jitter = [0.5 + 0.01 * math.sin(i) for i in range(60)]  # tiny jitter: inside the dead zone → no movement
        p = rf.smooth_path(times, jitter, cwf)
        self.assertLess(max(x for _, x in p) - min(x for _, x in p), 1e-6)
        step = [0.5] * 20 + [0.6] * 40  # moderate move → capped pan
        p = rf.smooth_path(times, step, cwf)
        for (t0, x0), (t1, x1) in zip(p, p[1:]):
            if t1 - t0 > 0.01:
                self.assertLessEqual(abs(x1 - x0) / (t1 - t0), 0.6 * cwf + 1e-6)
        far = [0.2] * 20 + [0.8] * 40  # sustained subject switch → hard cut
        p = rf.smooth_path(times, far, cwf)
        self.assertTrue(any(t1 - t0 < 0.01 and abs(x1 - x0) > 0.3 for (t0, x0), (t1, x1) in zip(p, p[1:])))

    def test_simplify_caps_points(self):
        path = [[i / 3, 0.5 + 0.2 * math.sin(i / 5)] for i in range(600)]
        s = rf.simplify(path, max_pts=60)
        self.assertLessEqual(len(s), 60)
        self.assertEqual(s[0][0], round(path[0][0], 3))

    def test_ffmpeg_accepts_expression_and_outputs_9x16(self):
        with tempfile.TemporaryDirectory() as td:
            src = util.lavfi_video(os.path.join(td, "w.mp4"), dur=3, size="1920x1080")
            tr = [[0, 0.2], [1.0, 0.8], [1.001, 0.3], [3, 0.6]]
            out = os.path.join(td, "o.mp4")
            subprocess.run([util.FF, "-v", "error", "-y", "-i", src, "-vf",
                            f"crop=w=trunc(ih*9/16/2)*2:h=ih:x='{rf.crop_expr(tr)}':y=0,scale=1080:1920", "-an", out], check=True)
            pr = media.probe(out)
            self.assertEqual((pr["width"], pr["height"]), (1080, 1920))

    def test_vertical_source_not_cropped(self):
        self.assertIsNone(rf.crop_frac({"width": 1080, "height": 1920}))
        self.assertAlmostEqual(rf.crop_frac({"width": 1920, "height": 1080}), 1080 * 9 / 16 / 1920)


if __name__ == "__main__":
    unittest.main()


class TextSpanRenderTest(unittest.TestCase):
    def test_fit_to_width_during_text_span(self):
        import numpy as np
        import captions as cap
        import render
        from PIL import Image
        pr = {"width": 1920, "height": 1080, "fps": 30.0, "transfer": None}
        crop = {"mode": "auto", "track": [[0, 0.5]], "x": 0.5,
                "text_spans": [{"start": 1.0, "end": 2.0, "band": [0.45, 0.6]}]}
        with tempfile.TemporaryDirectory() as td:
            src = util.lavfi_video(os.path.join(td, "w.mp4"), dur=3, size="1920x1080")
            preset = cap.load_presets()["bold_entertainment"]
            caps = [{"start": 0.2, "end": 2.8, "text": "HELLO THERE", "emphasis": []}]
            zones = render.text_avoid_zones(pr, crop)
            g = cap.layout(caps[0], preset)
            cy = cap.avoid_cy(g, zones, 0.2, 2.8)
            self.assertTrue(cy + g["height"] / 2 <= zones[0]["y0"] or cy - g["height"] / 2 >= zones[0]["y1"])
            self.assertTrue(0.55 * 1920 <= cy <= 0.72 * 1920)
            ass = os.path.join(td, "c.ass")
            with open(ass, "w") as f:
                f.write(cap.to_ass(caps, preset, 3.0, avoid=zones))
            fc = render.build_filter(pr, crop, ass, 3.0, preset, {"progress_bar": False})
            out = os.path.join(td, "o.mp4")
            subprocess.run([util.FF, "-v", "error", "-y", "-i", src, "-filter_complex", fc, "-map", "[v]", "-an", out], check=True)
            pr2 = media.probe(out)
            self.assertEqual((pr2["width"], pr2["height"]), (1080, 1920))
            frames = {}
            for t in (0.5, 1.5, 2.5):
                p = os.path.join(td, f"f{t}.png")
                subprocess.run([util.FF, "-v", "error", "-y", "-ss", str(t), "-i", out, "-frames:v", "1", p], check=True)
                frames[t] = np.asarray(Image.open(p).convert("L"), dtype=float)
            # the full 16:9 frame (all 7 colour bars → many hard vertical edges in one row) shows only inside the span;
            # the 9:16 crop around it sees ~2 bars. Hard cut both ways (frames 0.5 s either side are crop mode).
            def edges(a):
                row = a[900]
                return int((np.abs(np.diff(row)) > 40).sum())
            self.assertGreaterEqual(edges(frames[1.5]), 5)
            self.assertLessEqual(edges(frames[0.5]), 3)
            self.assertLessEqual(edges(frames[2.5]), 3)
