"""可复算的 BP 下一手条件概率模型与严格时间留出评估。"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import math
from typing import Any, Iterable


ALGORITHM_VERSION = "conditional-sequence-v2"
TOP_K = (1, 3, 5)
STAGES = ("1-8", "9-16", "17-24")
_ALPHA = 0.25
_BASE_WEIGHT = 0.45
_TEAM_WEIGHT = 0.35
_CONTEXT_WEIGHT = 0.20
_PATCH_MIN_SAMPLES = 30


def stage_for(ord_: int) -> str:
    if type(ord_) is not int or not 0 <= ord_ < 24:
        raise ValueError("ord 必须在 0..23")
    return STAGES[ord_ // 8]


def _key(*parts: object) -> str:
    return "|".join(str(part) for part in parts)


def _counter_dict(counter: Counter[int]) -> dict[str, int]:
    return {str(hero_id): count for hero_id, count in sorted(counter.items())}


def _action_team_id(match: dict[str, Any], team: int) -> int | None:
    raw = match.get("radiant_team_id") if team == 0 else match.get("dire_team_id")
    return int(raw) if type(raw) is int else None


def fit_conditional_model(
    matches: Iterable[dict[str, Any]], hero_ids: Iterable[int]
) -> dict[str, Any]:
    """拟合固定结构模型，不搜索参数，也不读取评估留出集。"""

    vocab = sorted(set(int(hero_id) for hero_id in hero_ids))
    if not vocab or any(hero_id <= 0 for hero_id in vocab):
        raise ValueError("hero_ids 必须是非空正整数集合")

    by_ord: defaultdict[str, Counter[int]] = defaultdict(Counter)
    by_team_ord: defaultdict[str, Counter[int]] = defaultdict(Counter)
    by_context: defaultdict[str, Counter[int]] = defaultdict(Counter)
    n_matches = 0
    for match in matches:
        patch = str(match["patch"])
        actions = sorted(match["actions"], key=lambda row: int(row["ord"]))
        seen: list[int] = []
        for action in actions:
            ord_ = int(action["ord"])
            hero_id = int(action["hero_id"])
            if hero_id not in vocab:
                raise ValueError(f"训练行含词表外英雄：{hero_id}")
            by_ord[_key(patch, ord_)][hero_id] += 1
            by_ord[_key("*", ord_)][hero_id] += 1
            team_id = _action_team_id(match, int(action["team"]))
            if team_id is not None:
                by_team_ord[_key(patch, ord_, team_id)][hero_id] += 1
                by_team_ord[_key("*", ord_, team_id)][hero_id] += 1
            for prefix_hero in seen:
                by_context[_key(patch, ord_, prefix_hero)][hero_id] += 1
                by_context[_key("*", ord_, prefix_hero)][hero_id] += 1
            seen.append(hero_id)
        n_matches += 1

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "config": {
            "alpha": _ALPHA,
            "base_weight": _BASE_WEIGHT,
            "team_weight": _TEAM_WEIGHT,
            "context_weight": _CONTEXT_WEIGHT,
            "patch_min_samples": _PATCH_MIN_SAMPLES,
            "parameter_search": False,
        },
        "hero_ids": vocab,
        "n_matches": n_matches,
        "counts": {
            "by_ord": {key: _counter_dict(value) for key, value in sorted(by_ord.items())},
            "by_team_ord": {
                key: _counter_dict(value) for key, value in sorted(by_team_ord.items())
            },
            "by_context": {
                key: _counter_dict(value) for key, value in sorted(by_context.items())
            },
        },
    }


def _distribution(
    raw_counts: dict[str, int] | None, legal: list[int], alpha: float
) -> dict[int, float]:
    counts = raw_counts or {}
    total = sum(int(counts.get(str(hero_id), 0)) for hero_id in legal)
    denominator = total + alpha * len(legal)
    if denominator <= 0:
        uniform = 1.0 / len(legal)
        return {hero_id: uniform for hero_id in legal}
    return {
        hero_id: (int(counts.get(str(hero_id), 0)) + alpha) / denominator
        for hero_id in legal
    }


def _with_global_backoff(
    table: dict[str, dict[str, int]], patch_key: str, global_key: str, min_samples: int
) -> dict[str, int] | None:
    patch_counts = table.get(patch_key)
    if patch_counts and sum(int(value) for value in patch_counts.values()) >= min_samples:
        return patch_counts
    return table.get(global_key)


def predict_distribution(
    model: dict[str, Any],
    *,
    patch: str,
    ord_: int,
    acting_team_id: int | None,
    seen_hero_ids: set[int],
) -> dict[int, float]:
    """输出所有合法候选的概率，已出现英雄概率为零并从结果中移除。"""

    if model.get("algorithm_version") != ALGORITHM_VERSION:
        raise ValueError("模型算法版本不兼容")
    stage_for(ord_)
    vocab = [int(hero_id) for hero_id in model["hero_ids"]]
    if not seen_hero_ids.issubset(set(vocab)):
        raise ValueError("前缀含词表外英雄")
    legal = [hero_id for hero_id in vocab if hero_id not in seen_hero_ids]
    if not legal:
        raise ValueError("没有合法候选英雄")

    config = model["config"]
    alpha = float(config["alpha"])
    patch_min_samples = int(config["patch_min_samples"])
    counts = model["counts"]
    base_counts = _with_global_backoff(
        counts["by_ord"],
        _key(patch, ord_),
        _key("*", ord_),
        patch_min_samples,
    )
    base = _distribution(base_counts, legal, alpha)

    components: list[tuple[float, dict[int, float]]] = []
    if acting_team_id is not None:
        team_counts = _with_global_backoff(
            counts["by_team_ord"],
            _key(patch, ord_, acting_team_id),
            _key("*", ord_, acting_team_id),
            patch_min_samples,
        )
        if team_counts:
            components.append(
                (float(config["team_weight"]), _distribution(team_counts, legal, alpha))
            )

    context_rows = [
        _with_global_backoff(
            counts["by_context"],
            _key(patch, ord_, hero_id),
            _key("*", ord_, hero_id),
            patch_min_samples,
        )
        for hero_id in sorted(seen_hero_ids)
    ]
    context_rows = [row for row in context_rows if row]
    if context_rows:
        context_distributions = [_distribution(row, legal, alpha) for row in context_rows]
        context = {
            hero_id: sum(row[hero_id] for row in context_distributions) / len(context_distributions)
            for hero_id in legal
        }
        components.append((float(config["context_weight"]), context))

    used_weight = sum(weight for weight, _ in components)
    probabilities = {
        hero_id: (1.0 - used_weight) * base[hero_id]
        + sum(weight * component[hero_id] for weight, component in components)
        for hero_id in legal
    }
    total = sum(probabilities.values())
    return {hero_id: value / total for hero_id, value in probabilities.items()}


def _new_bucket() -> dict[str, Any]:
    return {
        "n_predictions": 0,
        "model_hits": {str(k): 0 for k in TOP_K},
        "baseline_hits": {str(k): 0 for k in TOP_K},
        "global_hits": {str(k): 0 for k in TOP_K},
        "random_expected_hits": {str(k): 0.0 for k in TOP_K},
    }


def _score(
    bucket: dict[str, Any],
    target: int,
    model_rank: list[int],
    baseline_rank: list[int],
    global_rank: list[int],
) -> None:
    bucket["n_predictions"] += 1
    for k in TOP_K:
        bucket["model_hits"][str(k)] += int(target in model_rank[:k])
        bucket["baseline_hits"][str(k)] += int(target in baseline_rank[:k])
        bucket["global_hits"][str(k)] += int(target in global_rank[:k])
        bucket["random_expected_hits"][str(k)] += min(k, len(model_rank)) / len(model_rank)


def _finalize(bucket: dict[str, Any]) -> dict[str, Any]:
    n = int(bucket["n_predictions"])
    return {
        "n_predictions": n,
        "model": {
            f"top{k}": (round(bucket["model_hits"][str(k)] / n, 8) if n else None)
            for k in TOP_K
        },
        "uniform_random_expected": {
            f"top{k}": (round(bucket["random_expected_hits"][str(k)] / n, 8) if n else None)
            for k in TOP_K
        },
        "global_frequency": {
            f"top{k}": (round(bucket["global_hits"][str(k)] / n, 8) if n else None)
            for k in TOP_K
        },
        "stage_pickban_frequency": {
            f"top{k}": (round(bucket["baseline_hits"][str(k)] / n, 8) if n else None)
            for k in TOP_K
        },
    }


def _split_chronologically(rows: list[dict[str, Any]]) -> tuple[list[dict], list[dict], str]:
    if len(rows) < 2:
        raise ValueError("至少需要 2 场才能做时间留出")
    ordered = sorted(rows, key=lambda row: (str(row["started_at"]), int(row["match_id"])))
    target = math.floor(len(ordered) * 0.8)
    if target <= 0 or target >= len(ordered):
        raise ValueError("80/20 时间切分无法形成非空训练集与留出集")
    split_at = str(ordered[target]["started_at"])
    train = [row for row in ordered if str(row["started_at"]) < split_at]
    holdout = [row for row in ordered if str(row["started_at"]) >= split_at]
    if not train or not holdout:
        raise ValueError("同一时间戳不跨界后出现空集合")
    return train, holdout, split_at


def evaluate_chronological(
    matches: Iterable[dict[str, Any]],
    hero_ids: Iterable[int],
    *,
    independent_holdout: bool = False,
) -> dict[str, Any]:
    """固定算法的时间留出评估，留出集从不参与拟合或参数选择。"""

    rows = list(matches)
    vocab = sorted(set(int(hero_id) for hero_id in hero_ids))
    train, holdout, split_at = _split_chronologically(rows)
    model = fit_conditional_model(train, vocab)

    stage_counts: defaultdict[tuple[str, bool], Counter[int]] = defaultdict(Counter)
    global_counts: Counter[int] = Counter()
    for match in train:
        for action in match["actions"]:
            global_counts[int(action["hero_id"])] += 1
            stage_counts[(stage_for(int(action["ord"])), bool(action["is_pick"]))][
                int(action["hero_id"])
            ] += 1

    overall = _new_bucket()
    stages = {stage: _new_bucket() for stage in STAGES}
    patches: defaultdict[str, dict[str, Any]] = defaultdict(_new_bucket)
    for match in holdout:
        patch = str(match["patch"])
        seen: set[int] = set()
        for action in sorted(match["actions"], key=lambda row: int(row["ord"])):
            ord_ = int(action["ord"])
            target = int(action["hero_id"])
            stage = stage_for(ord_)
            legal = [hero_id for hero_id in vocab if hero_id not in seen]
            probabilities = predict_distribution(
                model,
                patch=patch,
                ord_=ord_,
                acting_team_id=_action_team_id(match, int(action["team"])),
                seen_hero_ids=seen,
            )
            model_rank = sorted(legal, key=lambda hero_id: (-probabilities[hero_id], hero_id))
            baseline_counter = stage_counts[(stage, bool(action["is_pick"]))]
            baseline_rank = sorted(
                legal, key=lambda hero_id: (-baseline_counter[hero_id], hero_id)
            )
            global_rank = sorted(legal, key=lambda hero_id: (-global_counts[hero_id], hero_id))
            for bucket in (overall, stages[stage], patches[patch]):
                _score(bucket, target, model_rank, baseline_rank, global_rank)
            seen.add(target)

    overall_metrics = _finalize(overall)
    model_top1 = overall_metrics["model"]["top1"]
    baseline_top1 = overall_metrics["stage_pickban_frequency"]["top1"]
    development_ready = bool(
        overall_metrics["n_predictions"] >= 100
        and model_top1 is not None
        and baseline_top1 is not None
        and model_top1 > baseline_top1
    )
    return {
        "report_kind": "policy_conditional_sequence_evaluation",
        "created_at": datetime.now().astimezone().isoformat(),
        "algorithm_version": ALGORITHM_VERSION,
        "development_ready": development_ready,
        "ready": bool(development_ready and independent_holdout),
        "release_ready": bool(development_ready and independent_holdout),
        "readiness_rule": "holdout n>=100 且 model top1 严格高于 stage+pick/ban frequency top1",
        "protocol": {
            "split": "按 started_at 排序，在 floor(80%) 位置取边界，同时间戳全部进入留出集",
            "parameter_search": False,
            "holdout_used_for_tuning": False,
            "independent_holdout": independent_holdout,
            "candidate_rule": "每一步排除当前真实前缀已出现英雄",
            "baseline": "stage(1-8,9-16,17-24)+is_pick frequency",
        },
        "split": {
            "split_at": split_at,
            "train_matches": len(train),
            "holdout_matches": len(holdout),
            "train_min_started_at": min(str(row["started_at"]) for row in train),
            "train_max_started_at": max(str(row["started_at"]) for row in train),
            "holdout_min_started_at": min(str(row["started_at"]) for row in holdout),
            "holdout_max_started_at": max(str(row["started_at"]) for row in holdout),
        },
        "metrics": {
            "overall": overall_metrics,
            "by_stage": {stage: _finalize(stages[stage]) for stage in STAGES},
            "by_patch": {patch: _finalize(patches[patch]) for patch in sorted(patches)},
        },
    }
