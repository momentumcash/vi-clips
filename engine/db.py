"""SQLite store (WAL) + in-process event bus for the SSE stream.

One shared connection guarded by an RLock: the engine is single-process and the write volume is tiny.
JSON columns are TEXT and are parsed/serialised here so callers only ever see Python objects.
"""
import datetime as _dt
import json
import os
import queue
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, platform TEXT DEFAULT 'local', channel_url TEXT,
  monitoring INTEGER DEFAULT 0, categories TEXT DEFAULT '[]', source_method TEXT DEFAULT 'upload', folder_path TEXT,
  rights_status TEXT DEFAULT 'unknown', rights_note TEXT, last_scan_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT, creator_id INTEGER, title TEXT, origin TEXT, origin_ref TEXT, file_key TEXT,
  duration_s REAL, width INTEGER, height INTEGER, fps REAL, has_audio INTEGER, thumb_key TEXT, status TEXT DEFAULT 'new',
  rights_status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, kind TEXT DEFAULT 'process', settings TEXT, stages TEXT,
  status TEXT DEFAULT 'queued', current_stage TEXT, cost_usd REAL DEFAULT 0, error TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, job_id INTEGER, rank INTEGER, start_s REAL, end_s REAL,
  title TEXT, description TEXT, hook TEXT, payoff TEXT, needs_context INTEGER DEFAULT 0, context_note TEXT, best_open_s REAL,
  category TEXT, scores TEXT, reasons TEXT, score_basis TEXT, overall REAL, concerns TEXT DEFAULT '[]', caption_preset TEXT,
  crop TEXT, captions TEXT DEFAULT '[]', status TEXT DEFAULT 'candidate', file_key TEXT, thumb_key TEXT, qc TEXT,
  transcript_excerpt TEXT, duplicate_of INTEGER, variant_of INTEGER, keywords TEXT DEFAULT '[]', reject_reason TEXT,
  render_sig TEXT, captions_edited INTEGER DEFAULT 0, manual_range INTEGER DEFAULT 0, error TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, clip_ids TEXT, dir TEXT, files TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, platform TEXT, views INTEGER, likes INTEGER, comments INTEGER,
  shares INTEGER, avg_watch_pct REAL, recorded_at TEXT, source TEXT DEFAULT 'manual');
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, kind TEXT, message TEXT, ref TEXT);
CREATE INDEX IF NOT EXISTS clips_source ON clips(source_id);
CREATE INDEX IF NOT EXISTS clips_job ON clips(job_id);
CREATE INDEX IF NOT EXISTS jobs_source ON jobs(source_id);
"""

JSON_COLS = {
    "creators": ("categories", "vocab"),
    "jobs": ("settings", "stages"),
    "clips": ("scores", "reasons", "score_basis", "concerns", "crop", "captions", "qc", "keywords", "model_scores"),
    "exports": ("clip_ids", "files"),
    "settings": ("value",),
    "events": ("ref",),
    "sources": (), "metrics": (),
}


def now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Bus:
    """Fan-out of live events to SSE subscribers. Each subscriber gets its own bounded queue."""

    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, kind, data):
        msg = {"type": kind, "ts": now(), "data": data}
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass  # slow client; it will resync from REST


class DB:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.bus = Bus()

    MIGRATIONS = {
        "clips": (("variant_of", "INTEGER"), ("keywords", "TEXT DEFAULT '[]'"), ("reject_reason", "TEXT"),
                  ("render_sig", "TEXT"), ("captions_edited", "INTEGER DEFAULT 0"), ("manual_range", "INTEGER DEFAULT 0"),
                  ("error", "TEXT"), ("below_bar", "INTEGER DEFAULT 0"), ("below_bar_reason", "TEXT"),
                  ("scores_stale", "INTEGER DEFAULT 0"), ("model_scores", "TEXT")),
        "creators": (("vocab", "TEXT DEFAULT '[]'"),),
        "sources": (("fingerprint", "TEXT"),),
    }

    def _migrate(self):
        # add columns introduced after a DB was first created
        for table, cols in self.MIGRATIONS.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols:
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    # ------------------------------------------------------------------ low level
    def _parse(self, table, row):
        if row is None:
            return None
        d = dict(row)
        for c in JSON_COLS.get(table, ()):
            if c in d and isinstance(d[c], str):
                try:
                    d[c] = json.loads(d[c])
                except ValueError:
                    pass
        return d

    def _enc(self, table, fields):
        out = {}
        for k, v in fields.items():
            if k in JSON_COLS.get(table, ()) and v is not None and not isinstance(v, str):
                v = json.dumps(v)
            out[k] = v
        return out

    def query(self, table, sql, args=()):
        with self.lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [self._parse(table, r) for r in rows]

    def scalar(self, sql, args=()):
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
        return r[0] if r else None

    def execute(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args)

    def get(self, table, id_):
        with self.lock:
            r = self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()
        return self._parse(table, r)

    def insert(self, table, fields):
        f = self._enc(table, fields)
        cols = ",".join(f)
        qs = ",".join("?" for _ in f)
        with self.lock:
            cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(f.values()))
            return cur.lastrowid

    def update(self, table, id_, fields):
        if not fields:
            return
        f = self._enc(table, fields)
        sets = ",".join(f"{k}=?" for k in f)
        with self.lock:
            self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", tuple(f.values()) + (id_,))

    def delete(self, table, id_):
        with self.lock:
            self.conn.execute(f"DELETE FROM {table} WHERE id=?", (id_,))

    # ------------------------------------------------------------------ settings
    def get_setting(self, key, default=None):
        with self.lock:
            r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not r:
            return default
        try:
            return json.loads(r[0])
        except ValueError:
            return default

    def set_setting(self, key, value):
        with self.lock:
            self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (key, json.dumps(value)))

    # ------------------------------------------------------------------ activity feed
    def event(self, kind, message, level="info", ref=None):
        ts = now()
        eid = self.insert("events", {"ts": ts, "level": level, "kind": kind, "message": message, "ref": ref or {}})
        ev = {"id": eid, "ts": ts, "level": level, "kind": kind, "message": message, "ref": ref or {}}
        self.bus.publish("event", ev)
        return ev
