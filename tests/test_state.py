import time

from movementtix.models import Listing, PassType
from movementtix.state import State


def make_listing(site: str, price: float, url: str = "https://example.com",
                 pt: PassType = PassType.THREE_DAY) -> Listing:
    return Listing(
        site=site,
        pass_type=pt,
        base_price=price,
        fees=0.0,
        quantity=1,
        url=url,
    )


def test_record_and_prior_min(tmp_path):
    """prior_min returns the price recorded by the *previous* cycle, not
    an all-time minimum. A $250 listing that sold doesn't keep
    suppressing alerts forever once the floor rebounds to $280."""
    state = State(tmp_path / "s.db")
    assert state.prior_min("tixel", PassType.THREE_DAY) is None
    state.record(make_listing("tixel", 320))
    assert state.prior_min("tixel", PassType.THREE_DAY) == 320
    state.record(make_listing("tixel", 250))
    assert state.prior_min("tixel", PassType.THREE_DAY) == 250
    state.record(make_listing("tixel", 280))
    assert state.prior_min("tixel", PassType.THREE_DAY) == 280
    state.close()


def test_prior_min_partitions_by_pass_type(tmp_path):
    state = State(tmp_path / "s.db")
    state.record(make_listing("tixel", 250, pt=PassType.THREE_DAY))
    state.record(make_listing("tixel", 90, pt=PassType.SATURDAY))
    assert state.prior_min("tixel", PassType.THREE_DAY) == 250
    assert state.prior_min("tixel", PassType.SATURDAY) == 90
    state.close()


def test_dedupe_uses_site_pass_price_url(tmp_path):
    state = State(tmp_path / "s.db")
    a = make_listing("tixel", 250, "https://t/1")
    b = make_listing("tixel", 250, "https://t/2")  # different url
    assert State.alert_hash(a) != State.alert_hash(b)
    state.mark_alerted(a)
    assert state.recently_alerted(a, 6)
    assert not state.recently_alerted(b, 6)
    state.close()


def test_recently_alerted_respects_window(tmp_path, monkeypatch):
    state = State(tmp_path / "s.db")
    listing = make_listing("tixel", 250)
    state.mark_alerted(listing)
    assert state.recently_alerted(listing, 6)
    # Zero-hour window means never recent.
    assert not state.recently_alerted(listing, 0)
    state.close()
