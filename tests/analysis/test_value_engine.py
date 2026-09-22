from copy import deepcopy
from analysis.value_engine import build_value, build_value_analysis

REQ = {'patch':'7.41e','sources':['pro_match'],'first_pick_team':0,
       'as_of':'2026-09-22T00:00:00+00:00',
       'radiant':{'team_id':1,'heroes':[{'hero_id':1}]},
       'dire':{'team_id':2,'heroes':[{'hero_id':6}]}}


def test_missing_model_is_error_not_neutral_probability():
    result = build_value(REQ, {'matches':[]}, None)
    assert result['error']['code'] == 'insufficient_data'


def test_private_source_rejected_before_any_scoring():
    request = deepcopy(REQ)
    request['sources'] = ['scrim']
    assert build_value(request, {'matches':[]}, None)['error']['code'] == 'source_not_allowed'


def artifact():
    return {'model_version':'value-logistic-v2','patch':'7.41e','sources':['pro_match'],
            'weights':{'hero:1':.6,'hero:6':-.2,'first_pick':.1,'side_bias':.05,'counter':1,'comfort':.2},
            'hero_counts':{1:50,6:60,7:45},
            'metadata':{'train_n':80,'validation_n':20,'train_end':'2026-08-01T00:00:00+00:00',
                        'context_cutoff':'2026-08-02T00:00:00+00:00',
                        'validation':{'log_loss':.72},'baseline':{'log_loss':.69}}}


def test_value_is_draft_dependent_and_passes_frozen_contract():
    import yaml
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from contracts.tools.invariants import check_value
    body = build_value(REQ, {'matches':[]}, artifact())
    changed = deepcopy(REQ)
    changed['dire']['heroes'] = [{'hero_id':7}]
    other = build_value(changed, {'matches':[]}, artifact())
    assert body['radiant_win_prob'] != other['radiant_win_prob']
    schema = yaml.safe_load(Path('contracts/openapi.yaml').read_text())
    Draft202012Validator(schema).evolve(schema=schema['components']['schemas']['Value']).validate(body)
    assert not check_value(body)
    assert body['n_samples'] == 50
    assert next(x['delta'] for x in body['contributions'] if x['factor']=='counter_matchup') == 0


def test_empty_draft_uses_model_prior_and_first_pick():
    request = deepcopy(REQ)
    request['radiant']['heroes'] = request['dire']['heroes'] = []
    result = build_value_analysis(request, {'matches':[]}, artifact())
    assert result['value']['n_samples'] == 80
    assert result['detail']['partial_draft']
    assert result['model']['validation']['log_loss'] == .72
    assert result['detail']['quality_gate'] == '未优于固定训练胜率基线'


def test_wrong_patch_future_model_unseen_hero_and_duplicate_rejected():
    from copy import deepcopy
    request = deepcopy(REQ)
    request['patch'] = '7.41f'
    assert build_value(request, {'matches':[]}, artifact())['error']['code'] == 'insufficient_data'
    request = deepcopy(REQ)
    request['as_of'] = '2026-07-20'
    assert build_value_analysis(request, {'matches':[]}, artifact())['detail']['reason'] == 'model_after_cutoff'
    request = deepcopy(REQ)
    request['radiant']['heroes'] = [{'hero_id':99}]
    assert build_value(request, {'matches':[]}, artifact())['error']['code'] == 'insufficient_data'
    request['radiant']['heroes'] = [{'hero_id':6}]
    assert build_value(request, {'matches':[]}, artifact())['error']['code'] == 'invalid_request'


