from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Listing, PassType, Tier


class State:
    def __init__(self, db_path: str | Path):
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        # Listener and scraper run in separate processes; busy_timeout
        # absorbs any brief write contention without surfacing "database
        # is locked" to callers.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()
        self._migrate()

    def _migrate(self) -> None:
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(listings)").fetchall()
        }
        if "tier" not in cols:
            self._conn.execute(
                "ALTER TABLE listings ADD COLUMN tier TEXT NOT NULL DEFAULT 'unknown'"
            )
        sub_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(subscribers)").fetchall()
        }
        # Default 1 preserves the prior behavior (every subscriber got
        # Reddit alerts) for rows that predate this column.
        if "reddit_alerts" not in sub_cols:
            self._conn.execute(
                "ALTER TABLE subscribers ADD COLUMN reddit_alerts INTEGER NOT NULL DEFAULT 1"
            )

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
              tier TEXT NOT NULL DEFAULT 'unknown',
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
              chat_id       INTEGER PRIMARY KEY,
              username      TEXT,
              first_name    TEXT,
              joined_at     TEXT NOT NULL,
              active        INTEGER NOT NULL DEFAULT 1,
              reddit_alerts INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS kv (
              k TEXT PRIMARY KEY,
              v TEXT NOT NULL
            );

            -- Per-subscriber pass-type/tier preferences. Absence of any
            -- row for a chat_id means "watch everything" (default on
            -- /start). Once a user calls /watch or /unwatch, their
            -- watchlist is the explicit set of rows here.
            CREATE TABLE IF NOT EXISTS subscribers_watch (
              chat_id   INTEGER NOT NULL,
              pass_type TEXT NOT NULL,
              tier      TEXT NOT NULL,
              PRIMARY KEY (chat_id, pass_type, tier)
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
    # Reddit-alert opt-in. A separate toggle from the ticket watchlist:
    # a subscriber can want price alerts but not the r/MovementDEMF feed
    # (or vice versa). Defaults on for every subscriber.
    # ------------------------------------------------------------------

    def set_reddit_alerts(self, chat_id: int, enabled: bool) -> bool:
        """Flip a subscriber's Reddit-alert preference. Returns True if the
        value actually changed, False if it was already in that state (or
        the chat isn't an active subscriber)."""
        cur = self._conn.execute(
            "UPDATE subscribers SET reddit_alerts=? WHERE chat_id=? AND active=1"
            " AND reddit_alerts!=?",
            (1 if enabled else 0, chat_id, 1 if enabled else 0),
        )
        return cur.rowcount > 0

    def get_reddit_alerts(self, chat_id: int) -> bool | None:
        """True/False for an active subscriber's preference, or None if the
        chat isn't an active subscriber."""
        row = self._conn.execute(
            "SELECT reddit_alerts FROM subscribers WHERE chat_id=? AND active=1",
            (chat_id,),
        ).fetchone()
        return bool(row[0]) if row else None

    def reddit_subscribers(self) -> list[int]:
        return [
            row[0]
            for row in self._conn.execute(
                "SELECT chat_id FROM subscribers WHERE active=1 AND reddit_alerts=1"
                " ORDER BY joined_at"
            ).fetchall()
        ]

    # ------------------------------------------------------------------
    # Watchlist: which (pass_type, tier) combos each subscriber wants
    # ------------------------------------------------------------------

    def add_watch(self, chat_id: int, pass_type: PassType, tier: Tier) -> bool:
        """Insert a watch row. Returns True if newly added, False if it
        already existed."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO subscribers_watch(chat_id, pass_type, tier)"
            " VALUES (?,?,?)",
            (chat_id, pass_type.value, tier.value),
        )
        return cur.rowcount > 0

    def remove_watch(self, chat_id: int, pass_type: PassType, tier: Tier) -> bool:
        cur = self._conn.execute(
            "DELETE FROM subscribers_watch"
            " WHERE chat_id=? AND pass_type=? AND tier=?",
            (chat_id, pass_type.value, tier.value),
        )
        return cur.rowcount > 0

    def list_watch(self, chat_id: int) -> list[tuple[PassType, Tier]]:
        rows = self._conn.execute(
            "SELECT pass_type, tier FROM subscribers_watch WHERE chat_id=?"
            " ORDER BY pass_type, tier",
            (chat_id,),
        ).fetchall()
        return [(PassType(pt), Tier(t)) for pt, t in rows]

    def has_any_watch(self, chat_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM subscribers_watch WHERE chat_id=? LIMIT 1",
            (chat_id,),
        ).fetchone()
        return row is not None

    def watching_subscribers(self, pass_type: PassType, tier: Tier) -> list[int]:
        """Return active chat_ids that should receive an alert for this
        (pass_type, tier). A subscriber with no watch rows defaults to
        watching everything (full backwards-compat with pre-watchlist
        subscribers). When the listing's tier is UNKNOWN, we include
        anyone watching this pass_type at any tier (best-effort: the
        scraper couldn't classify, so we err on the side of delivery)."""
        # Subscribers with explicit watchlist matching this combo.
        if tier is Tier.UNKNOWN:
            tier_clause = "1=1"  # any tier matches
            tier_args: tuple = ()
        else:
            tier_clause = "(w.tier = ? OR w.tier = ?)"
            tier_args = (tier.value, Tier.UNKNOWN.value)
        explicit = {
            row[0]
            for row in self._conn.execute(
                "SELECT s.chat_id FROM subscribers s JOIN subscribers_watch w"
                " ON s.chat_id = w.chat_id"
                f" WHERE s.active=1 AND w.pass_type=? AND {tier_clause}",
                (pass_type.value, *tier_args),
            ).fetchall()
        }
        # Subscribers with no watchlist at all → default = watch everything.
        defaulted = {
            row[0]
            for row in self._conn.execute(
                "SELECT s.chat_id FROM subscribers s WHERE s.active=1"
                " AND NOT EXISTS (SELECT 1 FROM subscribers_watch w"
                "                 WHERE w.chat_id = s.chat_id)"
            ).fetchall()
        }
        return sorted(explicit | defaulted)

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

    def kv_delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM kv WHERE k=?", (key,))

    # ------------------------------------------------------------------
    # Global (cross-site) lowest price per pass_type. Updated at the end
    # of every cycle. NEW_LOW alerts compare the cycle's global min
    # against the prior cycle's global min — a per-site drop on a
    # site that's still pricier than another site does NOT fire.
    # ------------------------------------------------------------------

    @staticmethod
    def _global_min_key(pass_type: PassType) -> str:
        return f"global_min_{pass_type.value}"

    def prior_global_min(self, pass_type: PassType) -> float | None:
        s = self.kv_get(self._global_min_key(pass_type))
        return float(s) if s else None

    def set_global_min(self, pass_type: PassType, price: float) -> None:
        self.kv_set(self._global_min_key(pass_type), f"{price:.2f}")

    # ------------------------------------------------------------------
    # Latest known snapshot per site (the basis for cross-platform
    # "cheapest currently available"). Includes this cycle's freshly
    # recorded rows; falls back to past observations for sites that
    # missed this cycle. A max_age cutoff drops sites that have been
    # silent long enough that their last price is probably stale (e.g.
    # the listing sold or the site is offline).
    # ------------------------------------------------------------------

    def latest_per_site(
        self,
        pass_type: PassType,
        max_age_seconds: int | None = 1800,
    ) -> list[dict]:
        """One dict per site that has a row for this pass_type. Each
        dict carries the full row from the latest observation. Sites
        whose last row is older than `max_age_seconds` are excluded
        (set to None to disable the cutoff)."""
        rows = self._conn.execute(
            "SELECT site, base_price, fees, total_price, quantity,"
            " url, section, tier, MAX(fetched_at) as fetched_at"
            " FROM listings WHERE pass_type=? GROUP BY site",
            (pass_type.value,),
        ).fetchall()
        if not rows:
            return []
        cutoff = None
        if max_age_seconds is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        out: list[dict] = []
        for site, base, fees, total, qty, url, section, tier, ts in rows:
            if cutoff is not None:
                try:
                    if datetime.fromisoformat(ts) < cutoff:
                        continue
                except ValueError:
                    continue
            out.append({
                "site": site,
                "base_price": float(base),
                "fees": float(fees),
                "total_price": float(total),
                "quantity": int(qty),
                "url": url,
                "section": section,
                "tier": tier or "unknown",
                "fetched_at": ts,
            })
        return out

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
            " total_price, quantity, section, tier, url, raw_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                listing.site,
                listing.pass_type.value,
                listing.fetched_at.isoformat(),
                listing.base_price,
                listing.fees,
                listing.total_price,
                listing.quantity,
                listing.section,
                listing.tier.value,
                listing.url,
                json.dumps(listing.raw, default=str),
            ),
        )
        return cur.lastrowid or 0

    def prior_min(self, site: str, pass_type: PassType) -> float | None:
        """Return the cheapest available price the previous cycle saw for
        this (site, pass_type), or None if this is the first sighting.

        Each cycle the scraper records ONE row (the cheapest currently
        listed). The most recent row already in the table — written by
        the prior cycle — is therefore the previous cycle's floor. Using
        this instead of `MIN(total_price)` over all of history avoids
        the "ghost minimum" problem: a $X listing that sold can no
        longer be bought, so future prices shouldn't be measured
        against it."""
        row = self._conn.execute(
            "SELECT total_price FROM listings WHERE site=? AND pass_type=?"
            " ORDER BY fetched_at DESC, id DESC LIMIT 1",
            (site, pass_type.value),
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def current_floor(self, site: str, pass_type: PassType) -> tuple[float, str, int, str, str] | None:
        """Latest (cheapest currently available) snapshot for (site, pass_type).

        Returns (total_price, fetched_at_iso, quantity, url, tier) or
        None if no listings yet."""
        row = self._conn.execute(
            "SELECT total_price, fetched_at, quantity, url, tier"
            " FROM listings WHERE site=? AND pass_type=?"
            " ORDER BY fetched_at DESC, id DESC LIMIT 1",
            (site, pass_type.value),
        ).fetchone()
        return row if row else None

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
