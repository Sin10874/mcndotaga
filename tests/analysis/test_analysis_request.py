from datetime import date

import pytest

from analysis.analysis_request import parse_analysis_request
from analysis.analysis_service import as_policy_request, as_value_request, checked
from db.sources import SourceNotAllowed
from shared.draft_template import resolve


def request():
    return {"us": 10, "them": 20, "us_side": 1, "patch": "7.41e", "first_pick_team": 0,
            "draft": [], "sources": ["pro_match"], "as_of": "2026-09-22"}


@pytest.mark.parametrize("field,value", [("us", True), ("us_side", True), ("first_pick_team", True), ("top_n", True), ("as_of", "2026-09-23"), ("sources", []), ("patch", "7.41%")])
def test_request_rejects_ambiguous_or_future_values(field, value):
    body = request()
    body[field] = value
    with pytest.raises(ValueError):
        parse_analysis_request(body, "analysis", today=date(2026, 9, 22))


def test_identity_mapping_is_explicit_on_dire():
    body = request()
    body["draft"] = [{"ord": i, "is_pick": resolve(i, 0)[0], "team": resolve(i, 0)[1], "hero_id": i + 1} for i in range(9)]
    clean = parse_analysis_request(body, "analysis", today=date(2026, 9, 22))
    value = as_value_request(clean)
    policy = as_policy_request(clean)
    assert value["radiant"] == {"team_id": 20, "heroes": [{"hero_id": 8}]}
    assert value["dire"] == {"team_id": 10, "heroes": [{"hero_id": 9}]}
    assert policy["radiant_team_id"] == 20


def test_duplicates_and_mixed_scrim_are_rejected():
    body = request()
    body["draft"] = [{"ord": i, "is_pick": False, "team": 0, "hero_id": 1} for i in range(2)]
    with pytest.raises(ValueError, match="重复"):
        parse_analysis_request(body, "analysis")
    body = request()
    body["sources"] = ["pro_match", "scrim"]
    with pytest.raises(SourceNotAllowed):
        parse_analysis_request(body, "analysis")


def test_complete_draft_is_accepted_only_for_bundle():
    body = request()
    body["draft"] = [{"ord": i, "is_pick": resolve(i, 0)[0], "team": resolve(i, 0)[1], "hero_id": i + 1} for i in range(24)]
    assert len(parse_analysis_request(body, "analysis")["draft"]) == 24
    with pytest.raises(ValueError):
        parse_analysis_request(body, "advise")


def test_response_validator_rejects_used_hero_even_if_distribution_is_valid():
    from analysis.profile_service import ProfileServiceError
    body = request()
    body["draft"] = [{"ord": 0, "team": 0, "is_pick": False, "hero_id": 1}]
    response = {"next_ord": 1, "team": 0, "is_pick": False, "top_n": 1,
                "candidates": [{"hero_id": 1, "prob": 1., "reasons": ["历史"], "evidence_match_ids": []}],
                "other_prob": 0., "model": None, "baseline": {"frequency_top1": None}, "sources_used": ["pro_match"]}
    with pytest.raises(ProfileServiceError, match="冲突"):
        checked("Policy", response, body)
