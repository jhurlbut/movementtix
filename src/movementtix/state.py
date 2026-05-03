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
            """
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
