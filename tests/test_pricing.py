from movementtix.config import Caps
from movementtix.models import AlertReason, Listing, PassType
from movementtix.pricing import estimate_fees, should_alert


def make_listing(price: float, pt: PassType = PassType.THREE_DAY) -> Listing:
    return Listing(
        site="test",
        pass_type=pt,
        base_price=price,
        fees=0.0,
        quantity=1,
        url="https://example.com",
    )


def test_estimate_fees_uses_known_rate():
    assert estimate_fees(100.0, "stubhub") == 28.0
    assert estimate_fees(100.0, "tixel") == 10.0


def test_estimate_fees_unknown_site_default():
    assert estimate_fees(100.0, "unknown_site") == 25.0


def test_under_cap_three_day():
    caps = Caps(three_day=300, saturday=150)
    listing = make_listing(250)
    assert should_alert(listing, prior_min=400, caps=caps) is AlertReason.UNDER_CAP


def test_under_cap_saturday():
    caps = Caps(three_day=300, saturday=150)
    listing = make_listing(149.99, PassType.SATURDAY)
    assert should_alert(listing, prior_min=200, caps=caps) is AlertReason.UNDER_CAP


def test_new_low_when_above_cap_but_cheaper_than_history():
    caps = Caps(three_day=300, saturday=150)
    listing = make_listing(350)
    assert should_alert(listing, prior_min=400, caps=caps) is AlertReason.NEW_LOW


def test_new_low_when_no_history():
    caps = Caps(three_day=300, saturday=150)
    listing = make_listing(400)
    assert should_alert(listing, prior_min=None, caps=caps) is AlertReason.NEW_LOW


def test_no_alert_when_above_cap_and_not_a_new_low():
    caps = Caps(three_day=300, saturday=150)
    listing = make_listing(450)
    assert should_alert(listing, prior_min=400, caps=caps) is None


def test_alert_under_cap_takes_precedence_over_new_low_check():
    caps = Caps(three_day=300, saturday=150)
    listing = make_listing(280)
    assert should_alert(listing, prior_min=290, caps=caps) is AlertReason.UNDER_CAP


def test_caps_for_pass():
    caps = Caps(three_day=300, saturday=150)
    assert caps.for_pass(PassType.THREE_DAY) == 300
    assert caps.for_pass(PassType.SATURDAY) == 150
