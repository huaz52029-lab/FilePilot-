"""自适应并发控制。

目标：在不牺牲稳定性的前提下利用多连接；一旦发现服务器限制、
错误率上升或加速无收益，就自动降低连接数。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.common.constants import (
    ADAPTIVE_DOWNGRADE_DROP,
    ADAPTIVE_ERROR_RATE_LIMIT,
    ADAPTIVE_UPGRADE_GAIN,
    MAX_CONNECTIONS,
    MIN_CONNECTIONS,
)
from app.core.common.logger import get_logger

_log = get_logger("download.adaptive")

# 评估周期（秒）：太快会抖动，太慢则反应迟钝
ADAPTIVE_INTERVAL: float = 8.0


@dataclass(slots=True)
class AdaptiveDecision:
    """一次评估结果。"""

    connections: int
    reason: str
    changed: bool


class AdaptiveController:
    """根据速度增益与失败率调整连接数。"""

    def __init__(
        self,
        initial: int,
        *,
        minimum: int = MIN_CONNECTIONS,
        maximum: int = MAX_CONNECTIONS,
        upgrade_gain: float = ADAPTIVE_UPGRADE_GAIN,
        downgrade_drop: float = ADAPTIVE_DOWNGRADE_DROP,
        error_rate_limit: float = ADAPTIVE_ERROR_RATE_LIMIT,
    ) -> None:
        self.minimum = max(1, minimum)
        self.maximum = max(self.minimum, maximum)
        self.current = max(self.minimum, min(self.maximum, int(initial)))
        self.upgrade_gain = upgrade_gain
        self.downgrade_drop = downgrade_drop
        self.error_rate_limit = error_rate_limit
        self._baseline_speed: float | None = None
        self._last_speed: float | None = None
        self._evaluations = 0

    def evaluate(self, *, speed: float, error_rate: float) -> AdaptiveDecision:
        """评估是否需要调整连接数。

        ``speed`` 为当前窗口内的平均速度（字节/秒），
        ``error_rate`` 为分段重试率（0–1）。
        """
        self._evaluations += 1
        if speed <= 0 and self._last_speed is None:
            self._last_speed = speed
            return AdaptiveDecision(self.current, "等待速度采样", changed=False)

        reason = ""
        target = self.current

        if error_rate > self.error_rate_limit:
            target = max(self.minimum, self.current - 1)
            reason = f"错误率 {error_rate:.0%} 偏高"
        elif (
            self.current > self.minimum
            and self._last_speed
            and speed < self._last_speed * self.downgrade_drop
        ):
            target = max(self.minimum, self.current - 1)
            reason = "速度明显下降"
        elif (
            self.current < self.maximum
            and self._baseline_speed
            and speed > self._baseline_speed * self.upgrade_gain
            and error_rate <= self.error_rate_limit / 2
        ):
            target = min(self.maximum, self.current + 1)
            reason = "速度提升明显，可增加连接"

        changed = target != self.current
        if changed:
            _log.info(
                "自适应并发：%s → %s（%s，速度 %.2f MB/s）",
                self.current,
                target,
                reason,
                speed / 1024 / 1024,
            )
            self.current = target
            self._baseline_speed = speed if speed > 0 else self._baseline_speed
        elif self._baseline_speed is None or speed > self._baseline_speed:
            self._baseline_speed = speed
        self._last_speed = speed
        return AdaptiveDecision(target, reason or "保持当前连接数", changed=changed)

    @property
    def evaluations(self) -> int:
        return self._evaluations
