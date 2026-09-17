"""Apply SQL migrations then views. Idempotent via schema_migrations."""
from __future__ import annotations
import os, pathlib, sys
import psycopg

HERE = pathlib.Path(__file__).parent
MIGRATIONS = HERE / "migrations"
VIEWS = HERE / "views"

def dsn_from_env() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://dota:dota@localhost:5432/dota") \
        .replace("postgresql+psycopg://", "postgresql://")

def apply(dsn: str | None = None) -> None:
    with psycopg.connect(dsn or dsn_from_env()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        applied = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in applied:
                print(f"skip  {path.name}"); continue
            print(f"apply {path.name}")
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (path.name,))
        # 视图用 CREATE OR REPLACE，每次重跑以拾取变更
        for path in sorted(VIEWS.glob("*.sql")):
            print(f"view  {path.name}")
            conn.execute(path.read_text(encoding="utf-8"))
        conn.commit()

if __name__ == "__main__":
    apply()
