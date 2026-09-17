import pytest
from db.sources import resolve_sources, SourceNotAllowed

def test_opponent_view_excludes_scrim_and_pub(db, seeded):
    db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                  VALUES (2,'scrim',now(),'complete'), (3,'pub_match',now(),'complete')""")
    rows = db.execute("SELECT match_id FROM opponent_profile_matches ORDER BY match_id").fetchall()
    assert [r[0] for r in rows] == [1], "对手画像视图只能看到 pro_match"

def test_pub_view_excludes_scrim(db, seeded):
    db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                  VALUES (2,'scrim',now(),'complete'), (3,'pub_match',now(),'complete')""")
    rows = db.execute("SELECT match_id FROM opponent_profile_matches_with_pub ORDER BY match_id").fetchall()
    assert [r[0] for r in rows] == [1, 3]

def test_resolve_sources_rejects_scrim_for_opponent_paths():
    with pytest.raises(SourceNotAllowed):
        resolve_sources(["pro_match", "scrim"], allow_scrim=False)

def test_resolve_sources_accepts_scrim_when_explicitly_allowed():
    assert resolve_sources(["pro_match", "scrim"], allow_scrim=True) == ["pro_match", "scrim"]

def test_resolve_sources_rejects_unknown():
    with pytest.raises(SourceNotAllowed):
        resolve_sources(["ranked"])
