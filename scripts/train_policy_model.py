#!/usr/bin/env python3
"""训练并严格时间留出评估 Policy 条件概率模型。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.policy_repository import load_training_snapshot  # noqa: E402
from models.policy_model import evaluate_chronological, fit_conditional_model  # noqa: E402


UTC = timezone.utc
DEFAULT_SINCE = datetime(2025, 1, 1, tzinfo=UTC)


def build_outputs(
    snapshot: dict[str, Any], *, independent_holdout: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回本地模型产物与可公开的聚合评估，不复制逐场训练行。"""

    matches = snapshot["matches"]
    hero_ids = snapshot["hero_ids"]
    evaluation = evaluate_chronological(
        matches, hero_ids, independent_holdout=independent_holdout
    )
    evaluation["data"] = dict(snapshot["metadata"])
    evaluation["data"]["strict_family_valid_matches"] = len(matches)
    artifact = {
        "artifact_kind": "policy_conditional_sequence_model",
        "created_at": datetime.now(UTC).isoformat(),
        "training": {
            "n_matches": len(matches),
            "min_started_at": min(row["started_at"] for row in matches),
            "max_started_at": max(row["started_at"] for row in matches),
            "sources": list(snapshot["metadata"]["sources"]),
            "constants_snapshot_version": snapshot["metadata"]["constants_snapshot_version"],
            "historical_cm_pool_exact": snapshot["metadata"]["historical_cm_pool_exact"],
            "limitation": snapshot["metadata"]["limitation"],
        },
        "evaluation_summary": {
            "development_ready": evaluation["development_ready"],
            "release_ready": evaluation["release_ready"],
            "split_at": evaluation["split"]["split_at"],
            "holdout_matches": evaluation["split"]["holdout_matches"],
            "overall": evaluation["metrics"]["overall"],
        },
        "model": fit_conditional_model(matches, hero_ids),
    }
    return artifact, evaluation


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("时间必须带时区")
    return parsed.astimezone(UTC)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--since", type=_parse_datetime, default=DEFAULT_SINCE)
    parser.add_argument("--until", type=_parse_datetime)
    parser.add_argument(
        "--model-output",
        type=Path,
        default=ROOT / "data" / "models" / "policy-model.json",
    )
    parser.add_argument(
        "--evaluation-output",
        type=Path,
        default=ROOT / "data" / "models" / "policy-evaluation.json",
    )
    args = parser.parse_args()
    if not args.dsn:
        parser.error("请设置 DATABASE_URL 或传入 --dsn")

    snapshot = load_training_snapshot(
        args.dsn,
        since=args.since,
        until=args.until,
        sources=["pro_match"],
    )
    artifact, evaluation = build_outputs(snapshot)
    _write_json(args.model_output, artifact)
    _write_json(args.evaluation_output, evaluation)

    overall = evaluation["metrics"]["overall"]
    print(f"strict_family_valid_matches={evaluation['data']['strict_family_valid_matches']}")
    print(
        f"train_matches={evaluation['split']['train_matches']}, "
        f"holdout_matches={evaluation['split']['holdout_matches']}"
    )
    print(
        f"model_top1={overall['model']['top1']:.8f}, "
        f"frequency_top1={overall['stage_pickban_frequency']['top1']:.8f}"
    )
    print(f"development_ready={str(evaluation['development_ready']).lower()}")
    print(f"release_ready={str(evaluation['release_ready']).lower()}")
    print(f"model_output={args.model_output}")
    print(f"evaluation_output={args.evaluation_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
