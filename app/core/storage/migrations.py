"""数据库迁移定义。

迁移按版本号顺序执行，执行记录写入 ``schema_version`` 表。
新增结构变更时**只追加**新的 :class:`Migration`，不要修改历史迁移。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Final

from app.core.common.logger import get_logger

_log = get_logger("storage.migrations")


@dataclass(frozen=True, slots=True)
class Migration:
    """一次数据库结构变更。"""

    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration(
        version=1,
        name="initial_schema",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS download_tasks (
                task_id               TEXT PRIMARY KEY,
                url                   TEXT NOT NULL,
                final_url             TEXT NOT NULL DEFAULT '',
                file_name             TEXT NOT NULL,
                save_dir              TEXT NOT NULL,
                save_path             TEXT,
                temp_dir              TEXT,
                total_size            INTEGER NOT NULL DEFAULT 0,
                downloaded            INTEGER NOT NULL DEFAULT 0,
                status                TEXT NOT NULL,
                mode                  TEXT NOT NULL DEFAULT 'single',
                content_type          TEXT NOT NULL DEFAULT '',
                etag                  TEXT NOT NULL DEFAULT '',
                last_modified         TEXT NOT NULL DEFAULT '',
                supports_range        INTEGER NOT NULL DEFAULT 0,
                can_resume            INTEGER NOT NULL DEFAULT 0,
                connection_count      INTEGER NOT NULL DEFAULT 1,
                requested_connections INTEGER NOT NULL DEFAULT 8,
                sha256                TEXT NOT NULL DEFAULT '',
                expected_sha256       TEXT NOT NULL DEFAULT '',
                error_message         TEXT NOT NULL DEFAULT '',
                notice                TEXT NOT NULL DEFAULT '',
                retry_count           INTEGER NOT NULL DEFAULT 0,
                created_at            REAL NOT NULL,
                updated_at            REAL NOT NULL,
                completed_at          REAL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_download_tasks_status ON download_tasks(status)",
            "CREATE INDEX IF NOT EXISTS idx_download_tasks_created ON download_tasks(created_at DESC)",
            """
            CREATE TABLE IF NOT EXISTS download_segments (
                task_id      TEXT NOT NULL,
                index_no     INTEGER NOT NULL,
                start_byte   INTEGER NOT NULL,
                end_byte     INTEGER NOT NULL,
                current_byte INTEGER NOT NULL DEFAULT 0,
                state        TEXT NOT NULL DEFAULT 'pending',
                retry_count  INTEGER NOT NULL DEFAULT 0,
                error        TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (task_id, index_no),
                FOREIGN KEY (task_id) REFERENCES download_tasks(task_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS history_events (
                event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                kind       TEXT NOT NULL,
                action     TEXT NOT NULL,
                title      TEXT NOT NULL,
                detail     TEXT NOT NULL DEFAULT '',
                path       TEXT NOT NULL DEFAULT '',
                url        TEXT NOT NULL DEFAULT '',
                size       INTEGER NOT NULL DEFAULT 0,
                status     TEXT NOT NULL DEFAULT 'success',
                created_at REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_history_created ON history_events(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_history_kind ON history_events(kind, created_at DESC)",
            """
            CREATE TABLE IF NOT EXISTS organize_rules (
                rule_id    TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                enabled    INTEGER NOT NULL DEFAULT 1,
                priority   INTEGER NOT NULL DEFAULT 100,
                field      TEXT NOT NULL,
                value      TEXT NOT NULL DEFAULT '',
                action     TEXT NOT NULL DEFAULT 'move',
                target_dir TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_rules_priority ON organize_rules(priority, name)",
        ),
    ),
)

LATEST_VERSION: Final[int] = MIGRATIONS[-1].version


def current_version(connection: sqlite3.Connection) -> int:
    """读取当前数据库结构版本（未初始化时返回 0）。"""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at REAL NOT NULL
        )
        """
    )
    row = connection.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()
    if row is None:
        return 0
    value = row["version"] if isinstance(row, sqlite3.Row) else row[0]
    return int(value or 0)


def apply_migrations(connection: sqlite3.Connection) -> int:
    """依次执行未应用的迁移，返回最终版本号。"""
    version = current_version(connection)
    for migration in MIGRATIONS:
        if migration.version <= version:
            continue
        _log.info("应用数据库迁移 #%s (%s)", migration.version, migration.name)
        with connection:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT OR REPLACE INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, time.time()),
            )
        version = migration.version
    return version
