#!/usr/bin/env python3
"""M3 序列频率基线的只读、可复算评估。

算法在运行前固定，不读取留出集来选择参数：
1. 只读 repeatable read 快照中读取公共 opponent_profile_matches view。
2. 限定 2025-01-01 起、complete、非异常、24 手，再用 family_for 严格筛
   spec_6_0_24。
3. 按 started_at 排序，以 floor(80%) 位置的时间戳为边界，边界同刻记录全部
   进入留出集。
4. 训练均匀随机期望、全局英雄频率、阶段加 pick/ban 类型频率三项基线。
5. 评估每一步先移除真实前缀已出现英雄，相同频率按 hero_id 升序。

只保存聚合结果，不保存 match_id、阵容、逐手序列或数据库凭据。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest.order_families import SPEC_FAMILY, family_for  # noqa: E402


DEFAULT_DSN = os.environ.get("DATABASE_URL")
DEFAULT_SINCE = "2025-01-01T00:00:00+00:00"
TOP_K = (1, 3, 5)
STAGES = ("1-8", "9-16", "17-24")


SQL = r"""
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
COPY (
  WITH latest_snapshot AS (
    SELECT snapshot_version, fetched_at, n_heroes
    FROM constants_snapshot
    ORDER BY snapshot_version DESC
    LIMIT 1
  ),
  vocab AS (
    SELECT hti.snapshot_version,
           jsonb_agg(hti.hero_id ORDER BY hti.hero_id) AS hero_ids,
           count(*) AS indexed_heroes
    FROM hero_token_index hti
    JOIN latest_snapshot ls USING (snapshot_version)
    GROUP BY hti.snapshot_version
  ),
  source_counts AS (
    SELECT count(*) FILTER (
             WHERE m.data_source = 'pro_match'
               AND m.started_at >= :'since'::timestamptz
               AND m.started_at <= transaction_timestamp()
           ) AS public_since,
           count(*) FILTER (
             WHERE m.data_source = 'pro_match'
               AND m.started_at >= :'since'::timestamptz
               AND m.started_at <= transaction_timestamp()
               AND m.draft_state = 'complete'
               AND NOT m.anomaly
               AND m.n_draft_actions = 24
           ) AS hard_gate
    FROM opponent_profile_matches m
  ),
  eligible AS (
    SELECT m.match_id, m.started_at, m.first_pick_team,
           coalesce(p.version_name, '__unknown__') AS patch
    FROM opponent_profile_matches m
    LEFT JOIN patches p USING (patch_id)
    WHERE m.data_source = 'pro_match'
      AND m.started_at >= :'since'::timestamptz
      AND m.started_at <= transaction_timestamp()
      AND m.draft_state = 'complete'
      AND NOT m.anomaly
      AND m.n_draft_actions = 24
  ),
  match_rows AS (
    SELECT e.match_id, e.started_at, e.first_pick_team, e.patch,
           count(a.ord) AS action_count,
           coalesce(
             jsonb_agg(
               jsonb_build_object(
                 'ord', a.ord,
                 'is_pick', a.is_pick,
                 'team', a.team,
                 'hero_id', a.hero_id
               ) ORDER BY a.ord
             ) FILTER (WHERE a.ord IS NOT NULL),
             '[]'::jsonb
           ) AS actions
    FROM eligible e
    LEFT JOIN draft_actions a USING (match_id)
    GROUP BY e.match_id, e.started_at, e.first_pick_team, e.patch
  ),
  payloads AS (
    SELECT 0 AS kind_order, NULL::timestamptz AS started_at, NULL::bigint AS match_id,
           jsonb_build_object(
             'kind', 'meta',
             'snapshot_at', transaction_timestamp(),
             'pg_snapshot', txid_current_snapshot(),
             'transaction_isolation', current_setting('transaction_isolation'),
             'transaction_read_only', current_setting('transaction_read_only'),
             'source_view', 'opponent_profile_matches',
             'since', :'since',
             'public_since', sc.public_since,
             'hard_gate', sc.hard_gate,
             'snapshot_version', ls.snapshot_version,
             'constants_fetched_at', ls.fetched_at,
             'declared_n_heroes', ls.n_heroes,
             'indexed_heroes', v.indexed_heroes,
             'hero_ids', v.hero_ids
           ) AS payload
    FROM latest_snapshot ls
    JOIN vocab v USING (snapshot_version)
    CROSS JOIN source_counts sc
    UNION ALL
    SELECT 1, mr.started_at, mr.match_id,
           jsonb_build_object(
             'kind', 'match',
             'match_id', mr.match_id,
             'started_at', mr.started_at,
             'first_pick_team', mr.first_pick_team,
             'patch', mr.patch,
             'action_count', mr.action_count,
             'actions', mr.actions
           )
    FROM match_rows mr
  )
  SELECT payload::text
  FROM payloads
  ORDER BY kind_order, started_at, match_id
) TO STDOUT WITH (FORMAT CSV);
COMMIT;
"""


def stage_for(ord_: int) -> str:
    return STAGES[ord_ // 8]


def run_snapshot(dsn: str, since: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    command = [
        "psql", dsn, "-X", "-q", "-w", "-v", "ON_ERROR_STOP=1",
        "-v", f"since={since}",
    ]
    completed = subprocess.run(
        command,
        input=SQL,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"只读快照查询失败：{completed.stderr.strip()}")

    rows = list(csv.reader(io.StringIO(completed.stdout)))
    payloads = [json.loads(row[0]) for row in rows if row]
    if not payloads or payloads[0].get("kind") != "meta":
        raise RuntimeError("快照查询缺少 meta 行")
    meta = payloads[0]
    matches = [row for row in payloads[1:] if row.get("kind") == "match"]
    return meta, matches


def validate_and_filter(
    matches: Iterable[dict[str, Any]], vocab: set[int]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    accepted: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter({
        "action_count_not_24": 0,
        "duplicate_hero": 0,
        "hero_outside_frozen_vocab": 0,
        "not_spec_6_0_24": 0,
    })
    for match in matches:
        actions = match["actions"]
        if match["action_count"] != 24 or len(actions) != 24:
            excluded["action_count_not_24"] += 1
            continue
        hero_ids = [int(action["hero_id"]) for action in actions]
        if len(set(hero_ids)) != 24:
            excluded["duplicate_hero"] += 1
            continue
        if any(hero_id not in vocab for hero_id in hero_ids):
            excluded["hero_outside_frozen_vocab"] += 1
            continue
        normalized = [
            {
                "ord": int(action["ord"]),
                "is_pick": bool(action["is_pick"]),
                "team": int(action["team"]),
                "hero_id": int(action["hero_id"]),
            }
            for action in actions
        ]
        family = family_for(
            24,
            match["first_pick_team"],
            normalized,
        )
        if family != SPEC_FAMILY:
            excluded["not_spec_6_0_24"] += 1
            continue
        match["actions"] = normalized
        accepted.append(match)
    accepted.sort(key=lambda row: (row["started_at"], int(row["match_id"])))
    return accepted, dict(sorted(excluded.items()))


def split_chronologically(
    matches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, int]:
    if len(matches) < 2:
        raise RuntimeError("严格筛选后不足 2 场，无法做时间切分")
    target = math.floor(len(matches) * 0.8)
    if target <= 0 or target >= len(matches):
        raise RuntimeError("80/20 切分目标无效")
    split_at = matches[target]["started_at"]
    train = [row for row in matches if row["started_at"] < split_at]
    holdout = [row for row in matches if row["started_at"] >= split_at]
    if not train or not holdout:
        raise RuntimeError("按同时间戳不跨界规则切分后出现空集合")
    return train, holdout, split_at, target


def result_bucket() -> dict[str, Any]:
    return {
        "n_predictions": 0,
        "hits": {str(k): 0 for k in TOP_K},
        "random_expected_hits": {str(k): 0.0 for k in TOP_K},
    }


def score_bucket(bucket: dict[str, Any], target: int, remaining: int,
                 global_rank: list[int], stratified_rank: list[int]) -> None:
    bucket["n_predictions"] += 1
    for k in TOP_K:
        key = str(k)
        bucket["random_expected_hits"][key] += min(k, remaining) / remaining
        bucket.setdefault("global_hits", {str(x): 0 for x in TOP_K})
        bucket.setdefault("stratified_hits", {str(x): 0 for x in TOP_K})
        bucket["global_hits"][key] += int(target in global_rank[:k])
        bucket["stratified_hits"][key] += int(target in stratified_rank[:k])


def finalize_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    n = bucket["n_predictions"]
    if n == 0:
        return {
            "n_predictions": 0,
            "uniform_random_expected": {f"top{k}": None for k in TOP_K},
            "global_frequency": {f"top{k}": None for k in TOP_K},
            "stage_pickban_frequency": {f"top{k}": None for k in TOP_K},
        }
    return {
        "n_predictions": n,
        "uniform_random_expected": {
            f"top{k}": round(bucket["random_expected_hits"][str(k)] / n, 8)
            for k in TOP_K
        },
        "global_frequency": {
            f"top{k}": round(bucket["global_hits"][str(k)] / n, 8)
            for k in TOP_K
        },
        "stage_pickban_frequency": {
            f"top{k}": round(bucket["stratified_hits"][str(k)] / n, 8)
            for k in TOP_K
        },
    }


def date_range(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {"min_started_at": rows[0]["started_at"], "max_started_at": rows[-1]["started_at"]}


def patch_match_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row["patch"]) for row in rows).items()))


def evaluate(meta: dict[str, Any], raw_matches: list[dict[str, Any]]) -> dict[str, Any]:
    vocab = {int(hero_id) for hero_id in meta["hero_ids"]}
    if len(vocab) != int(meta["indexed_heroes"]):
        raise RuntimeError("冻结词表含重复 hero_id")
    if int(meta["indexed_heroes"]) != int(meta["declared_n_heroes"]):
        raise RuntimeError("冻结词表行数与 constants_snapshot.n_heroes 不一致")

    valid, excluded = validate_and_filter(raw_matches, vocab)
    train, holdout, split_at, target = split_chronologically(valid)

    global_counts: Counter[int] = Counter()
    stratified_counts: defaultdict[tuple[str, bool], Counter[int]] = defaultdict(Counter)
    for match in train:
        for action in match["actions"]:
            hero_id = action["hero_id"]
            global_counts[hero_id] += 1
            stratified_counts[(stage_for(action["ord"]), action["is_pick"])][hero_id] += 1

    global_order = sorted(vocab, key=lambda hero_id: (-global_counts[hero_id], hero_id))
    stratified_order: dict[tuple[str, bool], list[int]] = {}
    empty_buckets: list[dict[str, Any]] = []
    for stage in STAGES:
        for is_pick in (False, True):
            key = (stage, is_pick)
            counts = stratified_counts[key]
            if not counts:
                stratified_order[key] = global_order
                empty_buckets.append({"stage": stage, "is_pick": is_pick})
            else:
                stratified_order[key] = sorted(
                    vocab, key=lambda hero_id: (-counts[hero_id], hero_id)
                )

    overall = result_bucket()
    stages = {stage: result_bucket() for stage in STAGES}
    patches: defaultdict[str, dict[str, Any]] = defaultdict(result_bucket)
    fallback_predictions = 0
    for match in holdout:
        seen: set[int] = set()
        patch = str(match["patch"])
        for action in match["actions"]:
            ord_ = action["ord"]
            stage = stage_for(ord_)
            key = (stage, action["is_pick"])
            target_hero = action["hero_id"]
            if target_hero in seen:
                raise RuntimeError("留出集出现重复英雄，验证逻辑失效")
            global_rank = [hero_id for hero_id in global_order if hero_id not in seen]
            stratified_rank = [hero_id for hero_id in stratified_order[key] if hero_id not in seen]
            if not stratified_counts[key]:
                fallback_predictions += 1
            remaining = len(vocab) - len(seen)
            if len(global_rank) != remaining or len(stratified_rank) != remaining:
                raise RuntimeError("候选集排除真实前缀后长度错误")
            for bucket in (overall, stages[stage], patches[patch]):
                score_bucket(bucket, target_hero, remaining, global_rank, stratified_rank)
            seen.add(target_hero)

    train_patch_counts = patch_match_counts(train)
    holdout_patch_counts = patch_match_counts(holdout)
    patch_metrics = {}
    for patch in sorted(patches):
        patch_metrics[patch] = {
            **finalize_bucket(patches[patch]),
            "n_train_matches_from_patch": train_patch_counts.get(patch, 0),
            "n_holdout_matches_from_patch": holdout_patch_counts.get(patch, 0),
        }

    return {
        "report_kind": "m3_frequency_feasibility",
        "created_at": datetime.now().astimezone().isoformat(),
        "scope": {
            "status": "feasibility_only",
            "m7_complete": False,
            "m8_complete": False,
            "serves_inference_api": False,
        },
        "pre_registered_algorithm": {
            "parameter_search": False,
            "holdout_used_for_tuning": False,
            "source_view": "opponent_profile_matches",
            "data_source_filter": "pro_match",
            "since_inclusive": meta["since"],
            "until_inclusive": "transaction_timestamp()",
            "hard_gate": "draft_state=complete AND anomaly=false AND n_draft_actions=24",
            "required_order_family": SPEC_FAMILY,
            "split": "按 started_at 排序，floor(80%)*位置的时间戳为 split_at；started_at < split_at 训练，其余留出",
            "tie_break": "frequency desc, hero_id asc",
            "candidate_rule": "每一步排除该场真实前缀已出现英雄",
            "stratum": "stage(1-8,9-16,17-24) + is_pick；空桶回退 global",
        },
        "snapshot": {
            "snapshot_at": meta["snapshot_at"],
            "pg_snapshot": meta["pg_snapshot"],
            "transaction_isolation": meta["transaction_isolation"],
            "transaction_read_only": meta["transaction_read_only"],
            "constants_snapshot_version": meta["snapshot_version"],
            "constants_fetched_at": meta["constants_fetched_at"],
            "frozen_vocab_size": len(vocab),
        },
        "data_flow": {
            "public_view_matches_since": int(meta["public_since"]),
            "hard_gate_matches": int(meta["hard_gate"]),
            "rows_received_in_snapshot": len(raw_matches),
            "excluded_after_hard_gate": excluded,
            "strict_family_valid_matches": len(valid),
        },
        "split": {
            "target_train_index_floor_80pct": target,
            "split_at": split_at,
            "same_timestamp_crosses_boundary": False,
            "train": {
                "n_matches": len(train),
                "n_predictions": len(train) * 24,
                **date_range(train),
                "matches_by_patch": train_patch_counts,
            },
            "holdout": {
                "n_matches": len(holdout),
                "n_predictions": len(holdout) * 24,
                **date_range(holdout),
                "matches_by_patch": holdout_patch_counts,
            },
        },
        "fallback": {
            "empty_training_buckets": empty_buckets,
            "n_holdout_predictions_using_global_fallback": fallback_predictions,
        },
        "metrics": {
            "overall": finalize_bucket(overall),
            "by_stage": {stage: finalize_bucket(stages[stage]) for stage in STAGES},
            "by_patch": patch_metrics,
        },
    }


def concise_log(report: dict[str, Any]) -> str:
    overall = report["metrics"]["overall"]
    rows = [
        "M3 序列频率基线可行性评估",
        f"snapshot_at={report['snapshot']['snapshot_at']}",
        f"pg_snapshot={report['snapshot']['pg_snapshot']}",
        f"transaction={report['snapshot']['transaction_isolation']}, read_only={report['snapshot']['transaction_read_only']}",
        f"frozen_vocab_size={report['snapshot']['frozen_vocab_size']}",
        f"hard_gate_matches={report['data_flow']['hard_gate_matches']}",
        f"strict_family_valid_matches={report['data_flow']['strict_family_valid_matches']}",
        f"excluded_after_hard_gate={json.dumps(report['data_flow']['excluded_after_hard_gate'], ensure_ascii=False, sort_keys=True)}",
        f"split_at={report['split']['split_at']}",
        f"train_matches={report['split']['train']['n_matches']}, holdout_matches={report['split']['holdout']['n_matches']}",
        f"holdout_predictions={overall['n_predictions']}",
    ]
    for baseline in ("uniform_random_expected", "global_frequency", "stage_pickban_frequency"):
        metric = overall[baseline]
        rows.append(
            f"overall.{baseline}=top1 {metric['top1']:.8f}, top3 {metric['top3']:.8f}, top5 {metric['top5']:.8f}"
        )
    for stage, metric_row in report["metrics"]["by_stage"].items():
        metric = metric_row["stage_pickban_frequency"]
        rows.append(
            f"stage.{stage}.stage_pickban_frequency=n {metric_row['n_predictions']}, top1 {metric['top1']:.8f}, top3 {metric['top3']:.8f}, top5 {metric['top5']:.8f}"
        )
    for patch, metric_row in report["metrics"]["by_patch"].items():
        global_metric = metric_row["global_frequency"]
        stratified_metric = metric_row["stage_pickban_frequency"]
        rows.append(
            f"patch.{patch}=train_matches {metric_row['n_train_matches_from_patch']}, holdout_matches {metric_row['n_holdout_matches_from_patch']}, n {metric_row['n_predictions']}; "
            f"global top1 {global_metric['top1']:.8f}, top3 {global_metric['top3']:.8f}, top5 {global_metric['top5']:.8f}; "
            f"stratified top1 {stratified_metric['top1']:.8f}, top3 {stratified_metric['top3']:.8f}, top5 {stratified_metric['top5']:.8f}"
        )
    rows.extend([
        f"empty_training_buckets={json.dumps(report['fallback']['empty_training_buckets'], ensure_ascii=False)}",
        "holdout_used_for_tuning=false",
        "status=feasibility_only; m7_complete=false; m8_complete=false; serves_inference_api=false",
    ])
    return "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=ROOT / "data" / "m3-frequency-baseline.json",
    )
    parser.add_argument(
        "--output-log",
        type=Path,
        default=ROOT / "data" / "m3-frequency-baseline.log",
    )
    args = parser.parse_args()
    if not args.dsn:
        parser.error("请设置 DATABASE_URL 或传入 --dsn")

    meta, matches = run_snapshot(args.dsn, args.since)
    report = evaluate(meta, matches)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_log.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output_log.write_text(concise_log(report), encoding="utf-8")
    print(concise_log(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
