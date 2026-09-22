"""初始化画像依赖的规格权重，保留用户已配置的值。"""
from __future__ import annotations

import json
import os

import psycopg


# 规格 §7.2：每行按 pub、scrim、pro 的顺序列出，不能替换为单一全局权重。
DEFAULT_WEIGHTS = {
    "patch_strength": (1.0, 0.0, 0.5),
    "hero_pool": (1.0, 0.5, 1.0),
    "system_pref": (0.0, 0.5, 1.5),
    "bp_tendency": (0.0, 0.25, 1.5),
    "tempo": (0.25, 0.5, 1.0),
    "map_vision": (0.25, 0.5, 1.0),
}


def seed_profile_config(conn) -> int:
    inserted = 0
    for metric, weights in DEFAULT_WEIGHTS.items():
        for source, weight in zip(("pub_match", "scrim", "pro_match"), weights):
            row = conn.execute(
                """INSERT INTO metric_weights(metric,data_source,weight,note)
                   VALUES (%s,%s,%s,'规格 7.2 初始权重')
                   ON CONFLICT(metric,data_source) DO NOTHING RETURNING metric""",
                (metric, source, weight),
            ).fetchone()
            inserted += row is not None
    conn.execute(
        """INSERT INTO app_config_kv(key,value,note) VALUES
           ('min_sample_n','30','规格最低样本量') ON CONFLICT(key) DO NOTHING"""
    )
    return inserted


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("缺少 DATABASE_URL")
    with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://")) as conn:
        inserted = seed_profile_config(conn)
    print(json.dumps({"新增权重": inserted, "已有配置": "保留"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
