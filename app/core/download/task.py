"""单个下载任务的执行器。

职责：

* 探测（如需要）并校验服务器信息；
* 断点续传：读取/写入 ``*.fp.part/meta.json``；
* 选择单连接或多连接分段，并在 Range 不可用时自动回退；
* 实时统计速度 / ETA 并节流通知界面；
* 完成后计算 SHA-256 并原子归位文件。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiohttp

from app.core.common.constants import (
    MAX_CONNECTIONS,
    PROGRESS_EMIT_INTERVAL,
)
from app.core.common.exceptions import (
    DownloadError,
    DownloadResumeError,
    FilePilotError,
    HashMismatchError,
    to_user_message,
)
from app.core.common.helpers import unique_path
from app.core.common.logger import get_logger
from app.core.download.adaptive import ADAPTIVE_INTERVAL, AdaptiveController
from app.core.download.config import EngineCallbacks, EngineOptions
from app.core.download.control import (
    CancelledInterrupt,
    DownloadControl,
    PausedInterrupt,
    RangeUnsupported,
)
from app.core.download.models import (
    DownloadMode,
    DownloadSegment,
    DownloadStatus,
    DownloadTask,
    ProbeResult,
    SegmentState,
)
from app.core.download.probe import suggest_connections
from app.core.download.resume import PartMeta, PartStore
from app.core.download.retry import RetryPolicy
from app.core.download.segment import SegmentAllocator, SegmentWorker, cancel_pending
from app.core.download.session import build_headers
from app.core.download.speed import SpeedMeter
from app.core.files.hasher import compute_sha256
from app.core.files.scanner import ensure_disk_space
from app.core.storage.repositories import DownloadRepository

_log = get_logger("download.task")

#: 断点续传元数据写入间隔（秒）
_PERSIST_INTERVAL: float = 2.0


class TaskRunner:
    """执行一个下载任务直到结束（完成 / 失败 / 暂停 / 取消）。"""

    def __init__(
        self,
        *,
        task: DownloadTask,
        session: aiohttp.ClientSession,
        repository: DownloadRepository,
        options: EngineOptions,
        callbacks: EngineCallbacks,
        control: DownloadControl,
        probe: ProbeResult | None = None,
    ) -> None:
        self._task = task
        self._session = session
        self._repo = repository
        self._options = options
        self._callbacks = callbacks
        self._control = control
        self._probe = probe
        self._store: PartStore | None = None
        self._allocator: SegmentAllocator | None = None
        self._worker: SegmentWorker | None = None
        self._meter = SpeedMeter()
        self._ticker: asyncio.Task[None] | None = None
        self._last_persist = 0.0
        self._retry = RetryPolicy()
        self._pool: list[asyncio.Task[None]] = []
        self._active_workers = 1

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    async def run(self) -> DownloadTask:
        """执行任务；无论成功或失败都返回最终状态的任务对象。"""
        task = self._task
        try:
            probe = self._probe or await self._probe_url()
            self._probe = probe
            self._apply_probe(probe)
            await self._prepare(probe)
            self._meter.reset(task.downloaded)
            self._start_ticker()
            await self._download()
            await self._finalize()
        except PausedInterrupt:
            self._sync_progress()
            self._set_status(DownloadStatus.PAUSED, notice="已暂停，可随时继续（支持断点续传）")
            await self._persist(force=True)
        except CancelledInterrupt:
            self._sync_progress()
            self._set_status(
                DownloadStatus.CANCELLED, notice="任务已取消，可重试继续（临时文件已保留）"
            )
            await self._persist(force=True)
        except FilePilotError as exc:
            await self._handle_failure(exc)
        except asyncio.CancelledError:
            self._set_status(DownloadStatus.CANCELLED)
            await self._persist(force=True)
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都要落库
            _log.exception("任务 %s 出现未预期错误", task.task_id)
            await self._handle_failure(exc)
        finally:
            await self._stop_ticker()
            self._callbacks.task_updated(task.snapshot())
        return task

    # ------------------------------------------------------------------
    # 探测与准备
    # ------------------------------------------------------------------
    async def _probe_url(self) -> ProbeResult:
        from app.core.download.probe import probe_url

        return await probe_url(
            self._session,
            self._task.url,
            requested_connections=self._task.requested_connections,
        )

    def _apply_probe(self, probe: ProbeResult) -> None:
        task = self._task
        task.probe = probe
        task.final_url = probe.final_url or task.url
        task.content_type = probe.content_type
        task.etag = probe.etag
        task.last_modified = probe.last_modified
        task.supports_range = probe.supports_range
        task.can_resume = probe.can_resume
        if probe.total_size > 0:
            task.total_size = probe.total_size
        if probe.file_name and (not task.file_name or task.file_name == "download.bin"):
            task.file_name = probe.file_name
        if probe.note:
            task.notice = probe.note

    def _resolve_connections(self, probe: ProbeResult) -> int:
        if not probe.supports_range or probe.total_size <= 0:
            return 1
        recommended = suggest_connections(probe.total_size, supports_range=True, requested=0)
        desired = self._options.connections_for(recommended)
        return max(1, min(MAX_CONNECTIONS, desired))

    async def _prepare(self, probe: ProbeResult) -> None:
        task = self._task
        target = Path(task.save_dir) / task.file_name
        if target.exists():
            target = unique_path(target)
        task.save_path = target

        if task.total_size > 0:
            ensure_disk_space(task.save_dir, task.total_size)

        store = PartStore(target, task.task_id)
        self._store = store

        connections = self._resolve_connections(probe)
        segments: list[DownloadSegment] = []
        meta = store.load_meta()
        if meta is not None and task.total_size > 0:
            try:
                store.validate_resume(meta, probe)
            except DownloadResumeError as exc:
                _log.warning("断点续传校验失败，将重新下载：%s", exc.detail)
                store.cleanup()
                meta = None
            else:
                restored = meta.to_segments()
                if restored and sum(segment.length for segment in restored) == task.total_size:
                    segments = restored
                    remaining = max(task.total_size - sum(s.written for s in restored), 0)
                    task.notice = f"已恢复断点续传，继续下载剩余 {remaining} 字节"

        if not segments:
            segments = (
                DownloadSegment.split(task.total_size, connections)
                if task.total_size > 0
                else []
            )
        if not segments:
            connections = 1

        task.connection_count = connections if len(segments) > 1 else 1
        self._active_workers = task.connection_count
        task.mode = DownloadMode.SEGMENTED if task.connection_count > 1 else DownloadMode.SINGLE
        task.segments = list(segments)
        task.clamp_downloaded()

        store.prepare(task.total_size)
        self._allocator = SegmentAllocator(segments) if segments else None
        self._worker = SegmentWorker(
            session=self._session,
            url=task.final_url or task.url,
            data_path=store.data_path,
            control=self._control,
            retry=self._retry,
            base_headers=build_headers(),
            on_retry=self._on_retry,
            use_range=bool(task.supports_range),
        )
        await self._persist(force=True)
        self._emit()

    # ------------------------------------------------------------------
    # 下载阶段
    # ------------------------------------------------------------------
    async def _download(self) -> None:
        task = self._task
        if task.total_size <= 0:
            await self._download_unknown_size()
            return
        if task.connection_count > 1 and task.supports_range:
            try:
                await self._run_worker_pool()
                return
            except RangeUnsupported as exc:
                await self._fallback_to_single(str(exc))
        await self._download_single()

    async def _run_worker_pool(self) -> None:
        """多连接分段下载：worker 从分配器领取分段。"""
        allocator = self._allocator
        assert allocator is not None
        self._pool = []
        for _ in range(max(1, self._active_workers)):
            self._spawn_worker()

        monitor: asyncio.Task[None] | None = None
        if self._options.adaptive and self._active_workers > 1:
            monitor = asyncio.create_task(self._adaptive_loop(), name="adaptive")
        try:
            while True:
                active = [job for job in self._pool if not job.done()]
                if not active:
                    break
                done, _pending = await asyncio.wait(
                    active, return_when=asyncio.FIRST_COMPLETED
                )
                for job in done:
                    if job.cancelled():
                        continue
                    error = job.exception()
                    if error is not None:
                        raise error
        finally:
            if monitor is not None:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            await cancel_pending(self._pool)

    def _spawn_worker(self) -> None:
        self._pool.append(asyncio.create_task(self._worker_loop()))
        self._active_workers = len([job for job in self._pool if not job.done()])

    async def _worker_loop(self) -> None:
        allocator = self._allocator
        worker = self._worker
        assert allocator is not None and worker is not None
        while True:
            self._control.check()
            chunk = allocator.next_chunk()
            if chunk is None:
                return
            try:
                await worker.run(chunk)
            except asyncio.CancelledError:
                allocator.release_chunk(chunk)
                raise
            except BaseException:
                allocator.release_chunk(chunk)
                raise
            else:
                allocator.release_chunk(chunk, done=True)

    async def _adaptive_loop(self) -> None:
        """根据速度与错误率动态增减连接数。"""
        allocator = self._allocator
        assert allocator is not None
        controller = AdaptiveController(self._active_workers)
        try:
            while True:
                await asyncio.sleep(ADAPTIVE_INTERVAL)
                self._control.check()
                if allocator.is_finished:
                    return
                attempts = sum(chunk.retry_count for chunk in allocator.chunks)
                finished = max(1, allocator.completed + allocator.running)
                error_rate = min(1.0, attempts / finished)
                decision = controller.evaluate(
                    speed=self._meter.current, error_rate=error_rate
                )
                if not decision.changed:
                    continue
                task = self._task
                task.connection_count = decision.connections
                task.notice = (
                    f"自适应并发：{decision.reason}，当前 {decision.connections} 个连接"
                )
                self._active_workers = decision.connections
                self._emit()
                active = [job for job in self._pool if not job.done()]
                if decision.connections > len(active):
                    allocator.split_largest()
                    self._spawn_worker()
                elif decision.connections < len(active) and len(active) > 1:
                    victim = active[-1]
                    victim.cancel()
                    self._pool.remove(victim)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 自适应失败不影响下载
            _log.warning("自适应并发停止：%s", exc)

    async def _download_single(self) -> None:
        """单连接下载（支持从已下载偏移继续）。"""
        task = self._task
        worker = self._worker
        store = self._store
        assert worker is not None and store is not None
        total = task.total_size
        start = task.downloaded if task.supports_range else 0
        if start >= total:
            task.downloaded = total
            return
        # 单连接也使用 start=0 的完整区间：current 表示续传起点，
        # 这样 progress 统计不会丢失已下载的前半段。
        chunk = DownloadSegment(index=0, start=0, end=total - 1, current=start)
        task.segments = [chunk]
        task.connection_count = 1
        self._active_workers = 1
        self._allocator = SegmentAllocator([chunk])
        if task.mode is not DownloadMode.FALLBACK:
            task.mode = DownloadMode.SINGLE
        self._emit()
        try:
            await worker.run(chunk)
        except RangeUnsupported:
            # 服务器忽略 Range：从头顺序重下，并关闭续传能力
            _log.info("任务 %s：服务器忽略 Range，回退为纯单连接模式", task.task_id)
            await self._fallback_to_single("服务器忽略了 Range 请求")
            task.downloaded = 0
            chunk.start = 0
            chunk.current = 0
            chunk.state = SegmentState.PENDING
            self._allocator = SegmentAllocator([chunk])
            worker.use_range = False
            self._emit()
            await worker.run(chunk)

    async def _download_unknown_size(self) -> None:
        """服务器未提供 Content-Length：流式下载。"""
        task = self._task
        worker = self._worker
        assert worker is not None
        task.mode = DownloadMode.SINGLE
        task.connection_count = 1
        self._active_workers = 1
        chunk = DownloadSegment(index=0, start=0, end=-1, current=0)
        self._allocator = SegmentAllocator([chunk])
        task.segments = [chunk]
        self._emit()
        written = await worker.stream_unknown_size(start=0)
        task.downloaded = written
        chunk.current = written
        chunk.state = SegmentState.COMPLETED

    async def _fallback_to_single(self, reason: str) -> None:
        """多连接失败后回退单连接（重新顺序下载完整内容）。"""
        task = self._task
        _log.info("任务 %s：Range 分段不可用，回退单连接（%s）", task.task_id, reason)
        task.mode = DownloadMode.FALLBACK
        task.supports_range = False
        task.can_resume = False
        task.connection_count = 1
        task.downloaded = 0
        task.notice = "检测到服务器不支持分段下载，已自动切换到稳定单连接模式。"
        self._callbacks.notice("warning", f"{task.file_name}：已自动切换到单连接下载模式。")
        allocator = self._allocator
        if allocator is not None:
            for chunk in allocator.chunks:
                chunk.current = chunk.start
                chunk.state = SegmentState.PENDING
        worker = self._worker
        if worker is not None:
            worker.use_range = False
        self._emit()

    def _on_retry(
        self, chunk: DownloadSegment, exc: BaseException, delay: float, attempt: int
    ) -> None:
        """分段重试回调：更新状态与界面提示。"""
        task = self._task
        task.retry_count += 1
        task.status = DownloadStatus.RETRYING
        message = (
            f"分段 {chunk.index + 1} 连接中断，正在重试"
            f"（第 {attempt} 次，{delay:.0f} 秒后）"
        )
        task.notice = message
        _log.warning("任务 %s %s：%s", task.task_id, message, exc)
        self._emit()

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------
    async def _finalize(self) -> None:
        """下载完成：原子归位文件、计算 SHA-256、写入完成状态。"""
        task = self._task
        store = self._store
        assert store is not None
        self._sync_progress()
        if task.total_size > 0 and task.downloaded < task.total_size:
            raise DownloadError("下载数据不完整，请重试。")

        path = store.finalize()
        # 归位后不再写 meta.json，否则会重新创建临时目录
        self._store = None
        task.save_path = path
        if task.total_size > 0:
            task.downloaded = task.total_size

        if self._options.auto_verify:
            task.notice = "正在校验 SHA-256…"
            self._emit()
            digest = await asyncio.to_thread(compute_sha256, path)
            task.sha256 = digest
            if task.expected_sha256 and not task.hash_verified:
                mismatch = HashMismatchError(task.expected_sha256, digest)
                task.status = DownloadStatus.FAILED
                task.error_message = mismatch.user_message
                task.notice = "文件已保存，但 SHA-256 与预期值不一致。"
                await self._persist(force=True)
                self._emit()
                self._callbacks.notice("error", f"{task.file_name} 校验失败")
                self._callbacks.task_finished(task)
                return

        task.status = DownloadStatus.COMPLETED
        task.completed_at = time.time()
        task.error_message = ""
        task.notice = "下载完成"
        task.speed.current = 0.0
        await self._persist(force=True)
        self._emit()
        _log.info("任务 %s 下载完成：%s", task.task_id, path)
        self._callbacks.task_finished(task)

    async def _handle_failure(self, exc: BaseException) -> None:
        """统一失败处理：写入用户可读提示并通知界面。"""
        task = self._task
        message = to_user_message(exc)
        if isinstance(exc, HashMismatchError):
            message = exc.user_message
        task.status = DownloadStatus.FAILED
        task.error_message = message
        task.notice = ""
        task.speed.current = 0.0
        if isinstance(exc, FilePilotError):
            _log.error("任务 %s 失败：%s（%s）", task.task_id, message, exc.detail or "")
        else:
            _log.exception("任务 %s 失败：%s", task.task_id, exc)
        await self._persist(force=True)
        self._emit()
        self._callbacks.notice("error", f"{task.file_name} 下载失败：{message}")
        self._callbacks.task_finished(task)

    def _set_status(self, status: DownloadStatus, *, notice: str = "") -> None:
        self._task.status = status
        if notice:
            self._task.notice = notice
        if status.is_final:
            self._task.speed.current = 0.0

    # ------------------------------------------------------------------
    # 进度与持久化
    # ------------------------------------------------------------------
    def _sync_progress(self) -> None:
        """把分段进度汇总到任务对象，并更新速度 / ETA。"""
        task = self._task
        allocator = self._allocator
        if allocator is not None and allocator.chunks:
            task.segments = allocator.snapshot()
            task.downloaded = allocator.downloaded
        if task.status.is_active:
            self._meter.update(task.downloaded)
            task.speed = self._meter.snapshot()
        task.updated_at = time.time()

    def _start_ticker(self) -> None:
        """启动进度推送循环（节流，避免高频刷新界面）。"""
        self._ticker = asyncio.create_task(self._tick_loop(), name="progress-ticker")

    async def _stop_ticker(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            await asyncio.gather(self._ticker, return_exceptions=True)
            self._ticker = None

    async def _tick_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(PROGRESS_EMIT_INTERVAL)
                self._sync_progress()
                self._emit()
                await self._persist()
        except asyncio.CancelledError:
            raise

    async def _persist(self, *, force: bool = False) -> None:
        """把任务与分段状态写入 SQLite 与 meta.json（节流）。"""
        now = time.monotonic()
        if not force and now - self._last_persist < _PERSIST_INTERVAL:
            return
        self._last_persist = now
        task = self._task
        try:
            # SQLite 写入只有几 KB，放在事件循环内同步执行即可，
            # 既避免线程池开销，也避免跨线程连接管理。
            self._repo.save_task(task)
        except Exception as exc:  # noqa: BLE001 - 持久化失败不应中断下载
            _log.warning("保存任务状态失败：%s", exc)
        store = self._store
        if store is not None and task.total_size > 0:
            try:
                meta = PartMeta.from_task(
                    task_id=task.task_id,
                    url=task.url,
                    final_url=task.final_url,
                    file_name=task.file_name,
                    total_size=task.total_size,
                    etag=task.etag,
                    last_modified=task.last_modified,
                    supports_range=task.supports_range,
                    connection_count=task.connection_count,
                    segments=task.segments,
                )
                store.save_meta(meta)
            except Exception as exc:  # noqa: BLE001
                _log.warning("保存断点续传元数据失败：%s", exc)

    def _emit(self) -> None:
        """向外部推送任务快照（跨线程安全）。"""
        self._callbacks.task_updated(self._task.snapshot())

    @property
    def task(self) -> DownloadTask:
        return self._task
