"""API smoke on a temp port with a temp DB (real server process), plus rights gates and integration honesty."""
import json
import os
import shutil
import tempfile
import unittest
import urllib.request

import util


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp()
        cls.proc, cls.api = util.start_server(cls.td, env={"TIKTOK_ACCESS_TOKEN": "super-secret-value"})
        cls.vid = util.lavfi_video(os.path.join(cls.td, "in.mp4"), dur=3, size="1280x720")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(10)
        shutil.rmtree(cls.td, ignore_errors=True)

    def test_ping_overview_settings(self):
        s, b, _ = self.api.get("/api/ping")
        self.assertEqual((s, b["ok"]), (200, True))
        s, b, _ = self.api.get("/api/overview")
        self.assertEqual(s, 200)
        for k in ("clips_today", "creators_monitored", "queue", "top_clips", "activity"):
            self.assertIn(k, b)
        s, b, _ = self.api.get("/api/settings")
        self.assertEqual(s, 200)
        self.assertEqual(b["integrations"]["TIKTOK_ACCESS_TOKEN"], "set")
        self.assertNotIn("super-secret-value", json.dumps(b))
        s, b, _ = self.api.patch("/api/settings", {"automation": {"min_score": 55}})
        self.assertEqual((s, b["automation"]["min_score"]), (200, 55))
        s, b, _ = self.api.patch("/api/settings", {"nope": 1})
        self.assertEqual(s, 400)
        self.assertIn("fix", b)

    def test_creator_crud_and_import(self):
        s, c, _ = self.api.post("/api/creators", {"name": "Smoke", "rights_status": "owned"})
        self.assertEqual(s, 200)
        s, c2, _ = self.api.patch(f"/api/creators/{c['id']}", {"rights_note": "my own footage"})
        self.assertEqual(c2["rights_note"], "my own footage")
        s, b, _ = self.api.post("/api/creators", {"name": "Bad", "rights_status": "maybe"})
        self.assertEqual(s, 400)
        s, src, _ = self.api.post("/api/sources/import", {"path": self.vid, "creator_id": c["id"]})
        self.assertEqual(s, 200, src)
        self.assertEqual((src["width"], src["height"]), (1280, 720))
        self.assertEqual(src["origin_ref"], self.vid)  # referenced in place, not moved
        self.assertTrue(os.path.exists(self.vid))
        s, b, _ = self.api.get(f"/api/sources/{src['id']}")
        self.assertEqual(s, 200)
        self.assertIn("jobs", b)
        s, tl, _ = self.api.get(f"/api/sources/{src['id']}/timeline")
        self.assertEqual((s, tl["energy"], tl["frames"]), (200, [], []))  # nothing measured yet → nothing drawn
        s, b, _ = self.api.call("DELETE", f"/api/creators/{c['id']}")
        self.assertEqual(s, 409)
        # range request on the source video
        req = urllib.request.Request(self.api.base + src["video_url"], headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(len(r.read()), 100)
            self.assertTrue(r.headers["Content-Range"].startswith("bytes 0-99/"))

    def test_upload(self):
        s, c, _ = self.api.post("/api/creators", {"name": "Uploader", "rights_status": "licensed"})
        data = open(self.vid, "rb").read()
        s, src, _ = self.api.call("POST", "/api/sources/upload", raw=data,
                                  headers={"X-Filename": "up%20load.mp4", "X-Creator-Id": str(c["id"]), "Content-Length": str(len(data))})
        self.assertEqual(s, 200, src)
        self.assertTrue(src["file_key"].startswith("uploads/"))

    def test_url_import_rights_gate(self):
        for rights in ("unknown", "pending", "denied"):
            s, c, _ = self.api.post("/api/creators", {"name": f"Gate {rights}", "rights_status": rights, "platform": "youtube"})
            s, b, _ = self.api.post("/api/sources/url", {"url": "https://www.youtube.com/watch?v=xxxxxxxxxxx", "creator_id": c["id"]})
            self.assertEqual(s, 403, b)
            self.assertIn("rights", b["error"])
            self.assertTrue(b["fix"])

    def test_export_rights_gate(self):
        s, c, _ = self.api.post("/api/creators", {"name": "Pending person", "rights_status": "pending"})
        s, src, _ = self.api.post("/api/sources/import", {"path": self.vid, "creator_id": c["id"], "title": "pending src"})
        # a rendered + QC-passed clip row for a pending-rights source must still be blocked at export
        import sqlite3
        con = sqlite3.connect(os.path.join(self.td, "clipforge.db"))
        con.execute("INSERT INTO clips (source_id, start_s, end_s, title, status, file_key, qc) VALUES (?,?,?,?,?,?,?)",
                    (src["id"], 0, 3, "x", "rendered", "origin/%d" % src["id"], json.dumps({"pass": True, "checks": []})))
        cid = con.execute("SELECT MAX(id) FROM clips").fetchone()[0]
        con.commit()
        con.close()
        s, b, _ = self.api.post("/api/export", {"clip_ids": [cid]})
        self.assertIn(s, (403, 409), b)
        self.assertEqual(b["error"], "export blocked")

    def test_integrations_blocked_and_publish(self):
        s, ints, _ = self.api.get("/api/integrations")
        self.assertEqual(s, 200)
        pubs = [i for i in ints if i["kind"] == "publish"]
        self.assertEqual({p["name"] for p in pubs}, {"tiktok", "youtube", "instagram"})
        for p in pubs:
            self.assertEqual(p["status"], "blocked")
            self.assertTrue(p["needs"])
        yt = next(i for i in ints if i["name"] == "youtube_api")
        if not os.environ.get("YOUTUBE_API_KEY"):
            self.assertEqual((yt["status"], yt["needs"]), ("blocked", ["YOUTUBE_API_KEY"]))
        s, c, _ = self.api.post("/api/creators", {"name": "Pub", "rights_status": "owned"})
        s, src, _ = self.api.post("/api/sources/import", {"path": self.vid, "creator_id": c["id"], "title": "pub"})
        import sqlite3
        con = sqlite3.connect(os.path.join(self.td, "clipforge.db"))
        con.execute("INSERT INTO clips (source_id, start_s, end_s, title, status) VALUES (?,?,?,?,?)", (src["id"], 0, 3, "p", "rendered"))
        cid = con.execute("SELECT MAX(id) FROM clips").fetchone()[0]
        con.commit()
        con.close()
        s, b, _ = self.api.post(f"/api/publish/{cid}", {"platform": "tiktok"})
        self.assertEqual(s, 501)
        self.assertIn("TIKTOK", b["detail"] + b["fix"])

    def test_errors_have_shape(self):
        s, b, _ = self.api.get("/api/clips/999999")
        self.assertEqual(s, 404)
        self.assertEqual(set(b), {"error", "detail", "fix"})
        s, b, _ = self.api.post("/api/jobs", {"source_id": 999999})
        self.assertEqual(s, 404)

    def test_delete_source_keeps_original(self):
        s, c, _ = self.api.post("/api/creators", {"name": "Deleter", "rights_status": "owned"})
        s, src, _ = self.api.post("/api/sources/import", {"path": self.vid, "creator_id": c["id"], "title": "to delete"})
        thumb = os.path.join(self.td, "media", "sources", str(src["id"]))
        self.assertTrue(os.path.isdir(thumb))
        s, b, _ = self.api.call("DELETE", f"/api/sources/{src['id']}")
        self.assertEqual(s, 200, b)
        self.assertTrue(os.path.exists(self.vid))  # the original is never touched
        self.assertFalse(os.path.exists(thumb))
        self.assertEqual(self.api.get(f"/api/sources/{src['id']}")[0], 404)
        self.assertEqual(self.api.call("DELETE", "/api/jobs/999999")[0], 404)
        self.assertEqual(self.api.call("DELETE", "/api/clips/999999")[0], 404)
        s, b, _ = self.api.call("DELETE", f"/api/creators/{c['id']}")
        self.assertEqual(s, 200, b)

    def test_sse_stream_hello(self):
        req = urllib.request.Request(self.api.base + "/api/stream")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.headers["Content-Type"], "text/event-stream")
            buf = b""
            while b"\n\n" not in buf.split(b"retry: 3000\n", 1)[-1]:
                buf += r.read1(4096)
        self.assertIn(b"event: hello", buf)


if __name__ == "__main__":
    unittest.main()
