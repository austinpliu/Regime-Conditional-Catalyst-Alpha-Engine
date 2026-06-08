from datetime import date, timedelta

import pytest

from src.scoring.catalyst_score import (
    calculate_catalyst_score,
    days_until_event,
    estimate_source_credibility,
    event_type_score,
    normalize_unit_score,
    proximity_score,
)


def test_score_is_bounded_between_zero_and_one_hundred() -> None:
    score = calculate_catalyst_score(
        event_type="exchange_listing",
        source_credibility=100,
        days_until=0,
        confidence_score=100,
    )

    assert score == pytest.approx(98.25)
    assert 0 <= score <= 100


def test_exchange_listing_scores_above_other_with_same_inputs() -> None:
    high_impact = calculate_catalyst_score(
        event_type="exchange_listing",
        source_credibility=0.7,
        days_until=10,
        confidence_score=0.8,
    )
    low_impact = calculate_catalyst_score(
        event_type="other",
        source_credibility=0.7,
        days_until=10,
        confidence_score=0.8,
    )

    assert high_impact > low_impact


def test_near_events_score_above_far_events_with_same_inputs() -> None:
    near = calculate_catalyst_score(
        event_type="mainnet_upgrade",
        source_credibility=0.8,
        days_until=5,
        confidence_score=0.8,
    )
    far = calculate_catalyst_score(
        event_type="mainnet_upgrade",
        source_credibility=0.8,
        days_until=80,
        confidence_score=0.8,
    )

    assert near > far


def test_normalize_unit_score_accepts_percent_style_values() -> None:
    assert normalize_unit_score(0.85) == pytest.approx(0.85)
    assert normalize_unit_score(85) == pytest.approx(0.85)


def test_normalize_unit_score_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError):
        normalize_unit_score(120)


def test_proximity_score_zeroes_past_events() -> None:
    assert proximity_score(-1) == 0


def test_days_until_event_uses_supplied_as_of_date() -> None:
    as_of = date(2026, 6, 8)
    event_date = as_of + timedelta(days=14)

    assert days_until_event(event_date, as_of=as_of) == 14


def test_estimate_source_credibility_uses_known_domains() -> None:
    assert estimate_source_credibility("https://www.coinbase.com/listings/example") == pytest.approx(0.95)
    assert estimate_source_credibility("https://reddit.com/r/example") == pytest.approx(0.3)
    assert estimate_source_credibility("https://example.com/post") == pytest.approx(0.5)


def test_unknown_event_type_uses_other_weight() -> None:
    assert event_type_score("not_a_known_type") == event_type_score("other")
