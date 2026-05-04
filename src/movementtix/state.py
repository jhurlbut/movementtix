from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Listing, PassType


class State:
    def __init__(self, db_path: str | Path):
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              site TEXT NOT NULL,
              pass_type TEXT NOT NULL,
              fetched_at TEXT NOT NULL,
              base_price REAL NOT NULL,
              fees REAL NOT NULL,
              total_price REAL NOT NULL,
              quantity INTEGER NOT NULL,
              section TEXT,
              url TEXT NOT NULL,
              raw_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_listings_site_pt
              ON listings(site, pass_type, total_price);

            CREATE TABLE IF NOT EXISTS alerts (
              hash TEXT PRIMARY KEY,
              sent_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reddit_seen (
              post_id TEXT PRIMARY KEY,
              alerted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscribers (
              chat_id    INTEGER PRIMARY KEY,
              username   TEXT,
              first_name TEXT,
              joined_at  TEXT NOT NULL,
              active     INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS kv (
              k TEXT PRIMARY KEY,
              v TEXT NOT NULL
            );
            """
        )

    # ------------------------------------------------------------------
    # Subscribers
    # ------------------------------------------------------------------

    def add_subscriber(self, chat_id: int, username: str | None,
                       first_name: str | None) -> bool:
        """Add or reactivate a subscriber. Returns True if newly added or
        reactivated, False if they were already active."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "SELECT active FROM subscribers WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if cur is None:
            self._conn.execute(
                "INSERT INTO subscribers(chat_id, username, first_name, joined_at, active)"
                " VALUES (?,?,?,?,1)",
                (chat_id, username or "", first_name or "", now),
            )
            return True
        if cur[0] == 0:
            self._conn.execute(
                "UPDATE subscribers SET active=1, username=?, first_name=? WHERE chat_id=?",
                (username or "", first_name or "", chat_id),
            )
            return True
        # Already active — refresh metadata silently.
        self._conn.execute(
            "UPDATE subscribers SET username=?, first_name=? WHERE chat_id=?",
            (username or "", first_name or "", chat_id),
        )
        return False

    def remove_subscriber(self, chat_id: int) -> bool:
        cur = self._conn.execute(
            "UPDATE subscribers SET active=0 WHERE chat_id=? AND active=1", (chat_id,)
        )
        return cur.rowcount > 0

    def active_subscribers(self) -> list[int]:
        return [
            row[0]
            for row in self._conn.execute(
                "SELECT chat_id FROM subscribers WHERE active=1 ORDER BY joined_at"
            ).fetchall()
        ]

    def subscriber_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM subscribers WHERE active=1"
        ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Generic key/value (used for the Telegram getUpdates offset)
    # ------------------------------------------------------------------

    def kv_get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (key, value)
        )

    def reddit_already_seen(self, post_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM reddit_seen WHERE post_id=?", (post_id,)
        ).fetchone()
        return row is not None

    def mark_reddit_seen(self, post_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO reddit_seen(post_id, alerted_at) VALUES (?, ?)",
            (post_id, datetime.now(timezone.utc).isoformat()),
        )

    def record(self, listing: Listing) -> int:
        cur = self._conn.execute(
            "INSERT INTO listings(site, pass_type, fetched_at, base_price, fees,"
            " total_price, quantity, section, url, raw_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                listing.site,
                listing.pass_type.value,
                listing.fetched_at.isoformat(),
                listing.base_price,
                listing.fees,
                listing.total_price,
                listing.quantity,
                listing.section,
                listing.url,
                json.dumps(listing.raw, default=str),
            ),
        )
        return cur.lastrowid or 0

    def prior_min(self, site: str, pass_type: PassType) -> float | None:
        row = self._conn.execute(
            "SELECT MIN(total_price) FROM listings WHERE site=? AND pass_type=?",
            (site, pass_type.value),
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def global_min(self, pass_type: PassType) -> float | None:
        row = self._conn.execute(
            "SELECT MIN(total_price) FROM listings WHERE pass_type=?",
            (pass_type.value,),
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    @staticmethod
    def alert_hash(listing: Listing) -> str:
        key = f"{listing.site}|{listing.pass_type.value}|{listing.total_price}|{listing.url}"
        return hashlib.sha1(key.encode()).hexdigest()

    def recently_alerted(self, listing: Listing, dedupe_hours: int) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=dedupe_hours)
        row = self._conn.execute(
            "SELECT sent_at FROM alerts WHERE hash=?",
            (self.alert_hash(listing),),
        ).fetchone()
        if not row:
            return False
        return datetime.fromisoformat(row[0]) > cutoff

    def mark_alerted(self, listing: Listing) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO alerts(hash, sent_at) VALUES (?, ?)",
            (self.alert_hash(listing), datetime.now(timezone.utc).isoformat()),
        )

    def close(self) -> None:
        self._conn.close()
