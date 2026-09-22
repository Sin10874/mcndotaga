ALTER TABLE matches ADD COLUMN game_mode SMALLINT;

CREATE TABLE collector_pro_scans (
  source TEXT PRIMARY KEY,
  mode TEXT NOT NULL CHECK (mode IN ('backfill', 'incremental')),
  cutoff_at TIMESTAMPTZ NOT NULL,
  next_less_than_match_id BIGINT,
  stop_at_match_id BIGINT,
  cycle_high_match_id BIGINT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE collector_match_queue (
  match_id BIGINT PRIMARY KEY,
  discovered_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'complete', 'unavailable', 'non_cm', 'error')),
  next_attempt_at TIMESTAMPTZ,
  last_error TEXT,
  final_attempt_done BOOLEAN NOT NULL DEFAULT false,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX collector_match_queue_due_idx
  ON collector_match_queue (next_attempt_at)
  WHERE status IN ('pending', 'unavailable', 'error') AND NOT final_attempt_done;
