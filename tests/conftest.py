from __future__ import annotations
import os
from contextlib import contextmanager
import psycopg, pytest

def _base_dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://dota:dota@localhost:5432/dota") \
        .replace("postgresql+psycopg://", "postgresql://")

@pytest.fixture(scope="session")
def dsn() -> str:
    """每 session 重建 <db>_test 并跑迁移，使测试不污染开发库（规格 §15）。

    必须 DROP + CREATE，不能只在库不存在时创建：否则只要 ledger 里已有
    001_schema.sql，对该迁移的任何后续修改都不会被应用，测试会**静默**跑在
    旧 schema 上（改了 DDL 却依然全绿）。Task 3 起会频繁改 schema，这个坑
    必然再踩，故每次 session 都从零重建以换取「schema 永远对应当前迁移」。

    WITH (FORCE) 需要 PG 13+（本机 16.14）。
    """
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        head, dbname = _base_dsn().rsplit("/", 1)
        base = f"{head}/{dbname}_test"
    head, test_name = base.rsplit("/", 1)
    with psycopg.connect(f"{head}/postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)')
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
def expect_violation(conn, sqlstate: str | None = None):
    """断言语句违反约束，并回滚到保存点使事务可继续。

    必需：psycopg 在约束违规后事务进入 aborted 状态，
    后续任何语句都会以 'current transaction is aborted' 失败。

    sqlstate：不传则接受任何 psycopg.Error（宽松，仅适用于"被任意约束拒绝
    即可"的断言）；传入则要求恰好是该 SQLSTATE，否则视为测试失败。
    **守护唯一性/主键的测试必须显式传入**——psycopg.Error 是个很宽的网，
    一条被 FK（23503）或 CHECK（23514）拒绝的语句同样能让 with 块通过，
    于是测试名声称在守护主键、实际什么都没守护。
    """
    conn.execute("SAVEPOINT sp")
    try:
        yield
    except psycopg.Error as exc:
        conn.execute("ROLLBACK TO SAVEPOINT sp")
        if sqlstate is not None and exc.sqlstate != sqlstate:
            pytest.fail(
                f"期望 SQLSTATE {sqlstate}，实际为 {exc.sqlstate}：{exc}"
            )
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
