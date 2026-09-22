#!/usr/bin/env python3
"""只读训练精确版本 Value，输出不含比赛原始数据的模型与验证摘要。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from analysis.value_repository import load_value_dataset, model_path
from models.value_model import train_value_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patch', required=True)
    parser.add_argument('--as-of', default=datetime.now(timezone.utc).isoformat())
    parser.add_argument('--sources', default='pro_match')
    parser.add_argument('--dsn', default=os.environ.get('DATABASE_URL'))
    args = parser.parse_args()
    if not args.dsn:
        parser.error('必须显式提供 DATABASE_URL 或 --dsn')
    sources = sorted(set(args.sources.split(',')))
    with psycopg.connect(args.dsn.replace('postgresql+psycopg://','postgresql://'), connect_timeout=5) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '30s'")
        teams = conn.execute('''SELECT radiant_team_id,dire_team_id FROM opponent_profile_matches_with_pub
                               WHERE radiant_team_id IS NOT NULL AND dire_team_id IS NOT NULL
                                 AND radiant_team_id<>dire_team_id LIMIT 1''').fetchone()
        if teams is None:
            parser.error('公共数据中缺少两支战队')
        request = {'patch': args.patch, 'sources': sources, 'as_of': args.as_of, 'first_pick_team': 0,
                   'radiant': {'team_id': teams[0], 'heroes': []},
                   'dire': {'team_id': teams[1], 'heroes': []}}
        dataset = load_value_dataset(conn, request)
    try:
        artifact = train_value_model(dataset, patch=args.patch, sources=sources)
    except ValueError as exc:
        print(json.dumps({'error': {'code': 'insufficient_data', 'message': str(exc)}}, ensure_ascii=False))
        return 2
    output = model_path(args.patch, sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    # 服务可同时读取模型，使用原子替换避免读到半截 JSON。
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent, prefix=output.name+'.', suffix='.tmp', delete=False) as stream:
        stream.write(json.dumps(artifact, ensure_ascii=False, indent=2)+'\n')
        temporary = Path(stream.name)
    temporary.replace(output)
    summary = {'patch': args.patch, 'sources': sources, 'data_cutoff': dataset['cutoff'],
               'model_version': artifact['model_version'], **artifact['metadata']}
    review = ROOT / 'data/reviews' / ('value-' + args.patch + '-validation.json')
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'artifact': str(output), 'review': str(review), **summary}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
