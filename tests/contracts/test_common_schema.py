import pytest, jsonschema

def test_degraded_requires_needs_only_for_needs_replay(validator_for):
    v = validator_for("Degraded")
    v.validate({"value": None, "reason": "needs_replay", "needs": "Phase B"})
    v.validate({"value": None, "reason": "insufficient_samples"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"value": None, "reason": "insufficient_samples", "needs": "Phase B"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"value": None, "reason": "needs_replay"})

def test_degraded_rejects_non_null_value(validator_for):
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Degraded").validate({"value": 0, "reason": "insufficient_samples"})

def test_error_codes_are_closed(validator_for):
    with pytest.raises(jsonschema.ValidationError):
        validator_for("ErrorEnvelope").validate({"error": {"code": "boom", "message": "x"}})

def test_all_seven_their_opening_values_are_defined(common):
    assert set(common["TheirOpening"]["enum"]) == {
        "teamfight","push","pickoff","splitpush","protect","initiate","unknown"}

def test_reason_enum_closure_via_ref(validator_for):
    """四个 unavailable_reason 都必须被接受（逐个守护枚举成员），且经
    Degraded.reason 的 $ref 拒绝非法值——把该 $ref 换成 {type: string} 会让本测试变红。"""
    v = validator_for("Degraded")
    for reason in ["needs_replay", "insufficient_samples",
                   "source_not_allowed", "stat_unavailable"]:
        payload = {"value": None, "reason": reason}
        if reason == "needs_replay":
            payload["needs"] = "Phase B"
        v.validate(payload)
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"value": None, "reason": "bogus"})
