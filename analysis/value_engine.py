"""冻结 Value 响应及可单独包装的训练与缺失诊断。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
import re

from db.sources import SourceNotAllowed, resolve_sources
from models.value_model import FACTORS, MODEL_VERSION, extract_features, probability_parts, timestamp


def normalize_request(request):
    if not isinstance(request, dict):
        raise ValueError('请求必须是对象')
    if set(request) - {'patch', 'sources', 'first_pick_team', 'radiant', 'dire', 'as_of'}:
        raise ValueError('请求含不支持的参数')
    patch = request.get('patch')
    if not isinstance(patch, str) or not re.fullmatch(r'\d{1,2}\.\d{1,3}[a-z]?', patch):
        raise ValueError('必须指定精确版本')
    sources = request.get('sources', ['pro_match'])
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources):
        raise ValueError('来源必须为非空字符串数组')
    sources = resolve_sources(sources)
    if type(request.get('first_pick_team')) is not int or request['first_pick_team'] not in (0, 1):
        raise ValueError('先手方必须为 0 或 1')
    seen = set()
    for side in ('radiant', 'dire'):
        team = request.get(side)
        if not isinstance(team, dict) or set(team) != {'team_id', 'heroes'}:
            raise ValueError('双方必须提供 team_id 和 heroes')
        if type(team['team_id']) is not int or not 0 < team['team_id'] <= 2**63-1:
            raise ValueError('队伍 ID 必须为正整数')
        if not isinstance(team['heroes'], list) or len(team['heroes']) > 5:
            raise ValueError('每侧最多五个英雄')
        positions = set()
        for hero in team['heroes']:
            if not isinstance(hero, dict) or set(hero)-{'hero_id', 'position'}:
                raise ValueError('英雄字段不合法')
            hid = hero.get('hero_id')
            if type(hid) is not int or not 0 < hid <= 32767 or hid in seen:
                raise ValueError('英雄必须是非重复的正整数')
            seen.add(hid)
            if 'position' in hero:
                pos = hero['position']
                if type(pos) is not int or pos not in range(1, 6) or pos in positions:
                    raise ValueError('同侧位置必须是非重复的 1 到 5')
                positions.add(pos)
    if request['radiant']['team_id'] == request['dire']['team_id']:
        raise ValueError('双方不能是同一战队')
    now = datetime.now(timezone.utc)
    as_of = request.get('as_of')
    if as_of is None:
        cutoff = now
    elif isinstance(as_of, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', as_of):
        day = date.fromisoformat(as_of)
        if day > now.date():
            raise ValueError('截止日期不能在未来')
        cutoff = min(datetime.combine(day + timedelta(days=1), time.min, timezone.utc), now)
    else:
        cutoff = timestamp(as_of)
        if cutoff > now:
            raise ValueError('截止时间不能在未来')
    return {**request, 'sources': sources, 'as_of': cutoff.isoformat()}


def error_result(code, message, detail=None):
    return {'value': {'error': {'code': code, 'message': message}},
            'detail': detail or {}, 'model': None}


def build_value_analysis(request, dataset, artifact):
    try:
        request = normalize_request(request)
    except SourceNotAllowed:
        return error_result('source_not_allowed', 'Value 只接受公开来源，不允许训练赛')
    except (ValueError, TypeError, KeyError, OverflowError):
        return error_result('invalid_request', 'Value 请求参数不合法')
    if artifact is None:
        return error_result('insufficient_data', '该精确版本及来源的 Value 模型尚未就绪', {'reason': 'model_not_ready'})
    try:
        if artifact['model_version'] != MODEL_VERSION or artifact['patch'] != request['patch']:
            return error_result('insufficient_data', '没有匹配该精确版本的模型', {'reason': 'model_patch_mismatch'})
        if set(artifact['sources']) != set(request['sources']):
            return error_result('source_not_allowed', '模型来源与请求不一致，不能扩大数据来源')
        if not artifact['sources'] or any(s not in {'pro_match', 'pub_match'} for s in artifact['sources']):
            return error_result('source_not_allowed', '模型包含未允许来源')
        if not set(artifact.get('sources_used', artifact['sources'])).issubset(request['sources']):
            return error_result('source_not_allowed', '模型实际使用来源超出请求范围')
        metadata = artifact['metadata']
        if metadata['train_n'] < 60 or metadata['validation_n'] < 15:
            return error_result('insufficient_data', '模型训练或时间留出样本不足')
        cutoff = timestamp(request['as_of'])
        context_cutoff = timestamp(metadata['context_cutoff'])
        if (timestamp(metadata['train_end']) >= cutoff or context_cutoff > cutoff
                or metadata.get('validation_end') and timestamp(metadata['validation_end']) > cutoff):
            return error_result('insufficient_data', '模型晚于请求截止时间，拒绝未来信息', {'reason': 'model_after_cutoff'})
        weights = artifact['weights']
        if not weights or any(not isinstance(v, (float, int)) or isinstance(v, bool) or not isfinite(v) for v in weights.values()):
            raise ValueError('模型参数损坏')
        heroes = [h['hero_id'] for side in ('radiant', 'dire') for h in request[side]['heroes']]
        counts = {int(k):v for k,v in artifact['hero_counts'].items()}
        if any(type(v) is not int or v < 0 for v in counts.values()):
            raise ValueError('英雄样本数损坏')
        if any(not counts.get(h) for h in heroes):
            return error_result('insufficient_data', '所选英雄缺少该精确版本训练样本', {'reason': 'hero_unseen_in_training'})
        # 线上上下文与验证口径相同，冻结在训练边界，拒绝动态掺入留出标签。
        base_version = re.sub(r'[a-z]$', '', request['patch'])
        history = [r for r in dataset['matches'] if r['data_source'] in request['sources']
                   and r.get('base_version') == base_version and r.get('ended_at')
                   and timestamp(r['ended_at']) < min(cutoff, context_cutoff)]
        features, detail = extract_features(request, history)
        probability, parts = probability_parts(weights, features)
        n = min((counts[h] for h in heroes), default=metadata['train_n'])
        body = {'radiant_win_prob': probability, 'confidence': 'high' if n >= 200 else 'medium' if n >= 30 else 'low',
                'n_samples': n, 'contributions': [{'factor': factor, 'delta': parts[factor]} for factor in FACTORS],
                'sources_used': sorted(artifact.get('sources_used', artifact['sources']))}
        detail.update({'as_of': request['as_of'], 'context_cutoff': metadata['context_cutoff'],
                       'n_samples_definition': '所选英雄训练出场数的最小值；空局面用训练比赛数',
                       'attribution': '四组 logit 沿共同零基线缩放为概率加项，属于模型归因，不是因果效果',
                       'partial_draft': len(heroes) < 10,
                       'factor_training_support': metadata.get('feature_nonzero_training_examples', {}),
                       'matchup_definition': '同一历史位置的对方英雄，属于位置对照，不宣称实际分路对位',
                       'experimental': True,
                       'quality_gate': '优于固定训练胜率基线' if metadata['validation']['log_loss'] < metadata['baseline']['log_loss'] else '未优于固定训练胜率基线'})
        return {'value': body, 'detail': detail,
                'model': {'version': MODEL_VERSION, 'patch': artifact['patch'], 'sources': artifact['sources'], **metadata}}
    except (KeyError, TypeError, ValueError, OverflowError):
        return error_result('upstream_unavailable', 'Value 模型或历史数据未通过完整性校验')


def build_value(request, dataset, artifact):
    return build_value_analysis(request, dataset, artifact)['value']
