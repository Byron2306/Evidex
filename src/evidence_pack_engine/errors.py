from __future__ import annotations

from typing import Callable, TypeVar


class TransientJobError(RuntimeError):
    """An error that is likely temporary (e.g., file lock during Drive sync).

    The watcher should retry the job later instead of moving it to failed/.
    """


def is_transient_file_error(exc: BaseException) -> bool:
    """Return True if the exception looks like a Windows sharing/lock violation."""

    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        # Common transient Windows errors during Drive/OneDrive/AV sync:
        # 32: sharing violation, 33: lock violation, 5: access denied
        # 145: directory not empty (rmtree races / delayed deletes)
        # 2: file not found (can happen with temp/rename races while syncing)
        if winerror in {32, 33, 5, 145, 2}:
            return True
    return False


T = TypeVar("T")


def with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 8,
    base_delay_s: float = 0.5,
    max_delay_s: float = 8.0,
    retry_if: Callable[[BaseException], bool] = is_transient_file_error,
) -> T:
    """Run fn() with retry/backoff for transient failures."""

    import time

    last_exc: BaseException | None = None
    delay = base_delay_s

    for _ in range(max(1, attempts)):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001
            if not retry_if(exc):
                raise
            last_exc = exc
            time.sleep(delay)
            delay = min(max_delay_s, delay * 2)

    raise TransientJobError(str(last_exc) if last_exc else "Transient error")
