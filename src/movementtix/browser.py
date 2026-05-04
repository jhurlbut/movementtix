"""Playwright browser launcher with three modes:

1. CDP attach to a running Chrome (best for anti-bot evasion — uses your
   real logged-in session). Start Chrome with:
     google-chrome --remote-debugging-port=9222 \\
                   --user-data-dir=/path/to/some/profile
   Then set `browser.cdp_url: http://localhost:9222` in config.yaml.

2. Persistent context — Playwright launches its own Chromium against a
   directory that survives between runs. Cookies + fingerprint persist.

3. Ephemeral headless Chromium — last-resort default.

All modes share a context-manager API that yields a `Page`.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Iterator

from .config import BrowserConfig

log = logging.getLogger(__name__)


@contextlib.contextmanager
def open_page(cfg: BrowserConfig, *, stealth: bool = True) -> Iterator:
    """Yield a Playwright `Page` according to the configured mode.

    On import / launch failure, yields None so callers can degrade.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.info("playwright not installed; browser features disabled")
        yield None
        return

    pw = sync_playwright().start()
    browser = None
    context = None
    page = None
    launch_failed = False
    try:
        if cfg.cdp_url:
            log.info("browser: attaching to CDP at %s", cfg.cdp_url)
            browser = pw.chromium.connect_over_cdp(cfg.cdp_url)
            context = (
                browser.contexts[0]
                if browser.contexts
                else browser.new_context(ignore_https_errors=True)
            )
            page = context.new_page()
        elif cfg.user_data_dir:
            log.info("browser: persistent context at %s", cfg.user_data_dir)
            kwargs = {"headless": cfg.headless, "ignore_https_errors": True}
            if cfg.channel:
                kwargs["channel"] = cfg.channel
            context = pw.chromium.launch_persistent_context(cfg.user_data_dir, **kwargs)
            page = context.new_page()
        else:
            log.info("browser: ephemeral headless chromium (no profile)")
            launch_kwargs = {"headless": cfg.headless}
            if cfg.channel:
                launch_kwargs["channel"] = cfg.channel
            browser = pw.chromium.launch(**launch_kwargs)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()

        if stealth and page is not None:
            try:
                from playwright_stealth import stealth_sync  # type: ignore
                stealth_sync(page)
            except ImportError:
                pass
    except Exception as e:
        log.warning("browser launch failed: %s", e)
        launch_failed = True

    try:
        yield (None if launch_failed else page)
    finally:
        # Only tear down what we created — don't kill an attached CDP browser.
        try:
            if page is not None and cfg.cdp_url:
                page.close()
        except Exception:
            pass
        try:
            if context is not None and not cfg.cdp_url:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None and not cfg.cdp_url:
                browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