def test_counter_requires_30_conditioned_matches_and_own_baseline():
    from models.value_model import context_features
    from datetime import datetime, timedelta, timezone
    start = datetime(2026,7,1,tzinfo=timezone.utc)
    request = deepcopy(REQ)
    request['radiant']['heroes'][0]['position'] = 1
    request['dire']['heroes'][0]['position'] = 1
    history = []
    for i in range(40):
        history.append({'started_at':start+timedelta(hours=i), 'radiant_win':i < 30,
                        'radiant_team_id':1,'dire_team_id':2,
                        'players':[{'team':0,'account_id':101,'hero_id':1,'position':1},
                                   {'team':1,'account_id':201,'hero_id':6 if i<30 else 7,'position':1}]})
    _, value, detail = context_features(request, history)
    radiant = detail['counter_matchup'][0]
    assert radiant['n'] == 30
    assert radiant['baseline_n'] == 40
    assert radiant['conditional_delta'] == .25
    assert value > 0
    _, value29, detail29 = context_features(request, history[1:])
    assert detail29['counter_matchup'][0]['n'] == 29
    assert detail29['counter_matchup'][0]['reason'] == 'insufficient_samples'
    assert value29 == 0
    other_team = deepcopy(request)
    other_team['radiant']['team_id'] = 3
    _, _, missing = context_features(other_team, history)
    assert missing['counter_matchup'][0]['reason'] == 'stat_unavailable'


def test_private_future_and_other_base_context_do_not_change_response():
    from datetime import datetime, timezone
    bad = {'data_source':'scrim','base_version':'7.41','ended_at':datetime(2026,7,1,tzinfo=timezone.utc)}
    future = {**bad,'data_source':'pro_match','ended_at':datetime(2026,9,1,tzinfo=timezone.utc)}
    other = {**bad,'data_source':'pro_match','base_version':'7.40'}
    assert build_value(REQ, {'matches':[bad,future,other]}, artifact()) == build_value(REQ, {'matches':[]}, artifact())


def test_repository_filters_public_exact_time_and_legal_drafts(db):
    from analysis.value_repository import load_value_dataset
    from ingest.order_families import resolve_in, SPEC_FAMILY
    db.execute("INSERT INTO patches(patch_id,version_name,base_version,released_at) VALUES(1,'7.41e','7.41','2026-07-01'),(2,'7.40a','7.40','2026-01-01')")
    db.execute("INSERT INTO teams(team_id,name) VALUES(1,'甲'),(2,'乙')")
    for hid in range(1,25):
        db.execute('INSERT INTO heroes(hero_id,name,localized_name) VALUES(%s,%s,%s)',(hid,str(hid),str(hid)))
    scenarios = [(1,'pro_match',1,'2026-08-01',False,'complete',3600),
                 (2,'scrim',1,'2026-08-01',False,'complete',3600),
                 (3,'pub_match',1,'2026-08-01',False,'complete',3600),
                 (4,'pro_match',2,'2026-08-01',False,'complete',3600),
                 (5,'pro_match',1,'2026-08-01',True,'complete',3600),
                 (6,'pro_match',1,'2026-08-01',False,'pending',3600),
                 (7,'pro_match',1,'2026-09-22',False,'complete',3600),
                 (8,'pro_match',1,'2026-08-01',False,'complete',None)]
    for mid,source,patch,started,anomaly,state,duration in scenarios:
        db.execute('''INSERT INTO matches(match_id,data_source,patch_id,started_at,anomaly,draft_state,
                    duration_s,radiant_win,first_pick_team,n_draft_actions,radiant_team_id,dire_team_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,true,0,24,1,2)''',
                   (mid,source,patch,started+'T00:00:00+00:00',anomaly,state,duration))
        for order in range(24):
            pick, team = resolve_in(SPEC_FAMILY, order, 0)
            db.execute('INSERT INTO draft_actions(match_id,ord,team,is_pick,hero_id) VALUES(%s,%s,%s,%s,%s)',
                       (mid,order,team,pick,order+1))
    result = load_value_dataset(db, REQ)
    assert [r['match_id'] for r in result['matches']] == [1]
    assert set(result['matches'][0]) >= {'ended_at','radiant_heroes','dire_heroes','players'}
    assert not {'kills','gpm','net_worth','lane_role'} & set(result['matches'][0])
    # 历史标注要核对原始指纹，不能直接信任旁表。
    from psycopg.types.json import Jsonb
    from analysis.position_inference import METHOD_VERSION, infer_team_positions, team_input_fingerprint
    raw = []
    for slot in range(5):
        pid = 101+slot
        db.execute('INSERT INTO players(account_id) VALUES(%s)', (pid,))
        lane = [1,2,3,3,1][slot]
        economy = [10000,14000,12000,4000,2000][slot]
        db.execute("""INSERT INTO match_players(match_id,player_slot,team,account_id,hero_id,lane_role,
                   last_hits,net_worth,gpm,xpm,stats_available) VALUES(1,%s,0,%s,%s,%s,%s,%s,%s,%s,true)""",
                   (slot,pid,slot+1,lane,economy,economy,economy,economy))
        raw.append({'match_id':1,'player_slot':slot,'team':0,'account_id':pid,'hero_id':slot+1,
                    'position':None,'lane_role':lane,'last_hits':economy,'net_worth':economy,
                    'gpm':economy,'xpm':economy,'stats_available':True})
    db.execute("UPDATE matches SET parse_state='full' WHERE match_id=1")
    match = {'match_id':1,'data_source':'pro_match','parse_state':'full'}
    annotation = infer_team_positions(match, raw)
    db.execute('INSERT INTO profile_position_annotations VALUES(1,0,%s,%s,%s,now())',
               (team_input_fingerprint(match,raw),METHOD_VERSION,Jsonb(annotation)))
    enriched = load_value_dataset(db, REQ)['matches'][0]['players']
    assert sum(p['position_source']=='heuristic' for p in enriched) == 5
    assert all(not {'gpm','net_worth','lane_role'} & set(p) for p in enriched)
    db.execute('UPDATE match_players SET gpm=gpm+1 WHERE match_id=1 AND player_slot=0')
    stale = load_value_dataset(db, REQ)['matches'][0]['players']
    assert all(p['position'] is None for p in stale)


