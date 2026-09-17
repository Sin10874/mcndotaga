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

    两点取舍（有意为之，不是疏忽）：
    - WITH (FORCE) 需要 PG 13+（本机 16.14）。它会**踢掉连到该库的其他会话**，
      因此两个 pytest 会话并行跑会互相 DROP/CREATE。当前依赖**串行执行**；
      将来若要并行（pytest-xdist），必须改为每 worker 一个库名。
    - DROP/CREATE 每次 session 多花几百毫秒，换来"schema 永远对应当前迁移"。
    """
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        head, dbname = _base_dsn().rsplit("/", 1)
        base = f"{head}/{dbname}_test"
    head, test_name = base.rsplit("/", 1)
    # 库名是标识符，不能参数化（%s 只能用在值的位置），故手工转义双引号
    ident = test_name.replace('"', '""')
    with psycopg.connect(f"{head}/postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{ident}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{ident}"')
    from db.migrate import apply
    apply(base)
    return base

@pytest.fixture
def db(dsn):
    with psycopg.connect(dsn) as conn:
        yield conn
        conn.rollback()

@contextmanager
def expect_violation(conn, sqlstate: str | None = None,
                     constraint: str | tuple[str, ...] | None = None):
    """断言语句违反约束，并回滚到保存点使事务可继续。

    必需：psycopg 在约束违规后事务进入 aborted 状态，
    后续任何语句都会以 'current transaction is aborted' 失败。

    sqlstate / constraint：钉死"拒绝到底来自哪条约束"。都不传时接受任何
    psycopg.Error——**这是个很宽的网**：一条被别的约束拒绝的语句同样能让
    with 块通过，于是测试名声称在守护 X、实际什么都没守护。本项目已实测踩到
    这一点（一条声称守护 (match_id, player_slot) 主键的测试，实际可以是被
    FK 拒绝）。故**所有负向测试都必须显式传 sqlstate**；同一 SQLSTATE 下有
    歧义风险时再传 constraint。

    constraint 收字符串或字符串元组：PG 对具名约束报约束名，对**部分唯一
    索引**报索引名（如 rosters 的 left_at IS NULL 那条），两者形式不同。
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
        if constraint is not None:
            allowed = (constraint,) if isinstance(constraint, str) else constraint
            actual = exc.diag.constraint_name
            if actual not in allowed:
                pytest.fail(f"期望约束 {allowed}，实际为 {actual!r}：{exc}")
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
