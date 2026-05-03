"""Reddit r/MovementDEMF feed: alerts on new ticket-resale and after-party
posts. Uses Reddit's app-only OAuth (free; create a "script" app at
https://www.reddit.com/prefs/apps and put the id+secret in .env).
"""
from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass

import httpx

from .config import Config
from .state import State

log = logging.getLogger(__name__)

UA = "movementtix/0.1 by u/anonymous (personal ticket monitor)"

PRICE_RE = re.compile(r"\$\s?(\d{2,4}(?:\.\d{2})?)")
AFTER_PARTY_RE = re.compile(r"after[\s-]?part(?:y|ies)|\bafters\b", re.IGNORECASE)


@dataclass(slots=True)
class RedditPost:
    id: str
    title: str
    selftext: str
    permalink: str
    flair: str | None
    author: str | None
    created_utc: float

    @property
    def url(self) -> str:
        return f"https://www.reddit.com{self.permalink}"


class RedditClient:
    """Minimal app-only OAuth Reddit client."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_expires: float = 0.0

    def _get_token(self) -> str | None:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        if not (self.client_id and self.client_secret):
            return None
        creds = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        try:
            r = httpx.post(
                "https://www.reddit.com/api/v1/access_token",
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {creds}", "User-Agent": UA},
                timeout=15,
            )
            r.raise_for_status()
            tok = r.json()
        except httpx.HTTPError as e:
            log.warning("reddit token fetch failed: %s", e)
            return None
        self._token = tok.get("access_token")
        self._token_expires = time.time() + int(tok.get("expires_in", 3600))
        return self._token

    def fetch_new(self, subreddit: str, limit: int = 50) -> list[RedditPost]:
        token = self._get_token()
        if not token:
            return []
        try:
            r = httpx.get(
                f"https://oauth.reddit.com/r/{subreddit}/new",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": UA,
                },
                params={"limit": limit, "raw_json": 1},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as e:
            log.warning("reddit /new fetch failed: %s", e)
            return []
        out: list[RedditPost] = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            out.append(
                RedditPost(
                    id=d.get("id", ""),
                    title=d.get("title", ""),
                    selftext=d.get("selftext", "") or "",
                    permalink=d.get("permalink", ""),
                    flair=d.get("link_flair_text"),
                    author=d.get("author"),
                    created_utc=d.get("created_utc", 0.0),
                )
            )
        return out


def matches_keywords(post: RedditPost, keywords: list[str]) -> bool:
    haystack = f"{post.title}\n{post.selftext}\n{post.flair or ''}".lower()
    return any(k.lower() in haystack for k in keywords)


def extract_prices(text: str) -> list[float]:
    return [float(p) for p in PRICE_RE.findall(text)]


def classify(post: RedditPost) -> str:
    text = f"{post.title} {post.selftext}".lower()
    if AFTER_PARTY_RE.search(text):
        return "after-party"
    if "saturday" in text:
        return "Saturday"
    if "sunday" in text:
        return "Sunday"
    if "monday" in text:
        return "Monday"
    if any(k in text for k in ("3-day", "3 day", "wristband", "weekend")):
        return "3-Day"
    return "ticket"


def format_post(post: RedditPost, kind: str) -> str:
    body = post.selftext.strip().replace("\n\n", "\n")
    if len(body) > 350:
        body = body[:350].rstrip() + "…"
    prices = extract_prices(post.title + " " + post.selftext)
    price_line = (
        f"Mentioned prices: {', '.join(f'${p:.0f}' for p in sorted(set(prices)))}\n"
        if prices
        else ""
    )
    flair_line = f"Flair: _{post.flair}_\n" if post.flair else ""
    return (
        f"*r/MovementDEMF — {kind}*\n"
        f"*{post.title}*\n"
        + flair_line
        + (f"u/{post.author}\n" if post.author else "")
        + price_line
        + (f"\n{body}\n\n" if body else "\n")
        + f"[Open thread]({post.url})"
    )


def poll_and_alert(cfg: Config, state: State, dry_run: bool, telegram) -> int:
    """Fetch newest posts, alert on first sighting of any matching post.

    Returns the number of alerts triggered (or "would trigger" in dry-run).
    """
    if not cfg.reddit.enabled:
        return 0
    if not (cfg.reddit_client_id and cfg.reddit_client_secret):
        log.info("reddit: REDDIT_CLIENT_ID/SECRET not set; skipping")
        return 0

    client = RedditClient(cfg.reddit_client_id, cfg.reddit_client_secret)
    posts = client.fetch_new(cfg.reddit.subreddit, cfg.reddit.fetch_limit)
    log.info("reddit: %d posts fetched from r/%s", len(posts), cfg.reddit.subreddit)

    alerted = 0
    for post in posts:
        if not post.id:
            continue
        if state.reddit_already_seen(post.id):
            continue
        if not matches_keywords(post, cfg.reddit.keywords):
            # Mark seen anyway so next cycle skips quickly
            state.mark_reddit_seen(post.id)
            continue

        kind = classify(post)
        text = format_post(post, kind)
        if dry_run:
            log.info("[dry-run] reddit alert:\n%s", text)
        else:
            try:
                telegram.send_message(text)
            except Exception as e:
                log.exception("telegram send failed for reddit post %s: %s", post.id, e)
                continue
        state.mark_reddit_seen(post.id)
        alerted += 1
    return alerted
