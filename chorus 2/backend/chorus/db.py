"""SQLite storage. One file, no server, good enough for a single-node MVP."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  platform TEXT, handle TEXT, display_name TEXT, first_seen REAL, last_seen REAL,
  PRIMARY KEY(platform, handle));
CREATE TABLE IF NOT EXISTS posts(
  platform TEXT, post_id TEXT, author TEXT, text TEXT, created_at REAL, parent_id TEXT,
  first_seen REAL, metrics TEXT, abuse REAL,
  PRIMARY KEY(platform, post_id));
CREATE INDEX IF NOT EXISTS posts_author ON posts(platform, author);
CREATE INDEX IF NOT EXISTS posts_parent ON posts(platform, parent_id);
CREATE TABLE IF NOT EXISTS reposts(
  platform TEXT, actor TEXT, post_id TEXT, first_seen REAL,
  PRIMARY KEY(platform, actor, post_id));
CREATE TABLE IF NOT EXISTS observers(
  platform TEXT, item TEXT, client_id TEXT,
  PRIMARY KEY(platform, item, client_id));
CREATE TABLE IF NOT EXISTS scores(
  platform TEXT, handle TEXT, computed_at REAL, status TEXT, labels TEXT, evidence TEXT,
  PRIMARY KEY(platform, handle));
CREATE TABLE IF NOT EXISTS clusters(
  platform TEXT, cluster_id TEXT, computed_at REAL, members TEXT, stats TEXT,
  PRIMARY KEY(platform, cluster_id));
CREATE TABLE IF NOT EXISTS appeals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, handle TEXT, message TEXT,
  contact TEXT, created_at REAL, status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS overrides(
  platform TEXT, handle TEXT, action TEXT, note TEXT, created_at REAL,
  PRIMARY KEY(platform, handle));
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        # A shared in-memory DB needs one persistent connection
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if path == ":memory:" else None
        with self.conn() as c:
            c.executescript(SCHEMA)
            if not self._mem:
                c.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            c = self._mem or sqlite3.connect(self.path)
            c.row_factory = sqlite3.Row
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                if not self._mem:
                    c.close()

    # ---------- ingest ----------
    def ingest(self, platform: str, client_id: str, posts: Iterable[dict], reposts: Iterable[dict]) -> dict:
        now = time.time()
        n_posts = n_reposts = 0
        with self.conn() as c:
            for p in posts:
                c.execute(
                    """INSERT INTO accounts(platform, handle, display_name, first_seen, last_seen)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(platform, handle) DO UPDATE SET
                         last_seen=excluded.last_seen,
                         display_name=COALESCE(excluded.display_name, accounts.display_name)""",
                    (platform, p["author"], p.get("display_name"), now, now),
                )
                c.execute(
                    """INSERT INTO posts(platform, post_id, author, text, created_at, parent_id, first_seen, metrics)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(platform, post_id) DO UPDATE SET
                         text=CASE WHEN posts.text IS NULL OR posts.text='' THEN excluded.text ELSE posts.text END,
                         parent_id=COALESCE(posts.parent_id, excluded.parent_id),
                         created_at=COALESCE(posts.created_at, excluded.created_at),
                         metrics=COALESCE(excluded.metrics, posts.metrics)""",
                    (platform, p["post_id"], p["author"], p.get("text") or "", p.get("created_at"),
                     p.get("parent_id"), now, json.dumps(p["metrics"]) if p.get("metrics") else None),
                )
                c.execute("INSERT OR IGNORE INTO observers VALUES(?,?,?)", (platform, "p:" + p["post_id"], client_id))
                n_posts += 1
            for r in reposts:
                c.execute(
                    """INSERT INTO accounts(platform, handle, first_seen, last_seen) VALUES(?,?,?,?)
                       ON CONFLICT(platform, handle) DO UPDATE SET last_seen=excluded.last_seen""",
                    (platform, r["actor"], now, now),
                )
                c.execute("INSERT OR IGNORE INTO reposts VALUES(?,?,?,?)", (platform, r["actor"], r["post_id"], now))
                c.execute("INSERT OR IGNORE INTO observers VALUES(?,?,?)",
                          (platform, f"r:{r['actor']}:{r['post_id']}", client_id))
                n_reposts += 1
        return {"posts": n_posts, "reposts": n_reposts}

    # ---------- analysis inputs ----------
    def platforms(self) -> list[str]:
        with self.conn() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT platform FROM posts")]

    def load_posts(self, platform: str, min_observers: int, since: float) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute(
                """SELECT p.post_id, p.author, p.text, p.created_at, p.parent_id, p.abuse
                   FROM posts p JOIN (
                     SELECT item, COUNT(*) n FROM observers WHERE platform=? AND item LIKE 'p:%' GROUP BY item
                   ) o ON o.item = 'p:' || p.post_id
                   WHERE p.platform=? AND o.n >= ? AND (p.created_at IS NULL OR p.created_at >= ?)""",
                (platform, platform, min_observers, since),
            ).fetchall()

    def load_reposts(self, platform: str, min_observers: int) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute(
                """SELECT r.actor, r.post_id FROM reposts r JOIN (
                     SELECT item, COUNT(*) n FROM observers WHERE platform=? AND item LIKE 'r:%' GROUP BY item
                   ) o ON o.item = 'r:' || r.actor || ':' || r.post_id
                   WHERE r.platform=? AND o.n >= ?""",
                (platform, platform, min_observers),
            ).fetchall()

    def set_abuse(self, platform: str, values: list[tuple[str, float]]) -> None:
        with self.conn() as c:
            c.executemany("UPDATE posts SET abuse=? WHERE platform=? AND post_id=?",
                          [(v, platform, pid) for pid, v in values])

    def prune(self, before: float) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM posts WHERE first_seen < ?", (before,))
            c.execute("DELETE FROM reposts WHERE first_seen < ?", (before,))
            c.execute("""DELETE FROM observers WHERE item LIKE 'p:%' AND NOT EXISTS (
                           SELECT 1 FROM posts p WHERE p.platform=observers.platform
                           AND 'p:' || p.post_id = observers.item)""")
            c.execute("""DELETE FROM observers WHERE item LIKE 'r:%' AND NOT EXISTS (
                           SELECT 1 FROM reposts r WHERE r.platform=observers.platform
                           AND 'r:' || r.actor || ':' || r.post_id = observers.item)""")

    # ---------- results ----------
    def write_results(self, platform: str, scores: dict[str, dict], clusters: list[dict]) -> None:
        now = time.time()
        with self.conn() as c:
            c.execute("DELETE FROM scores WHERE platform=?", (platform,))
            c.execute("DELETE FROM clusters WHERE platform=?", (platform,))
            c.executemany(
                "INSERT INTO scores VALUES(?,?,?,?,?,?)",
                [(platform, h, now, s["status"], json.dumps(s["labels"]), json.dumps(s["evidence"]))
                 for h, s in scores.items()],
            )
            c.executemany(
                "INSERT INTO clusters VALUES(?,?,?,?,?)",
                [(platform, cl["cluster_id"], now, json.dumps(cl["members"]), json.dumps(cl["stats"]))
                 for cl in clusters],
            )

    def get_scores(self, platform: str, handles: list[str]) -> dict[str, dict]:
        if not handles:
            return {}
        q = ",".join("?" * len(handles))
        with self.conn() as c:
            rows = c.execute(
                "SELECT handle, computed_at, status, labels, evidence FROM scores "
                f"WHERE platform=? AND handle IN ({q})",
                (platform, *handles),
            ).fetchall()
            overrides = {r[0]: r[1] for r in c.execute(
                f"SELECT handle, action FROM overrides WHERE platform=? AND handle IN ({q})", (platform, *handles))}
        out = {}
        for r in rows:
            out[r["handle"]] = {
                "computed_at": r["computed_at"], "status": r["status"],
                "labels": json.loads(r["labels"]), "evidence": json.loads(r["evidence"]),
            }
        for h, action in overrides.items():
            if action == "suppress":
                out[h] = {"computed_at": None, "status": "suppressed", "labels": [], "evidence": {}}
        return out

    def get_clusters(self, platform: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT cluster_id, computed_at, members, stats FROM clusters WHERE platform=?",
                             (platform,)).fetchall()
        return [{"cluster_id": r[0], "computed_at": r[1], "members": json.loads(r[2]), "stats": json.loads(r[3])}
                for r in rows]

    def add_appeal(self, platform: str, handle: str, message: str, contact: str | None) -> int:
        with self.conn() as c:
            cur = c.execute("INSERT INTO appeals(platform, handle, message, contact, created_at) VALUES(?,?,?,?,?)",
                            (platform, handle, message, contact, time.time()))
            return cur.lastrowid

    def list_appeals(self, status: str = "open") -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM appeals WHERE status=? ORDER BY id", (status,))]

    def set_override(self, platform: str, handle: str, action: str, note: str | None) -> None:
        with self.conn() as c:
            if action == "clear":
                c.execute("DELETE FROM overrides WHERE platform=? AND handle=?", (platform, handle))
            else:
                c.execute("INSERT OR REPLACE INTO overrides VALUES(?,?,?,?,?)",
                          (platform, handle, action, note, time.time()))
            c.execute("UPDATE appeals SET status='resolved' WHERE platform=? AND handle=? AND status='open'",
                      (platform, handle))

    def stats(self) -> dict:
        with self.conn() as c:
            def one(q: str) -> int:
                return c.execute(q).fetchone()[0]

            return {
                "accounts": one("SELECT COUNT(*) FROM accounts"),
                "posts": one("SELECT COUNT(*) FROM posts"),
                "reposts": one("SELECT COUNT(*) FROM reposts"),
                "labeled_accounts": one("SELECT COUNT(*) FROM scores WHERE status='labeled'"),
                "clusters": one("SELECT COUNT(*) FROM clusters"),
                "open_appeals": one("SELECT COUNT(*) FROM appeals WHERE status='open'"),
            }
