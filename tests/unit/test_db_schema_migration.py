"""数据库 schema 迁移行为测试。"""

from pathlib import Path

from whisper_stt_service.db import Database


def test_init_schema_adds_dag_and_archive_tables(tmp_path: Path) -> None:
    """init_schema 应补齐 DAG 相关列和归档表。"""

    db = Database(tmp_path / "q.db")
    db.init_schema()

    with db.connect() as conn:
        jobs_cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        tasks_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert "dag_json" in jobs_cols
    assert "job_config_json" in jobs_cols
    assert "task_config_json" in tasks_cols
    assert "jobs_archive" in tables
    assert "tasks_archive" in tables
