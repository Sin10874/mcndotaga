-- 规格 §4.1：对手侧数据路径的唯一合法来源。刻意不含 scrim。
CREATE OR REPLACE VIEW opponent_profile_matches AS
SELECT * FROM matches WHERE data_source = 'pro_match';

-- 显式开启 pub_match 时的放宽版；仍不含 scrim。
CREATE OR REPLACE VIEW opponent_profile_matches_with_pub AS
SELECT * FROM matches WHERE data_source IN ('pro_match','pub_match');