def test_past_inferred_position_can_support_comfort_and_is_identified():
    from models.value_model import context_features
    history = [{'started_at':f'2026-07-{day:02d}T00:00:00+00:00','radiant_team_id':1,
                'dire_team_id':2,'radiant_win':True,
                'players':[{'team':0,'account_id':101,'hero_id':1,'position':2,'position_source':'heuristic'}]}
               for day in range(1,8)]
    comfort, _, detail = context_features(REQ, history)
    assert comfort > 0
    assert detail['player_comfort'][0]['position_method'] == 'historical_inferred_position'
    assert detail['player_comfort'][0]['comfort_n'] == 7


def test_invalid_cutoff_type_and_corrupt_model_are_safe_errors():
    request = deepcopy(REQ)
    request['as_of'] = 123
    assert build_value(request, {'matches':[]}, artifact())['error']['code'] == 'invalid_request'
    bad = artifact()
    bad['sources_used'] = ['scrim']
    assert build_value(REQ, {'matches':[]}, bad)['error']['code'] == 'source_not_allowed'
    bad = artifact()
    bad['hero_counts'][1] = -4
    assert build_value(REQ, {'matches':[]}, bad)['error']['code'] == 'upstream_unavailable'


def test_model_diagnostics_do_not_expose_validation_after_requested_cutoff():
    request = deepcopy(REQ)
    request['as_of'] = '2026-08-05'
    model = artifact()
    model['metadata']['validation_end'] = '2026-08-10T00:00:00+00:00'
    result = build_value_analysis(request, {'matches':[]}, model)
    assert result['value']['error']['code'] == 'insufficient_data'
    assert result['detail']['reason'] == 'model_after_cutoff'


def test_unknown_team_context_is_never_pooled_into_anonymous_team():
    from models.value_model import context_features
    request = deepcopy(REQ)
    request['radiant']['team_id'] = None
    request['radiant']['heroes'][0]['position'] = 1
    history = [{'started_at':'2026-07-01T00:00:00+00:00','radiant_team_id':None,
                'dire_team_id':2,'radiant_win':True,
                'players':[{'team':0,'account_id':101,'hero_id':1,'position':1}]}]
    comfort, counter, detail = context_features(request, history)
    assert comfort == 0
    assert counter == 0
    assert not detail['player_comfort'][0]['player_known']
