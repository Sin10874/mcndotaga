import pytest

from tests import conftest


def test_missing_explicit_test_dsn_never_opens_a_database(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://dota@localhost:5432/dota")
    def forbidden(*args, **kwargs):
        raise AssertionError("不能连接任何默认数据库")
    monkeypatch.setattr(conftest.psycopg, "connect", forbidden)
    with pytest.raises(pytest.exit.Exception, match="必须显式设置 TEST_DATABASE_URL"):
        conftest.dsn.__wrapped__()


def test_explicit_non_test_database_is_rejected_before_connection(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://dota@localhost:5432/real_data")
    def forbidden(*args, **kwargs):
        raise AssertionError("不能连接非测试库")
    monkeypatch.setattr(conftest.psycopg, "connect", forbidden)
    with pytest.raises(pytest.exit.Exception, match="必须以 '_test' 结尾"):
        conftest.dsn.__wrapped__()
