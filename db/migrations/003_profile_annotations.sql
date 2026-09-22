CREATE TABLE profile_position_annotations (
  match_id          BIGINT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
  team              SMALLINT NOT NULL CHECK (team IN (0, 1)),
  input_fingerprint TEXT NOT NULL,
  method_version    TEXT NOT NULL,
  annotations       JSONB NOT NULL CHECK (jsonb_typeof(annotations) = 'array'),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (match_id, team)
);

CREATE TABLE profile_league_tiers (
  league_id       BIGINT PRIMARY KEY REFERENCES leagues(league_id) ON DELETE CASCADE,
  verified_name   TEXT NOT NULL,
  canonical_tier  TEXT NOT NULL
                  CHECK (canonical_tier IN ('tier1', 'tier2', 'qualifier', 'other')),
  source_url      TEXT NOT NULL,
  method_version  TEXT NOT NULL,
  evidence        JSONB NOT NULL,
  reviewed_at     TIMESTAMPTZ NOT NULL
);
