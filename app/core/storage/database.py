"""SQLite 连接管理。

* 每个线程持有独立连接（``threading.local``），避免跨线程复用连接；
* 启用 WAL 与外键约束；
* 写操作串行化并支持嵌套事务（内层复用外层事务）；
* 仅使用标准库 ``sqlite3``，不引入 ORM。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.common.exceptions import DatabaseError
from app.core.common.logger import get_logger
from app.core.storage.migrations import apply_migrations, current_version

_log = get_logger("storage.database")

SqlParams = Sequence[Any] | dict[str, Any] | None


class Database:
    """线程安全的 SQLite 封装。"""

    def __init__(self, path: Path | str, *, timeout: float = 20.0) -> None:
        self._path = Path(path)
        self._timeout = timeout
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.RLock()
        self._initialized = False

    # -- 基础 --------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    @property
    def version(self) -> int:
        return current_version(self.connection)

    @property
    def connection(self) -> sqlite3.Connection:
        """返回当前线程的连接（按需创建）。"""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._create_connection()
            self._local.connection = connection
        return connection

    def _create_connection(self) -> sqlite3.Connection:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(self._path),
                timeout=self._timeout,
                isolation_level=None,
            )
        except sqlite3.Error as exc:  # pragma: no cover - 取决于文件系统状态
            raise DatabaseError("无法打开本地数据库文件。", detail=f"{self._path}: {exc}") from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={int(self._timeout * 1000)}")
        with self._lock:
            self._connections.append(connection)
        return connection

    def initialize(self) -> int:
        """创建目录并执行迁移，返回数据库版本号。"""
        if self._initialized:
            return self.version
        try:
            version = apply_migrations(self.connection)
        except sqlite3.Error as exc:
            raise DatabaseError("初始化数据库失败。", detail=str(exc)) from exc
        self._initialized = True
        _log.info("数据库就绪：%s（结构版本 %s）", self._path, version)
        return version

    # -- 查询与执行 --------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """显式事务块，异常时自动回滚；支持嵌套（内层复用外层事务）。"""
        connection = self.connection
        depth = getattr(self._local, "tx_depth", 0)
        outer = depth == 0
        with self._lock:
            self._local.tx_depth = depth + 1
            if outer:
                connection.execute("BEGIN")
            try:
                yield connection
            except Exception:
                if outer:
                    connection.rollback()
                raise
            else:
                if outer:
                    connection.commit()
            finally:
                self._local.tx_depth = depth

    def execute(self, sql: str, params: SqlParams = None) -> sqlite3.Cursor:
        """执行单条 SQL（自动提交；位于事务中时由外层事务提交）。"""
        try:
            with self._lock:
                connection = self.connection
                cursor = connection.execute(sql, params or ())
                if not connection.in_transaction:
                    connection.commit()
                return cursor
        except sqlite3.Error as exc:
            raise DatabaseError("数据库写入失败。", detail=f"SQL: {sql} | {exc}") from exc

    def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        """批量执行 SQL。"""
        try:
            with self._lock:
                connection = self.connection
                connection.executemany(sql, rows)
                if not connection.in_transaction:
                    connection.commit()
        except sqlite3.Error as exc:
            raise DatabaseError("数据库批量写入失败。", detail=f"SQL: {sql} | {exc}") from exc

    def query_all(self, sql: str, params: SqlParams = None) -> list[sqlite3.Row]:
        try:
            return list(self.connection.execute(sql, params or ()).fetchall())
        except sqlite3.Error as exc:
            raise DatabaseError("数据库查询失败。", detail=f"SQL: {sql} | {exc}") from exc

    def query_one(self, sql: str, params: SqlParams = None) -> sqlite3.Row | None:
        try:
            return self.connection.execute(sql, params or ()).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError("数据库查询失败。", detail=f"SQL: {sql} | {exc}") from exc

    def query_scalar(self, sql: str, params: SqlParams = None, default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    # -- 生命周期 ----------------------------------------------------------
    def close_thread_connection(self) -> None:
        """关闭当前线程的连接（工作线程退出前调用）。"""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            return
        try:
            connection.close()
        except sqlite3.Error:  # pragma: no cover - 关闭失败无需处理
            pass
        finally:
            with self._lock:
                if connection in self._connections:
                    self._connections.remove(connection)
            self._local.connection = None

    def dispose(self) -> None:
        """关闭所有连接（程序退出时调用）。"""
        with self._lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:  # pragma: no cover
                continue
        self._local = threading.local()
        self._initialized = False
