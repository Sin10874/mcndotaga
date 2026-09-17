from __future__ import annotations
import os
from contextlib import contextmanager
import psycopg, pytest

def _base_dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://dota:dota@localhost:5432/dota") \
        .replace("postgresql+psycopg://", "postgresql://")

@pytest.fixture(scope="session")
def dsn() -> str:
    """建 <db>_test 并跑迁移，使测试不污染开发库（规格 §15）。"""
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        head, dbname = _base_dsn().rsplit("/", 1)
        base = f"{head}/{dbname}_test"
    head, test_name = base.rsplit("/", 1)
    with psycopg.connect(f"{head}/postgres", autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (test_name,)).fetchone():
            c.execute(f'CREATE DATABASE "{test_name}"')
    from db.migrate import apply
    apply(base)
    return base

@pytest.fixture
def db(dsn):
    with psycopg.connect(dsn) as conn:
        yield conn
        conn.rollback()

@contextmanager
def expect_violation(conn):
    """断言语句违反约束，并回滚到保存点使事务可继续。

    必需：psycopg 在约束违规后事务进入 aborted 状态，
    后续任何语句都会以 'current transaction is aborted' 失败。
    """
    conn.execute("SAVEPOINT sp")
    try:
        yield
    except psycopg.Error:
        conn.execute("ROLLBACK TO SAVEPOINT sp")
    except BaseException:
        # 非 psycopg 异常（例如测试里调用的辅助函数抛 KeyError）也必须回滚，
        # 否则块内的写入会留在事务里可见，污染后续断言。
        conn.execute("ROLLBACK TO SAVEPOINT sp")
        raise
    else:
        conn.execute("ROLLBACK TO SAVEPOINT sp")
        pytest.fail("期望约束违规，但语句执行成功了")

@pytest.fixture
def seeded(db):
    """最小可用数据集。

    注意 constants_snapshot 必须写**显式列名**：该表实际列序是
    (snapshot_version, fetched_at, n_heroes, n_items, source)，
    VALUES (1,127,501) 会被当成给 fetched_at 传整数而报类型错误。
    """
    db.execute("""INSERT INTO constants_snapshot
                  (snapshot_version, n_heroes, n_items) VALUES (1, 127, 501)""")
    db.execute("""INSERT INTO heroes(hero_id, name, localized_name)
                  VALUES (80, 'npc_dota_hero_lone_druid', 'Lone Druid')""")
    db.execute("INSERT INTO hero_token_index VALUES (1, 80, 73)")
    db.execute("INSERT INTO leagues(league_id, name) VALUES (1, 'L')")
    db.execute("""INSERT INTO matches(match_id, data_source, started_at, first_pick_team, draft_state)
                  VALUES (1, 'pro_match', now(), 0, 'complete')""")
    return 1
