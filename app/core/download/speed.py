"""下载速度统计（滑动窗口）。

速度不能因为单次数据包波动而剧烈跳动，因此使用固定时间窗内的平均增量。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.common.constants import SPEED_WINDOW_SECONDS
from app.core.download.models import SpeedSnapshot


@dataclass(slots=True)
class _Sample:
    """一次采样：时间戳与累计字节数。"""

    timestamp: float
    total_bytes: int


@dataclass(slots=True)
class SpeedMeter:
    """滑动窗口速度计量器。

    用法::

        meter = SpeedMeter()
        meter.reset(0)
        speed = meter.update(downloaded_bytes)
    """

    window_seconds: float = SPEED_WINDOW_SECONDS
    clock: Callable[[], float] = time.monotonic
    _samples: deque[_Sample] = field(default_factory=deque)
    _start_time: float = 0.0
    _start_bytes: int = 0
    _last_total: int = 0
    _peak: float = 0.0

    def reset(self, total_bytes: int = 0) -> None:
        """重置计量器（新任务或续传开始时调用）。"""
        now = self.clock()
        self._samples.clear()
        self._samples.append(_Sample(now, total_bytes))
        self._start_time = now
        self._start_bytes = total_bytes
        self._last_total = total_bytes
        self._peak = 0.0

    def update(self, total_bytes: int) -> float:
        """记录一次进度，返回当前速度（字节/秒）。"""
        now = self.clock()
        if not self._samples:
            self.reset(total_bytes)
            return 0.0
        if total_bytes < self._last_total:
            # 任务被重置（重新下载）——重新开始统计
            self.reset(total_bytes)
            return 0.0

        self._last_total = total_bytes
        self._samples.append(_Sample(now, total_bytes))
        cutoff = now - self.window_seconds
        while len(self._samples) > 2 and self._samples[0].timestamp < cutoff:
            self._samples.popleft()

        oldest = self._samples[0]
        elapsed = max(now - oldest.timestamp, 1e-3)
        current = max(total_bytes - oldest.total_bytes, 0) / elapsed
        self._peak = max(self._peak, current)
        return current

    @property
    def current(self) -> float:
        """最近一次计算出的瞬时速度。"""
        if len(self._samples) < 2:
            return 0.0
        newest = self._samples[-1]
        oldest = self._samples[0]
        elapsed = max(newest.timestamp - oldest.timestamp, 1e-3)
        return max(newest.total_bytes - oldest.total_bytes, 0) / elapsed

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def average(self) -> float:
        """任务开始至今的平均速度。"""
        if not self._samples:
            return 0.0
        elapsed = max(self.clock() - self._start_time, 1e-3)
        return max(self._last_total - self._start_bytes, 0) / elapsed

    def snapshot(self) -> SpeedSnapshot:
        """返回可直接放入 ``DownloadTask`` 的快照对象。"""
        return SpeedSnapshot(current=self.current, average=self.average, peak=self.peak)
