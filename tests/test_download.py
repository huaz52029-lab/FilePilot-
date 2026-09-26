"""下载引擎端到端测试（真实 HTTP 请求 + 真实文件写入）。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from app.core.common.exceptions import DownloadProbeError, InsufficientDiskSpaceError
from app.core.download.config import EngineCallbacks, EngineOptions
from app.core.download.engine import DownloadEngine
from app.core.download.models import DownloadMode, DownloadStatus
from app.core.storage.database import Database
from app.core.storage.repositories import DownloadRepository
from tests.http_server import TestHTTPServer, serve

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB 可复现数据
SMALL_PAYLOAD = b"FilePilot download engine test payload\n"


@pytest.fixture()
def http_server():  # type: ignore[no-untyped-def]
    with serve() as server:
        yield server


class EngineHarness:
    """封装“数据库 + 仓储 + 引擎”的测试脚手架。"""

    def __init__(self, tmp_path: Path, **options: object) -> None:
        self.db = Database(tmp_path / "downloads.db")
        self.db.initialize()
        self.repo = DownloadRepository(self.db)
        self.events: list[tuple[str, str]] = []
        callbacks = EngineCallbacks(
            on_task_updated=lambda task: None,
            on_notice=lambda level, message: self.events.append((level, message)),
        )
        self.engine = DownloadEngine(
            repository=self.repo,
            options=EngineOptions(**options),  # type: ignore[arg-type]
            callbacks=callbacks,
        )

    async def __aenter__(self) -> EngineHarness:
        await self.engine.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.engine.shutdown()
        self.db.dispose()

    async def download(
        self,
        url: str,
        save_dir: Path,
        *,
        connections: int | None = None,
        expected_sha256: str = "",
        timeout: float = 30.0,
    ):  # type: ignore[no-untyped-def]
        probe = await self.engine.probe(url, save_dir)
        task = self.engine.create_task(
            probe,
            save_dir=save_dir,
            connections=connections,
            expected_sha256=expected_sha256,
        )
        await self.engine.start_task(task, is_new=True)
        await asyncio.wait_for(self.engine.scheduler.join(), timeout=timeout)
        return self.engine.get_task(task.task_id)


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------
async def test_probe_supports_range(http_server: TestHTTPServer, tmp_path: Path) -> None:
    url = http_server.register(
        "/big.bin",
        PAYLOAD,
        etag='"v1"',
        modified="Wed, 21 Oct 2026 07:28:00 GMT",
    )
    async with EngineHarness(tmp_path) as harness:
        probe = await harness.engine.probe(url, tmp_path)
    assert probe.total_size == len(PAYLOAD)
    assert probe.supports_range is True
    assert probe.can_resume is True
    assert probe.etag == '"v1"'
    assert probe.file_name == "big.bin"
    assert probe.content_type == "application/octet-stream"
    assert probe.suggested_connections >= 1


async def test_probe_without_range(http_server: TestHTTPServer, tmp_path: Path) -> None:
    url = http_server.register("/norange.bin", PAYLOAD, mode="norange")
    async with EngineHarness(tmp_path) as harness:
        probe = await harness.engine.probe(url, tmp_path)
    assert probe.total_size == len(PAYLOAD)
    assert probe.supports_range is False
    assert probe.can_resume is False
    assert probe.suggested_connections == 1
    assert "单连接" in probe.note


async def test_probe_follows_redirect(http_server: TestHTTPServer, tmp_path: Path) -> None:
    http_server.register("/real.bin", SMALL_PAYLOAD)
    redirect = http_server.register_redirect("/go", "/real.bin")
    async with EngineHarness(tmp_path) as harness:
        probe = await harness.engine.probe(redirect, tmp_path)
    assert probe.file_name == "real.bin"
    assert probe.total_size == len(SMALL_PAYLOAD)
    assert probe.final_url.endswith("/real.bin")


async def test_probe_missing_file(http_server: TestHTTPServer, tmp_path: Path) -> None:
    url = http_server.register("/nope.bin", b"", mode="missing")
    async with EngineHarness(tmp_path) as harness:
        with pytest.raises(DownloadProbeError) as info:
            await harness.engine.probe(url, tmp_path)
    assert "404" in info.value.user_message or "不存在" in info.value.user_message


async def test_probe_invalid_url(tmp_path: Path) -> None:
    async with EngineHarness(tmp_path) as harness:
        with pytest.raises(DownloadProbeError):
            await harness.engine.probe("not-a-url", tmp_path)


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------
async def test_small_file_download(http_server: TestHTTPServer, tmp_path: Path) -> None:
    url = http_server.register("/small.txt", SMALL_PAYLOAD)
    async with EngineHarness(tmp_path) as harness:
        task = await harness.download(url, tmp_path)
    assert task is not None
    assert task.status is DownloadStatus.COMPLETED
    assert task.mode in {DownloadMode.SINGLE, DownloadMode.SEGMENTED}
    target = tmp_path / "small.txt"
    assert target.read_bytes() == SMALL_PAYLOAD
    assert task.sha256 == sha256_of(SMALL_PAYLOAD)
    assert task.progress_percent == 100.0
    assert not (tmp_path / "small.txt.fp.part").exists()


async def test_large_file_segmented_download(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    payload = PAYLOAD * 4  # 4 MiB
    url = http_server.register("/large.bin", payload, etag='"abc"')
    async with EngineHarness(tmp_path, connection_mode=4, adaptive=False) as harness:
        task = await harness.download(url, tmp_path, connections=4)
    assert task is not None
    assert task.status is DownloadStatus.COMPLETED
    assert task.mode is DownloadMode.SEGMENTED
    assert task.connection_count == 4
    assert (tmp_path / "large.bin").read_bytes() == payload
    assert task.sha256 == sha256_of(payload)
    # 服务端确实收到了 Range 请求，并且覆盖了整段文件
    ranges = [item for item in http_server.state.ranges_for("/large.bin") if item]
    assert ranges
    assert any(item.startswith("bytes=") for item in ranges)
    assert len(task.segments) >= 1
    assert all(segment.is_complete for segment in task.segments)


async def test_server_ignoring_range_falls_back(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    """服务器声称支持 Range 但忽略 Range 请求时必须自动回退。"""
    url = http_server.register("/lying.bin", PAYLOAD * 2, mode="lying")
    async with EngineHarness(tmp_path, connection_mode=4, adaptive=False) as harness:
        # 直接按分段模式启动（探测阶段会误判为支持 Range）
        from app.core.download.models import ProbeResult

        probe = ProbeResult(
            url=url,
            final_url=url,
            file_name="lying.bin",
            total_size=len(PAYLOAD) * 2,
            supports_range=True,
            can_resume=True,
            etag='"x"',
            suggested_connections=4,
        )
        task = harness.engine.create_task(probe, save_dir=tmp_path, connections=4)
        await harness.engine.start_task(task, is_new=True)
        await asyncio.wait_for(harness.engine.scheduler.join(), timeout=30)
        result = harness.engine.get_task(task.task_id)
    assert result is not None
    assert result.mode is DownloadMode.FALLBACK
    assert result.status is DownloadStatus.COMPLETED
    assert (tmp_path / "lying.bin").read_bytes() == PAYLOAD * 2
    assert any("单连接" in message for _level, message in harness.events)


async def test_retry_after_temporary_failure(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    # 探测正常，但前两次真正的下载请求返回 503，用于验证下载阶段的自动重试
    url = http_server.register("/flaky.bin", PAYLOAD, mode="flaky-download", failures=2)
    async with EngineHarness(tmp_path, adaptive=False) as harness:
        task = await harness.download(url, tmp_path)
    assert task is not None
    assert task.status is DownloadStatus.COMPLETED
    assert task.retry_count >= 1
    assert (tmp_path / "flaky.bin").read_bytes() == PAYLOAD


async def test_unknown_size_stream(http_server: TestHTTPServer, tmp_path: Path) -> None:
    url = http_server.register("/stream.bin", PAYLOAD, mode="unknown")
    async with EngineHarness(tmp_path) as harness:
        task = await harness.download(url, tmp_path)
    assert task is not None
    assert task.status is DownloadStatus.COMPLETED
    assert task.mode is DownloadMode.SINGLE
    assert (tmp_path / "stream.bin").read_bytes() == PAYLOAD


async def test_existing_file_is_renamed(http_server: TestHTTPServer, tmp_path: Path) -> None:
    url = http_server.register("/dup.txt", SMALL_PAYLOAD)
    (tmp_path / "dup.txt").write_bytes(b"old content")
    async with EngineHarness(tmp_path) as harness:
        task = await harness.download(url, tmp_path)
    assert task is not None
    assert task.save_path is not None
    assert task.save_path.name == "dup (1).txt"
    assert (tmp_path / "dup.txt").read_bytes() == b"old content"
    assert task.save_path.read_bytes() == SMALL_PAYLOAD


async def test_hash_verification_success_and_failure(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    url = http_server.register("/hash.bin", PAYLOAD)
    good = sha256_of(PAYLOAD)
    async with EngineHarness(tmp_path) as harness:
        ok_task = await harness.download(url, tmp_path / "ok", expected_sha256=good)
        (tmp_path / "ok").mkdir(exist_ok=True)
        bad_task = await harness.download(
            url, tmp_path / "ok", expected_sha256="0" * 64
        )
    assert ok_task is not None and ok_task.status is DownloadStatus.COMPLETED
    assert ok_task.hash_verified is True
    assert bad_task is not None
    assert bad_task.status is DownloadStatus.FAILED
    assert "校验失败" in bad_task.error_message


async def test_insufficient_disk_space_blocks_task(
    http_server: TestHTTPServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = http_server.register("/huge.bin", PAYLOAD)
    import app.core.download.task as task_module

    def _raise(path: Path | str, required: int, *, margin: int = 0) -> None:
        raise InsufficientDiskSpaceError(required, 1024, path=str(path))

    monkeypatch.setattr(task_module, "ensure_disk_space", _raise)
    async with EngineHarness(tmp_path) as harness:
        task = await harness.download(url, tmp_path)
    assert task is not None
    assert task.status is DownloadStatus.FAILED
    assert "空间不足" in task.error_message
    assert not (tmp_path / "huge.bin").exists()


async def test_pause_and_resume_keeps_progress(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    payload = PAYLOAD * 8  # 8 MiB，慢速发送
    url = http_server.register("/slow.bin", payload, mode="slow", etag='"slow"')
    async with EngineHarness(tmp_path, connection_mode=1, adaptive=False) as harness:
        probe = await harness.engine.probe(url, tmp_path)
        task = harness.engine.create_task(probe, save_dir=tmp_path, connections=1)
        await harness.engine.start_task(task, is_new=True)
        await asyncio.sleep(1.2)
        harness.engine.pause(task.task_id)
        for _ in range(80):
            await asyncio.sleep(0.1)
            current = harness.engine.get_task(task.task_id)
            if current is not None and current.status is DownloadStatus.PAUSED:
                break
        paused = harness.engine.get_task(task.task_id)
        assert paused is not None
        assert paused.status is DownloadStatus.PAUSED
        assert paused.downloaded < len(payload)
        part_dir = tmp_path / "slow.bin.fp.part"
        assert (part_dir / "meta.json").exists()

        resumed = await harness.engine.resume(task.task_id)
        assert resumed is True
        await asyncio.wait_for(harness.engine.scheduler.join(), timeout=60)
        final = harness.engine.get_task(task.task_id)
    assert final is not None
    assert final.status is DownloadStatus.COMPLETED
    assert (tmp_path / "slow.bin").read_bytes() == payload
    # 续传请求确实从非 0 偏移开始
    ranges = [
        item
        for item in http_server.state.ranges_for("/slow.bin")
        if item and not item.startswith("bytes=0-")
    ]
    assert ranges, "续传时应发起非零起点的 Range 请求"


async def test_restart_recovery_reuses_part_file(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    """模拟程序重启：新引擎在同一数据目录上继续未完成任务。"""
    payload = PAYLOAD * 6
    url = http_server.register("/resume.bin", payload, mode="slow", etag='"r1"')
    async with EngineHarness(tmp_path, connection_mode=1, adaptive=False) as harness:
        probe = await harness.engine.probe(url, tmp_path)
        task = harness.engine.create_task(probe, save_dir=tmp_path, connections=1)
        await harness.engine.start_task(task, is_new=True)
        await asyncio.sleep(1.0)
        harness.engine.pause(task.task_id)
        for _ in range(80):
            await asyncio.sleep(0.1)
            current = harness.engine.get_task(task.task_id)
            if current is not None and current.status is DownloadStatus.PAUSED:
                break
        paused_downloaded = harness.engine.get_task(task.task_id).downloaded  # type: ignore[union-attr]
        task_id = task.task_id

    # 新的引擎实例（等价于重启程序）
    async with EngineHarness(tmp_path, connection_mode=1, adaptive=False) as second:
        restored = await second.engine.load_unfinished()
        assert any(item.task_id == task_id for item in restored)
        assert second.engine.get_task(task_id).status is DownloadStatus.PAUSED
        await second.engine.resume(task_id)
        await asyncio.wait_for(second.engine.scheduler.join(), timeout=60)
        final = second.engine.get_task(task_id)
    assert final is not None
    assert final.status is DownloadStatus.COMPLETED
    assert (tmp_path / "resume.bin").read_bytes() == payload
    assert paused_downloaded > 0


async def test_cancel_task_keeps_part_files(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    url = http_server.register("/cancel.bin", PAYLOAD * 8, mode="slow", etag='"c1"')
    async with EngineHarness(tmp_path, connection_mode=1) as harness:
        probe = await harness.engine.probe(url, tmp_path)
        task = harness.engine.create_task(probe, save_dir=tmp_path, connections=1)
        await harness.engine.start_task(task, is_new=True)
        await asyncio.sleep(0.8)
        harness.engine.cancel(task.task_id)
        for _ in range(60):
            await asyncio.sleep(0.1)
            current = harness.engine.get_task(task.task_id)
            if current is not None and current.status is DownloadStatus.CANCELLED:
                break
        cancelled = harness.engine.get_task(task.task_id)
        assert cancelled is not None
        assert cancelled.status is DownloadStatus.CANCELLED
        assert not (tmp_path / "cancel.bin").exists()
        # 删除任务记录时清理临时文件
        await harness.engine.delete_task(task.task_id, delete_files=True)
        assert not (tmp_path / "cancel.bin.fp.part").exists()


async def test_queue_limits_concurrency(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    urls = [
        http_server.register(f"/queue{i}.bin", PAYLOAD, mode="slow", etag=f'"q{i}"')
        for i in range(5)
    ]
    async with EngineHarness(tmp_path, max_concurrent_tasks=2, connection_mode=1) as harness:
        tasks = []
        for url in urls:
            probe = await harness.engine.probe(url, tmp_path)
            task = harness.engine.create_task(probe, save_dir=tmp_path, connections=1)
            await harness.engine.start_task(task, is_new=True)
            tasks.append(task)
        await asyncio.sleep(0.6)
        assert harness.engine.scheduler.active_count <= 2
        await asyncio.wait_for(harness.engine.scheduler.join(), timeout=90)
    for index in range(5):
        target = tmp_path / f"queue{index}.bin"
        assert target.exists()
        assert target.read_bytes() == PAYLOAD


async def test_delete_task_removes_database_row(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    url = http_server.register("/del.txt", SMALL_PAYLOAD)
    async with EngineHarness(tmp_path) as harness:
        task = await harness.download(url, tmp_path)
        assert task is not None
        assert harness.repo.get_task(task.task_id) is not None
        await harness.engine.delete_task(task.task_id, delete_files=False)
        assert harness.repo.get_task(task.task_id) is None
        assert (tmp_path / "del.txt").exists()


async def test_bad_content_range_is_reported(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    """服务器返回错误的分段位置时不允许写入错位数据。"""
    from app.core.download.models import ProbeResult

    url = http_server.register("/bad.bin", PAYLOAD * 2, mode="badrange", etag='"b1"')
    async with EngineHarness(tmp_path, connection_mode=2, adaptive=False) as harness:
        probe = ProbeResult(
            url=url,
            final_url=url,
            file_name="bad.bin",
            total_size=len(PAYLOAD) * 2,
            supports_range=True,
            can_resume=True,
            etag='"b1"',
            suggested_connections=2,
        )
        task = harness.engine.create_task(probe, save_dir=tmp_path, connections=2)
        await harness.engine.start_task(task, is_new=True)
        await asyncio.wait_for(harness.engine.scheduler.join(), timeout=60)
        result = harness.engine.get_task(task.task_id)
    assert result is not None
    assert result.status is DownloadStatus.FAILED
    assert result.error_message
    # 没有产生损坏的成品文件
    assert not (tmp_path / "bad.bin").exists()


@pytest.mark.network
async def test_https_probe_real_site(tmp_path: Path) -> None:
    """真实 HTTPS 探测（默认跳过：需要外网）。"""
    pytest.skip("需要外网访问，默认跳过；可用 FILEPILOT_NETWORK_TESTS=1 手动运行")


# ---------------------------------------------------------------------------
# 临时签名地址失效（GitHub Release 618 / jwt:expired）
# ---------------------------------------------------------------------------
async def test_signed_url_expiry_refreshes_and_resumes(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    """临时下载地址失效后，必须重新向原始 URL 取新地址并断点续传。"""
    payload = bytes((index * 13) % 251 for index in range(2 * 1024 * 1024))
    url = http_server.register_signed("/release.bin", payload, token_ttl=2, etag='"signed"')

    async with EngineHarness(tmp_path, connection_mode=4, adaptive=False) as harness:
        probe = await harness.engine.probe(url, tmp_path)
        assert probe.final_url != url, "原始 URL 应重定向到带令牌的临时地址"
        assert "token=" in probe.final_url
        task = harness.engine.create_task(probe, save_dir=tmp_path, connections=4)
        await harness.engine.start_task(task, is_new=True)
        await asyncio.wait_for(harness.engine.scheduler.join(), timeout=120)
        result = harness.engine.get_task(task.task_id)
        notices = list(harness.events)
        stored = harness.repo.get_task(task.task_id)

    assert result is not None
    assert result.status is DownloadStatus.COMPLETED, result.error_message
    target = tmp_path / "release.bin"
    assert target.read_bytes() == payload, "续传后的文件内容必须完整"
    assert result.sha256 == sha256_of(payload)

    # 任务始终保存**原始 URL**，不会被临时签名地址覆盖
    assert result.url == url
    assert stored is not None and stored.url == url

    # 提示与日志：确实做过“重新建立连接”
    assert any("重新建立连接" in message for _level, message in notices), notices

    # 刷新后从断点继续：应出现非 0 起点的 Range 请求
    ranges = [item for item in http_server.state.ranges_for("/release.bin") if item]
    assert ranges, "应至少发起过一次 Range 请求"
    assert any(not item.replace(" ", "").startswith("bytes=0-") for item in ranges), (
        "刷新地址后应从中断偏移继续下载，而不是从头开始"
    )
    # 原始 URL 被重新请求过（首次探测 + 至少一次刷新）
    assert http_server.state.signed_redirects >= 2


# ---------------------------------------------------------------------------
# 暂停任务的持久化与重启行为
# ---------------------------------------------------------------------------
async def test_paused_task_survives_engine_restart(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    """用户暂停的任务在“重启”后必须仍然是暂停状态（不会被自动恢复）。"""
    payload = PAYLOAD * 6
    url = http_server.register("/pause-restart.bin", payload, mode="slow", etag='"p1"')

    async with EngineHarness(tmp_path, connection_mode=1, adaptive=False) as harness:
        probe = await harness.engine.probe(url, tmp_path)
        task = harness.engine.create_task(probe, save_dir=tmp_path, connections=1)
        await harness.engine.start_task(task, is_new=True)
        for _ in range(150):
            await asyncio.sleep(0.1)
            current = harness.engine.get_task(task.task_id)
            if current is not None and current.downloaded > 0:
                break
        harness.engine.pause(task.task_id)
        for _ in range(100):
            await asyncio.sleep(0.1)
            current = harness.engine.get_task(task.task_id)
            if current is not None and current.status is DownloadStatus.PAUSED:
                break
        paused = harness.engine.get_task(task.task_id)
        assert paused is not None and paused.status is DownloadStatus.PAUSED
        paused_bytes = paused.downloaded
        assert paused_bytes > 0
        stored = harness.repo.get_task(task.task_id)
        assert stored is not None and stored.status is DownloadStatus.PAUSED, "暂停状态必须持久化"
        task_id = task.task_id

    async with EngineHarness(tmp_path, connection_mode=1, adaptive=False) as second:
        restored = await second.engine.load_unfinished()
        entry = second.engine.get_task(task_id)
        assert entry is not None
        assert entry.status is DownloadStatus.PAUSED, "重启后暂停任务必须仍然存在且为已暂停"
        assert entry.interrupted is False, "用户主动暂停的任务不应被标记为可自动恢复"
        assert any(item.task_id == task_id for item in restored)

        assert await second.engine.resume(task_id) is True
        await asyncio.wait_for(second.engine.scheduler.join(), timeout=120)
        final = second.engine.get_task(task_id)

    assert final is not None and final.status is DownloadStatus.COMPLETED
    assert (tmp_path / "pause-restart.bin").read_bytes() == payload
    assert final.downloaded == len(payload)


async def test_interrupted_download_is_flagged_for_auto_resume(
    http_server: TestHTTPServer, tmp_path: Path
) -> None:
    """程序退出时仍在下载的任务，重启后应被标记为“被中断”，可供自动恢复。"""
    payload = PAYLOAD * 6
    url = http_server.register("/crash.bin", payload, mode="slow", etag='"c1"')

    first = EngineHarness(tmp_path, connection_mode=1, adaptive=False)
    await first.engine.start()
    try:
        probe = await first.engine.probe(url, tmp_path)
        task = first.engine.create_task(probe, save_dir=tmp_path, connections=1)
        await first.engine.start_task(task, is_new=True)
        for _ in range(200):
            await asyncio.sleep(0.1)
            stored = first.repo.get_task(task.task_id)
            if (
                stored is not None
                and stored.status is DownloadStatus.DOWNLOADING
                and stored.downloaded > 0
            ):
                break
        else:  # pragma: no cover - 依赖后台持久化时序
            raise AssertionError("未在预期时间内观察到“下载中”状态的持久化")

        # 模拟程序被直接关闭：另一个引擎实例读取数据库
        async with EngineHarness(tmp_path, connection_mode=1, adaptive=False) as second:
            restored = await second.engine.load_unfinished()
            entry = second.engine.get_task(task.task_id)
            assert entry is not None
            assert entry.status is DownloadStatus.PAUSED
            assert entry.interrupted is True, "被中断的任务应标记为可自动恢复"
            assert any(item.task_id == task.task_id for item in restored)
    finally:
        await first.engine.shutdown()
        first.db.dispose()
