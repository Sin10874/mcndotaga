"""Value 只读公共数据仓储与模型载入。"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from analysis.profile_repository import _apply_position_annotation
from analysis.value_engine import build_value_analysis, error_result, normalize_request
from db.sources import SourceNotAllowed
from ingest.order_families import family_for
from models.value_model import timestamp

ROOT = Path(__file__).resolve().parents[1]


def model_path(patch, sources):
    return ROOT / 'data/models' / ('value-' + patch + '-' + '+'.join(sorted(sources)) + '.json')


def load_value_dataset(conn, request):
    """必须由调用者在只读事务中执行；没有写入和训练副作用。"""
    request = normalize_request(request)
    cutoff = timestamp(request['as_of'])
    with conn.cursor(row_factory=dict_row) as cursor:
        patch = cursor.execute('SELECT patch_id,base_version FROM patches WHERE version_name=%s', (request['patch'],)).fetchone()
        if patch is None:
            raise LookupError('未知版本')
        team_ids = [request[side]['team_id'] for side in ('radiant', 'dire')]
        if cursor.execute('SELECT count(*) AS n FROM teams WHERE team_id=ANY(%s)', (team_ids,)).fetchone()['n'] != 2:
            raise LookupError('未知战队')
        rows = cursor.execute('''SELECT m.match_id,m.started_at,m.duration_s,m.data_source,
            m.radiant_team_id,m.dire_team_id,m.radiant_win,m.first_pick_team,m.n_draft_actions,m.parse_state,
            p.version_name AS patch,p.base_version
            FROM opponent_profile_matches_with_pub m JOIN patches p USING(patch_id)
            WHERE p.base_version=%s AND m.data_source=ANY(%s)
              AND m.started_at < %s AND m.started_at <= CURRENT_TIMESTAMP
              AND m.duration_s > 0 AND m.radiant_win IS NOT NULL
              AND m.draft_state='complete' AND m.anomaly=false
              AND (m.game_mode=2 OR m.game_mode IS NULL)
            ORDER BY m.started_at,m.match_id''', (patch['base_version'], request['sources'], cutoff)).fetchall()
        matches = []
        ids = [r['match_id'] for r in rows]
        actions = defaultdict(list)
        players = defaultdict(list)
        annotations = {}
        if ids:
            for action in cursor.execute('SELECT match_id,ord,team,is_pick,hero_id FROM draft_actions WHERE match_id=ANY(%s) ORDER BY match_id,ord', (ids,)):
                actions[action['match_id']].append(action)
            # 经济字段只在仓储内核对历史标注指纹，随后删除，不作为模型特征。
            for player in cursor.execute('SELECT match_id,player_slot,team,account_id,hero_id,position,stats_available,lane_role,last_hits,net_worth,gpm,xpm FROM match_players WHERE match_id=ANY(%s)', (ids,)):
                player['position_source'] = 'recorded' if player['position'] else 'unknown'
                players[player['match_id']].append(player)
            for annotation in cursor.execute('SELECT match_id,team,input_fingerprint,method_version,annotations FROM profile_position_annotations WHERE match_id=ANY(%s)', (ids,)):
                annotations[(annotation['match_id'], annotation['team'])] = annotation
        for row in rows:
            draft = actions[row['match_id']]
            ended = row['started_at'] + timedelta(seconds=row['duration_s'])
            if ended >= cutoff or not family_for(row['n_draft_actions'], row['first_pick_team'], draft):
                continue
            picked = [a['hero_id'] for a in draft if a['is_pick']]
            if len(picked) != 10 or len(set(a['hero_id'] for a in draft)) != len(draft):
                continue
            row['ended_at'] = ended
            for team, side in enumerate(('radiant', 'dire')):
                row[side+'_heroes'] = [a['hero_id'] for a in draft if a['is_pick'] and a['team'] == team]
            if any(len(row[side+'_heroes']) != 5 for side in ('radiant', 'dire')):
                continue
            historical_players = players[row['match_id']]
            for team in (0,1):
                annotation = annotations.get((row['match_id'],team))
                if annotation:
                    _apply_position_annotation(row, [p for p in historical_players if p['team'] == team], annotation)
            row['players'] = [{k:p.get(k) for k in ('team','account_id','hero_id','position','position_source')} for p in historical_players]
            matches.append(row)
        return {'matches': matches, 'patch': request['patch'], 'base_version': patch['base_version'], 'cutoff': cutoff.isoformat()}


def prepare_value(dsn, request, artifact_path=None):
    """返回 (dataset, artifact)，供一次加载后反复纯函数评分。"""
    request = normalize_request(request)
    file_path = Path(artifact_path) if artifact_path else model_path(request['patch'], request['sources'])
    artifact = json.loads(file_path.read_text()) if file_path.exists() else None
    if artifact is None:
        return {'matches': []}, None
    with psycopg.connect(dsn.replace('postgresql+psycopg://','postgresql://'), connect_timeout=5) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '15s'")
        dataset = load_value_dataset(conn, request)
    return dataset, artifact


def load_value_analysis(dsn, request, artifact_path=None):
    try:
        dataset, artifact = prepare_value(dsn, request, artifact_path)
        return build_value_analysis(request, dataset, artifact)
    except SourceNotAllowed:
        return error_result('source_not_allowed', 'Value 只允许公开来源')
    except LookupError:
        return error_result('not_found', '请求的战队或版本不存在')
    except ValueError:
        return error_result('invalid_request', 'Value 请求或模型格式不合法')
    except (psycopg.Error, OSError, TypeError):
        return error_result('upstream_unavailable', 'Value 数据或模型暂时不可用')


def load_value(dsn, request, artifact_path=None):
    return load_value_analysis(dsn, request, artifact_path)['value']
