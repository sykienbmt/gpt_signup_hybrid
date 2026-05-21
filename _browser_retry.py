"""Shared utilities cho browser launch retry — dùng bởi browser_phase + session_phase.

Lý do tách module: lỗi `Page.goto: Connection closed while reading from the driver`
xảy ra ở cả 2 phase (signup + get_session), pattern xử lý giống nhau:
  - Detect lỗi driver pipe đóng sớm (transient)
  - Retry launch với profile sạch
  - Fail-fast nếu lỗi non-transient hoặc đã pass mốc check-point quan trọng

Không phụ thuộc playwright/camoufox — chỉ phân tích error message.
"""
from __future__ import annotations


# Số lần thử lại launch khi driver pipe đóng sớm.
LAUNCH_RETRY_MAX = 2

# Backoff giữa các retry (seconds).
LAUNCH_RETRY_BACKOFF = 2.0

# Patterns nhận biết lỗi driver pipe / browser process chết.
# Đây là lỗi transient — retry launch sạch lại profile thường thoát.
DRIVER_DEAD_MARKERS: tuple[str, ...] = (
    "Connection closed while reading from the driver",
    "Target page, context or browser has been closed",
    "Browser closed",
    "Browser has been closed",
    "Target closed",
    "Transport closed",
    "Page closed",
    "BrowserContext has been closed",
    "has been closed",
)


def is_driver_dead_error(exc: BaseException | None) -> bool:
    """Return True nếu exc là lỗi driver/browser pipe chết (transient)."""
    if exc is None:
        return False
    msg = str(exc)
    return any(marker in msg for marker in DRIVER_DEAD_MARKERS)
