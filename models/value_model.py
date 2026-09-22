"""仅用 BP 与赛前历史的稀疏 L2 逻辑回归，固定参数与时间留出。"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import exp, log
import re

FACTORS = ('patch_strength', 'counter_matchup', 'player_comfort', 'first_pick')
MODEL_VERSION = 'value-logistic-v2'


def timestamp(value):
    if not isinstance(value, (str, datetime)):
        raise ValueError('时间必须是带时区的字符串或 datetime')
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('时间必须包含时区')
    return result.astimezone(timezone.utc)


def sigmoid(z):
    return 1 / (1 + exp(-max(-40, min(40, z))))


def probability_parts(weights, features):
    logits = {factor: sum(weights.get(key, 0) * value for key, value in features.get(factor, {}).items())
              for factor in FACTORS}
    z = sum(logits.values())
    prob = sigmoid(z)
    scale = (prob - .5) / z if abs(z) > 1e-12 else .25
    return prob, {factor: value * scale for factor, value in logits.items()}


def request_for_match(row):
    return {'patch': row['patch'], 'sources': [row['data_source']],
            'first_pick_team': row['first_pick_team'],
            **{side: {'team_id': row.get(side + '_team_id'),
                      'heroes': [{'hero_id': h} for h in row[side + '_heroes']]}
               for side in ('radiant', 'dire')}}


def context_features(request, history):
    """历史必须已结束。位置只用既往记录及已核对指纹的历史推断，不读目标同场赛后信息。"""
    baseline = defaultdict(lambda: [0, 0])
    matchups = defaultdict(lambda: [0, 0])
    positions = defaultdict(Counter)
    inferred_positions = set()
    latest = {}
    for row in history:
        players = row.get('players', [])
        for team, side in enumerate(('radiant', 'dire')):
            tid = row.get(side + '_team_id')
            if not tid:
                continue
            own = [p for p in players if p['team'] == team and p.get('account_id')]
            if own and (tid not in latest or timestamp(row['started_at']) > latest[tid][0]):
                latest[tid] = (timestamp(row['started_at']), own)
            won = int(bool(row['radiant_win']) == (team == 0))
            for p in own:
                key = (tid, p['account_id'], p['hero_id'])
                baseline[key][0] += 1
                baseline[key][1] += won
                position = p.get('position')
                if position is None:
                    continue
                positions[(tid, p['hero_id'])][position] += 1
                if p.get('position_source') == 'heuristic':
                    inferred_positions.add((tid, p['hero_id']))
                for opponent in players:
                    if opponent['team'] != team and opponent.get('position') == position:
                        item = matchups[key + (opponent['hero_id'],)]
                        item[0] += 1
                        item[1] += won
    assigned = {}
    details = []
    comfort = 0.0
    for team, side in enumerate(('radiant', 'dire')):
        tid = request[side].get('team_id')
        roster = latest.get(tid, (None, []))[1]
        for hero in request[side]['heroes']:
            hid = hero['hero_id']
            position = hero.get('position')
            method = 'request' if position else 'unavailable'
            if position is None:
                counts = positions[(tid, hid)]
                if counts:
                    ordered = counts.most_common()
                    if ordered[0][1] >= 5 and ordered[0][1] / sum(counts.values()) >= .6:
                        position = ordered[0][0]
                        method = 'historical_inferred_position' if (tid, hid) in inferred_positions else 'historical_raw_position'
            candidates = [p for p in roster if position is not None and p.get('position') == position]
            player = candidates[0]['account_id'] if len(candidates) == 1 else None
            assigned[(team, hid)] = (tid, player, position)
            n = baseline[(tid, player, hid)][0] if player else 0
            # 只有明确到历史阵容中的选手，才定义该英雄的熟练度代理量。
            if n:
                comfort += (1 if team == 0 else -1) * min(1, log(1+n) / log(31)) / 5
            details.append({'side': side, 'hero_id': hid, 'position_method': method,
                            'player_known': player is not None, 'comfort_n': n,
                            'roster_position_source': candidates[0].get('position_source', 'recorded') if len(candidates) == 1 else 'unknown',
                            'comfort_reason': None if n else ('stat_unavailable' if not player else 'insufficient_samples')})
    counter = 0.0
    counter_detail = []
    for team, side in enumerate(('radiant', 'dire')):
        other = request['dire' if team == 0 else 'radiant']['heroes']
        for hero in request[side]['heroes']:
            hid = hero['hero_id']
            tid, player, position = assigned[(team, hid)]
            opponents = [h for h in other if position is not None and assigned[(1-team, h['hero_id'])][2] == position]
            if not player or len(opponents) != 1:
                counter_detail.append({'side': side, 'hero_id': hid, 'n': 0,
                                       'reason': 'stat_unavailable', 'cause': '缺少可信的历史选手位置或对位'})
                continue
            oid = opponents[0]['hero_id']
            n, wins = matchups[(tid, player, hid, oid)]
            bn, bw = baseline[(tid, player, hid)]
            delta = wins/n - bw/bn if n >= 30 and bn else 0.0
            counter += (1 if team == 0 else -1) * delta / 10
            counter_detail.append({'side': side, 'hero_id': hid, 'opponent_hero_id': oid,
                                   'n': n, 'baseline_n': bn, 'conditional_delta': delta,
                                   'reason': None if n >= 30 else 'insufficient_samples'})
    return comfort, counter, {'player_comfort': details, 'counter_matchup': counter_detail}


def extract_features(request, history):
    hero_features = {'side_bias': 1.0}
    for sign, side in ((1, 'radiant'), (-1, 'dire')):
        for hero in request[side]['heroes']:
            hero_features['hero:' + str(hero['hero_id'])] = sign
    comfort, counter, detail = context_features(request, history)
    return {'patch_strength': hero_features, 'player_comfort': {'comfort': comfort},
            'counter_matchup': {'counter': counter},
            'first_pick': {'first_pick': 1 if request['first_pick_team'] == 0 else -1}}, detail


def _fit(samples):
    """固定 300 次全批次梯度下降，L2=0.02，步长 0.3，不看留出集选参。"""
    weights = {key: 0.0 for x, _ in samples for key in x}
    for _ in range(300):
        gradients = {key: .02 * value for key, value in weights.items()}
        for x, y in samples:
            error = sigmoid(sum(weights[key] * value for key, value in x.items())) - y
            for key, value in x.items():
                gradients[key] += error * value / len(samples)
        for key in weights:
            weights[key] -= .3 * gradients[key]
    return weights


def _metrics(labels, probabilities):
    n = len(labels)
    return {'n': n, 'accuracy': sum((p >= .5) == bool(y) for y,p in zip(labels, probabilities))/n,
            'brier': sum((p-y)**2 for y,p in zip(labels, probabilities))/n,
            'log_loss': -sum(y*log(max(p,1e-12))+(1-y)*log(max(1-p,1e-12)) for y,p in zip(labels, probabilities))/n}


def train_value_model(dataset, *, patch, sources):
    from db.sources import resolve_sources
    sources = resolve_sources(sources)
    base_version = re.sub(r'[a-z]$', '', patch)
    data_cutoff = timestamp(dataset['cutoff']) if dataset.get('cutoff') else None
    history = sorted([r for r in dataset['matches'] if r['data_source'] in sources and
                      r.get('base_version') == base_version and
                      r.get('radiant_win') is not None and r.get('ended_at') and
                      (data_cutoff is None or timestamp(r['ended_at']) < data_cutoff)],
                     key=lambda r: (timestamp(r['started_at']), r['match_id']))
    exact = [r for r in history if r['patch'] == patch]
    if len(exact) < 75:
        raise ValueError('精确版本至少需要 75 场完整样本，保证训练不少于 60、时间留出不少于 15')
    split_time = timestamp(exact[int(len(exact)*.8)]['started_at'])
    train = [r for r in exact if timestamp(r['ended_at']) < split_time]
    validation = [r for r in exact if timestamp(r['started_at']) >= split_time]
    if len(train) < 60 or len(validation) < 15 or len({r['radiant_win'] for r in train}) < 2:
        raise ValueError('时间隔离后样本或标签类别不足')
    features = []
    for row in train:
        past = [r for r in history if timestamp(r['ended_at']) < timestamp(row['started_at'])]
        request = request_for_match(row)
        # 完整阵容与两侧各前 1、3 个已选英雄共享比赛权重，支持部分局面。
        for length in (1, 3, 5):
            partial = {**request, **{side: {**request[side], 'heroes': request[side]['heroes'][:length]}
                                     for side in ('radiant', 'dire')}}
            groups, _ = extract_features(partial, past)
            features.append(({k:v for group in groups.values() for k,v in group.items()}, int(row['radiant_win'])))
    weights = _fit(features)
    # 留出期间历史上下文冻结在训练边界之前，不吸收留出标签。
    frozen_history = [r for r in history if timestamp(r['ended_at']) < split_time]
    labels, probabilities = [], []
    partial_probabilities = {1: [], 3: []}
    for row in validation:
        request = request_for_match(row)
        groups, _ = extract_features(request, frozen_history)
        probabilities.append(probability_parts(weights, groups)[0])
        labels.append(int(row['radiant_win']))
        for length in partial_probabilities:
            partial = {**request, **{side: {**request[side], 'heroes': request[side]['heroes'][:length]}
                                    for side in ('radiant', 'dire')}}
            groups, _ = extract_features(partial, frozen_history)
            partial_probabilities[length].append(probability_parts(weights, groups)[0])
    rate = (sum(r['radiant_win'] for r in train)+1)/(len(train)+2)
    hero_counts = Counter(h for r in train for side in ('radiant', 'dire') for h in r[side+'_heroes'])
    return {'model_version': MODEL_VERSION, 'patch': patch, 'sources': sources,
            'weights': weights, 'hero_counts': dict(hero_counts),
            'sources_used': sorted({r['data_source'] for r in frozen_history}),
            'metadata': {'train_n': len(train), 'validation_n': len(validation),
                         'excluded_overlap_n': len(exact)-len(train)-len(validation),
                         'train_start': timestamp(train[0]['started_at']).isoformat(),
                         'train_end': max(timestamp(r['ended_at']) for r in train).isoformat(),
                         'context_cutoff': split_time.isoformat(),
                         'validation_start': split_time.isoformat(),
                         'validation_end': max(timestamp(r['ended_at']) for r in validation).isoformat(),
                         'validation': _metrics(labels, probabilities),
                         'partial_validation': {str(k): _metrics(labels, p) for k,p in partial_probabilities.items()},
                         'baseline': _metrics(labels, [rate]*len(labels)),
                         'hyperparameters': {'l2': .02, 'learning_rate': .3, 'iterations': 300},
                         'holdout_used_for_tuning': False,
                         'evaluation_status': 'development_reused_holdout',
                         'experimental': True,
                         'feature_nonzero_training_examples': {key: sum(bool(x.get(key)) for x,y in features) for key in ('comfort','counter')},
                         'feature_policy': 'BP英雄、先手、赛前已结束比赛的历史选手熟练度与条件化对位；不读取同场赛后数据',
                         'limitations': ['部分局面验证只覆盖双方各 1、3、5 个 pick，非完整实时前缀回放',
                                         '胜率未作独立校准；confidence 仅表示样本档位',
                                         'patch_strength 包含该精确版本的 Radiant 侧偏置',
                                         '历史位置含经指纹核对的启发式标注，推断准确率未经真值验证',
                                         '此版增加历史位置特征后复用已看过的时间留出集，仅属开发评估' ]}}
