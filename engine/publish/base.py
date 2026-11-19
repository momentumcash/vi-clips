import os


class Publisher:
    name = "base"
    needs = []
    doc = ""
    why = ""

    def status(self):
        missing = [k for k in self.needs if not os.environ.get(k)]
        # Even with credentials present, no upload path is implemented yet: report blocked honestly.
        detail = (f"missing {', '.join(missing)}" if missing else "credentials present but the upload flow is not implemented in this build")
        return {"name": self.name, "kind": "publish", "status": "blocked", "needs": missing or ["upload implementation"],
                "detail": detail + (" — " + self.why if self.why else ""), "doc": self.doc}

    def publish(self, clip_id):
        st = self.status()
        return {"status": "blocked", "detail": f"{self.name}: {st['detail']}",
                "fix": f"set {', '.join(st['needs'])} and complete the platform's app review; export the clip and post it by hand meanwhile"}
