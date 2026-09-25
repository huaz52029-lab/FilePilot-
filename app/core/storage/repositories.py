"""仓储层：把领域模型与 SQLite 表相互映射。

UI 与下载引擎都不直接写 SQL，只通过仓储读写数据。
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from app.core.common.logger import get_logger
from app.core.download.models import (
    DownloadMode,
    DownloadSegment,
    DownloadStatus,
    DownloadTask,
    SegmentState,
)
from app.core.files.models import MatchField
from app.core.files.rules import OrganizeRule
from app.core.storage.database import Database
from app.core.storage.models import HistoryEvent, HistoryKind, HistoryStatus

_log = get_logger("storage.repositories")


# ---------------------------------------------------------------------------
# 映射辅助
# ---------------------------------------------------------------------------
def _enum_or(default, enum_type, value):  # type: ignore[no-untyped-def]
    try:
        return enum_type(value)
    except (ValueError, TypeError):
        return default


def row_to_task(row) -> DownloadTask:  # type: ignore[no-untyped-def]
    """数据库行 → :class:`DownloadTask`。"""
    save_dir = Path(row["save_dir"])
    save_path = Path(row["save_path"]) if row["save_path"] else None
    temp_dir = Path(row["temp_dir"]) if row["temp_dir"] else None
    task = DownloadTask(
        task_id=str(row["task_id"]),
        url=str(row["url"]),
        final_url=str(row["final_url"]),
        file_name=str(row["file_name"]),
        save_dir=save_dir,
        save_path=save_path,
        temp_dir=temp_dir,
        total_size=int(row["total_size"]),
        downloaded=int(row["downloaded"]),
        status=_enum_or(DownloadStatus.PENDING, DownloadStatus, row["status"]),
        mode=_enum_or(DownloadMode.SINGLE, DownloadMode, row["mode"]),
        content_type=str(row["content_type"]),
        etag=str(row["etag"]),
        last_modified=str(row["last_modified"]),
        supports_range=bool(row["supports_range"]),
        can_resume=bool(row["can_resume"]),
        connection_count=int(row["connection_count"]),
        requested_connections=int(row["requested_connections"]),
        sha256=str(row["sha256"]),
        expected_sha256=str(row["expected_sha256"]),
        error_message=str(row["error_message"]),
        notice=str(row["notice"]),
        retry_count=int(row["retry_count"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        completed_at=float(row["completed_at"]) if row["completed_at"] else None,
    )
    return task


def _task_params(task: DownloadTask) -> tuple:
    return (
        task.task_id,
        task.url,
        task.final_url,
        task.file_name,
        str(task.save_dir),
        str(task.save_path) if task.save_path else None,
        str(task.temp_dir) if task.temp_dir else None,
        int(task.total_size),
        int(task.downloaded),
        task.status.value,
        task.mode.value,
        task.content_type,
        task.etag,
        task.last_modified,
        int(task.supports_range),
        int(task.can_resume),
        int(task.connection_count),
        int(task.requested_connections),
        task.sha256,
        task.expected_sha256,
        task.error_message,
        task.notice,
        int(task.retry_count),
        float(task.created_at),
        float(task.updated_at),
        float(task.completed_at) if task.completed_at else None,
    )


def row_to_segment(row) -> DownloadSegment:  # type: ignore[no-untyped-def]
    """数据库行 → :class:`DownloadSegment`。"""
    return DownloadSegment(
        index=int(row["index_no"]),
        start=int(row["start_byte"]),
        end=int(row["end_byte"]),
        current=int(row["current_byte"]),
        state=_enum_or(SegmentState.PENDING, SegmentState, row["state"]),
        retry_count=int(row["retry_count"]),
        error=str(row["error"]),
    )


def row_to_rule(row) -> OrganizeRule:  # type: ignore[no-untyped-def]
    """数据库行 → :class:`OrganizeRule`。"""
    return OrganizeRule(
        rule_id=str(row["rule_id"]),
        name=str(row["name"]),
        enabled=bool(row["enabled"]),
        priority=int(row["priority"]),
        field=_enum_or(MatchField.EXTENSION, MatchField, row["field"]),
        value=str(row["value"]),
        target_dir=Path(row["target_dir"]) if row["target_dir"] else Path(),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


class BaseRepository:
    """仓储基类。"""

    def __init__(self, database: Database) -> None:
        self._db = database


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------
class SettingsRepository(BaseRepository):
    """键值设置表（数据库侧，QSettings 负责窗口状态等 UI 数据）。"""

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return str(row["value"]) if row is not None else default

    def get_all(self) -> dict[str, str]:
        rows = self._db.query_all("SELECT key, value FROM settings")
        return {str(row["key"]): str(row["value"]) for row in rows}

    def set(self, key: str, value: str) -> None:
        self._db.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, time.time()),
        )

    def set_many(self, values: dict[str, str]) -> None:
        now = time.time()
        self._db.execute_many(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            [(key, value, now) for key, value in values.items()],
        )

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM settings WHERE key = ?", (key,))


# ---------------------------------------------------------------------------
# 下载任务
# ---------------------------------------------------------------------------
_TASK_UPSERT = """
INSERT INTO download_tasks (
    task_id, url, final_url, file_name, save_dir, save_path, temp_dir, total_size, downloaded,
    status, mode, content_type, etag, last_modified, supports_range, can_resume,
    connection_count, requested_connections, sha256, expected_sha256, error_message, notice,
    retry_count, created_at, updated_at, completed_at
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
ON CONFLICT(task_id) DO UPDATE SET
    url = excluded.url,
    final_url = excluded.final_url,
    file_name = excluded.file_name,
    save_dir = excluded.save_dir,
    save_path = excluded.save_path,
    temp_dir = excluded.temp_dir,
    total_size = excluded.total_size,
    downloaded = excluded.downloaded,
    status = excluded.status,
    mode = excluded.mode,
    content_type = excluded.content_type,
    etag = excluded.etag,
    last_modified = excluded.last_modified,
    supports_range = excluded.supports_range,
    can_resume = excluded.can_resume,
    connection_count = excluded.connection_count,
    requested_connections = excluded.requested_connections,
    sha256 = excluded.sha256,
    expected_sha256 = excluded.expected_sha256,
    error_message = excluded.error_message,
    notice = excluded.notice,
    retry_count = excluded.retry_count,
    updated_at = excluded.updated_at,
    completed_at = excluded.completed_at
"""

_SEGMENT_UPSERT = """
INSERT INTO download_segments (
    task_id, index_no, start_byte, end_byte, current_byte, state, retry_count, error
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(task_id, index_no) DO UPDATE SET
    start_byte = excluded.start_byte,
    end_byte = excluded.end_byte,
    current_byte = excluded.current_byte,
    state = excluded.state,
    retry_count = excluded.retry_count,
    error = excluded.error
"""


class DownloadRepository(BaseRepository):
    """下载任务与分段的持久化。"""

    def save_task(self, task: DownloadTask, *, include_segments: bool = True) -> None:
        """写入或更新任务（含分段）。"""
        with self._db.transaction() as connection:
            connection.execute(_TASK_UPSERT, _task_params(task))
            if include_segments:
                self._save_segments(connection, task)

    def _save_segments(self, connection, task: DownloadTask) -> None:  # type: ignore[no-untyped-def]
        if not task.segments:
            return
        connection.executemany(
            _SEGMENT_UPSERT,
            [
                (
                    task.task_id,
                    segment.index,
                    segment.start,
                    segment.end,
                    segment.current,
                    segment.state.value,
                    segment.retry_count,
                    segment.error,
                )
                for segment in task.segments
            ],
        )

    def update_progress(
        self,
        task_id: str,
        *,
        downloaded: int,
        status: DownloadStatus,
        segments: Sequence[DownloadSegment] | None = None,
        retry_count: int | None = None,
        error_message: str | None = None,
        notice: str | None = None,
        total_size: int | None = None,
        connection_count: int | None = None,
        mode: DownloadMode | None = None,
    ) -> None:
        """轻量更新：用于高频进度写入（不重写整行）。"""
        fields = ["downloaded = ?", "status = ?", "updated_at = ?"]
        params: list[object] = [int(downloaded), status.value, time.time()]
        if total_size is not None:
            fields.append("total_size = ?")
            params.append(int(total_size))
        if connection_count is not None:
            fields.append("connection_count = ?")
            params.append(int(connection_count))
        if mode is not None:
            fields.append("mode = ?")
            params.append(mode.value)
        if retry_count is not None:
            fields.append("retry_count = ?")
            params.append(int(retry_count))
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(error_message)
        if notice is not None:
            fields.append("notice = ?")
            params.append(notice)
        params.append(task_id)
        with self._db.transaction() as connection:
            connection.execute(
                f"UPDATE download_tasks SET {', '.join(fields)} WHERE task_id = ?", params
            )
            if segments:
                connection.executemany(
                    """
                    UPDATE download_segments
                    SET current_byte = ?, state = ?, retry_count = ?, error = ?
                    WHERE task_id = ? AND index_no = ?
                    """,
                    [
                        (
                            segment.current,
                            segment.state.value,
                            segment.retry_count,
                            segment.error,
                            task_id,
                            segment.index,
                        )
                        for segment in segments
                    ],
                )

    def get_task(self, task_id: str, *, with_segments: bool = True) -> DownloadTask | None:
        row = self._db.query_one("SELECT * FROM download_tasks WHERE task_id = ?", (task_id,))
        if row is None:
            return None
        task = row_to_task(row)
        if with_segments:
            task.segments = self.list_segments(task_id)
        return task

    def list_segments(self, task_id: str) -> list[DownloadSegment]:
        rows = self._db.query_all(
            "SELECT * FROM download_segments WHERE task_id = ? ORDER BY index_no", (task_id,)
        )
        return [row_to_segment(row) for row in rows]

    def list_tasks(
        self,
        *,
        statuses: Iterable[DownloadStatus] | None = None,
        limit: int | None = None,
        order_by: str = "created_at DESC",
        with_segments: bool = False,
    ) -> list[DownloadTask]:
        """列出任务。``order_by`` 只允许内部白名单字段。"""
        allowed_orders = {
            "created_at DESC",
            "created_at ASC",
            "updated_at DESC",
            "updated_at ASC",
        }
        safe_order = order_by if order_by in allowed_orders else "created_at DESC"
        sql = "SELECT * FROM download_tasks"
        params: list[object] = []
        if statuses:
            values = [status.value for status in statuses]
            placeholders = ", ".join("?" for _ in values)
            sql += f" WHERE status IN ({placeholders})"
            params.extend(values)
        sql += f" ORDER BY {safe_order}"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self._db.query_all(sql, params)
        tasks = [row_to_task(row) for row in rows]
        if with_segments:
            for task in tasks:
                task.segments = self.list_segments(task.task_id)
        return tasks

    def list_unfinished(self) -> list[DownloadTask]:
        """未完成任务（用于启动时恢复）。"""
        unfinished = (
            DownloadStatus.PENDING,
            DownloadStatus.PROBING,
            DownloadStatus.QUEUED,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.RETRYING,
            DownloadStatus.PAUSED,
            DownloadStatus.FAILED,
        )
        return self.list_tasks(statuses=unfinished, order_by="created_at ASC", with_segments=True)

    def count_by_status(self) -> dict[DownloadStatus, int]:
        rows = self._db.query_all(
            "SELECT status, COUNT(*) AS total FROM download_tasks GROUP BY status"
        )
        counts: dict[DownloadStatus, int] = {}
        for row in rows:
            status = _enum_or(None, DownloadStatus, row["status"])
            if status is not None:
                counts[status] = int(row["total"])
        return counts

    def total_downloaded_since(self, since: float) -> int:
        """统计某时间点之后完成任务的累计大小（今日下载量）。"""
        value = self._db.query_scalar(
            """
            SELECT COALESCE(SUM(total_size), 0) FROM download_tasks
            WHERE status = ? AND completed_at IS NOT NULL AND completed_at >= ?
            """,
            (DownloadStatus.COMPLETED.value, float(since)),
            default=0,
        )
        return int(value or 0)

    def delete_task(self, task_id: str) -> None:
        """删除任务记录（含分段）。不删除磁盘文件。"""
        with self._db.transaction() as connection:
            connection.execute("DELETE FROM download_segments WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM download_tasks WHERE task_id = ?", (task_id,))

    def delete_finished(self) -> int:
        """删除全部已完成 / 已取消任务记录，返回删除数量。"""
        rows = self._db.query_all(
            "SELECT task_id FROM download_tasks WHERE status IN (?, ?)",
            (DownloadStatus.COMPLETED.value, DownloadStatus.CANCELLED.value),
        )
        ids = [str(row["task_id"]) for row in rows]
        for task_id in ids:
            self.delete_task(task_id)
        return len(ids)


# ---------------------------------------------------------------------------
# 历史记录
# ---------------------------------------------------------------------------
class HistoryRepository(BaseRepository):
    """下载 / 整理 / 校验 / 文件操作历史。"""

    def add(
        self,
        *,
        kind: HistoryKind,
        action: str,
        title: str,
        detail: str = "",
        path: str = "",
        url: str = "",
        size: int = 0,
        status: HistoryStatus = HistoryStatus.SUCCESS,
        created_at: float | None = None,
    ) -> int:
        cursor = self._db.execute(
            """
            INSERT INTO history_events
                (kind, action, title, detail, path, url, size, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind.value,
                action,
                title,
                detail,
                path,
                url,
                int(size),
                status.value,
                float(created_at if created_at is not None else time.time()),
            ),
        )
        return int(cursor.lastrowid or 0)

    def list_events(
        self,
        *,
        kinds: Iterable[HistoryKind] | None = None,
        query: str = "",
        limit: int = 300,
        offset: int = 0,
    ) -> list[HistoryEvent]:
        sql = "SELECT * FROM history_events"
        conditions: list[str] = []
        params: list[object] = []
        if kinds:
            values = [kind.value for kind in kinds]
            placeholders = ", ".join("?" for _ in values)
            conditions.append(f"kind IN ({placeholders})")
            params.extend(values)
        if query.strip():
            conditions.append("(title LIKE ? OR detail LIKE ? OR path LIKE ?)")
            like = f"%{query.strip()}%"
            params.extend([like, like, like])
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        rows = self._db.query_all(sql, params)
        return [HistoryEvent.from_row(row) for row in rows]

    def count(self, *, kinds: Iterable[HistoryKind] | None = None, query: str = "") -> int:
        sql = "SELECT COUNT(*) FROM history_events"
        conditions: list[str] = []
        params: list[object] = []
        if kinds:
            values = [kind.value for kind in kinds]
            placeholders = ", ".join("?" for _ in values)
            conditions.append(f"kind IN ({placeholders})")
            params.extend(values)
        if query.strip():
            conditions.append("(title LIKE ? OR detail LIKE ? OR path LIKE ?)")
            like = f"%{query.strip()}%"
            params.extend([like, like, like])
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        return int(self._db.query_scalar(sql, params, default=0) or 0)

    def clear(self, *, kinds: Iterable[HistoryKind] | None = None) -> int:
        """清空历史（可按类型）。返回删除条数。"""
        if kinds:
            values = [kind.value for kind in kinds]
            placeholders = ", ".join("?" for _ in values)
            count = int(
                self._db.query_scalar(
                    f"SELECT COUNT(*) FROM history_events WHERE kind IN ({placeholders})",
                    values,
                    default=0,
                )
                or 0
            )
            self._db.execute(
                f"DELETE FROM history_events WHERE kind IN ({placeholders})", values
            )
            return count
        count = int(self._db.query_scalar("SELECT COUNT(*) FROM history_events", default=0) or 0)
        self._db.execute("DELETE FROM history_events")
        return count

    def latest(self, limit: int = 5, *, kinds: Iterable[HistoryKind] | None = None) -> list[HistoryEvent]:
        """最近若干条历史记录（首页使用）。"""
        return self.list_events(kinds=kinds, limit=limit)


# ---------------------------------------------------------------------------
# 整理规则
# ---------------------------------------------------------------------------
class RuleRepository(BaseRepository):
    """自定义整理规则持久化。"""

    def list_rules(self, *, enabled_only: bool = False) -> list[OrganizeRule]:
        sql = "SELECT * FROM organize_rules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY priority ASC, name ASC"
        return [row_to_rule(row) for row in self._db.query_all(sql)]

    def save_rule(self, rule: OrganizeRule) -> None:
        rule.touch()
        self._db.execute(
            """
            INSERT INTO organize_rules
                (rule_id, name, enabled, priority, field, value, action, target_dir, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rule_id) DO UPDATE SET
                name = excluded.name,
                enabled = excluded.enabled,
                priority = excluded.priority,
                field = excluded.field,
                value = excluded.value,
                action = excluded.action,
                target_dir = excluded.target_dir,
                updated_at = excluded.updated_at
            """,
            (
                rule.rule_id,
                rule.name,
                int(rule.enabled),
                int(rule.priority),
                rule.field.value,
                rule.value,
                "move",
                str(rule.target_dir),
                float(rule.created_at),
                float(rule.updated_at),
            ),
        )

    def delete_rule(self, rule_id: str) -> None:
        self._db.execute("DELETE FROM organize_rules WHERE rule_id = ?", (rule_id,))

    def set_enabled(self, rule_id: str, enabled: bool) -> None:
        self._db.execute(
            "UPDATE organize_rules SET enabled = ?, updated_at = ? WHERE rule_id = ?",
            (int(enabled), time.time(), rule_id),
        )
