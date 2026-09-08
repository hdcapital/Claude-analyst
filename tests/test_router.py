from pathlib import Path

from analyst.models import Lane
from analyst.router import Router
from tests.conftest import make_announcement

ROUTING = Path(__file__).resolve().parents[1] / "config" / "routing.yaml"


def router() -> Router:
    return Router.load(ROUTING)


def test_asx_3y_routes_deterministic() -> None:
    ann = make_announcement(title="Change of Director's Interest Notice")
    decision = router().route(ann)
    assert decision.lane is Lane.DETERMINISTIC
    assert decision.parser == "asx_3y"
    assert decision.rule == "asx.appendix_3y"


def test_asx_quarterly() -> None:
    ann = make_announcement(title="Appendix 4C - Quarterly Cash Flow Report")
    decision = router().route(ann)
    assert decision.parser == "asx_quarterly"


def test_asx_nta_goes_diff() -> None:
    ann = make_announcement(title="Net Tangible Asset Backing - August")
    decision = router().route(ann)
    assert decision.lane is Lane.DIFF
    assert decision.diff_group == "nta"


def test_uk_tr1() -> None:
    ann = make_announcement(market="uk", exchange="LSE", title="Holding(s) in Company")
    decision = router().route(ann)
    assert decision.parser == "rns_tr1"


def test_uk_buyback() -> None:
    ann = make_announcement(market="uk", exchange="LSE", title="Transaction in Own Shares")
    decision = router().route(ann)
    assert decision.parser == "rns_buyback"


def test_us_form4_by_form_type() -> None:
    ann = make_announcement(market="us", exchange="US", form="4", title="4 — Example")
    decision = router().route(ann)
    assert decision.parser == "us_form4"


def test_us_13d_prefix() -> None:
    ann = make_announcement(market="us", exchange="US", form="SC 13D/A", title="SC 13D/A")
    decision = router().route(ann)
    assert decision.parser == "us_13dg"


def test_us_10q_diff_lane() -> None:
    ann = make_announcement(market="us", exchange="US", form="10-Q", title="10-Q")
    decision = router().route(ann)
    assert decision.lane is Lane.DIFF
    assert decision.diff_group == "periodic"


def test_unmatched_goes_ai() -> None:
    ann = make_announcement(title="Completely novel strategic announcement")
    decision = router().route(ann)
    assert decision.lane is Lane.AI
    assert decision.rule == "default.unmatched"


def test_title_matching_never_uses_body() -> None:
    # body mentions a takeover; title is a plain 3Y — routing must ignore body
    ann = make_announcement(
        title="Appendix 3Y", text="proposed takeover scheme at a large premium"
    )
    decision = router().route(ann)
    assert decision.parser == "asx_3y"
