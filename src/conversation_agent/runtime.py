"""Runtime helpers for process locking."""

from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    """Raised when another agent process appears to be running."""


class SingleInstanceLock:
    def __init__(self, runtime_dir: Path, name: str = "agent") -> None:
        self.runtime_dir = runtime_dir
        self.lock_path = runtime_dir / f"{name}.lock"
        self.pid_path = runtime_dir / f"{name}.pid"
        self._fd: int | None = None

    def acquire(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_lock()
        try:
            self._fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AlreadyRunningError(f"Agent is already running: {self.lock_path}") from exc
        pid = str(os.getpid())
        os.write(self._fd, pid.encode("ascii"))
        self.pid_path.write_text(pid, encoding="ascii")

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        for path in (self.lock_path, self.pid_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()

    def _clear_stale_lock(self) -> None:
        if not self.lock_path.exists():
            return
        pid = _read_pid(self.pid_path) or _read_pid(self.lock_path)
        if pid is not None and _pid_is_running(pid):
            return
        for path in (self.lock_path, self.pid_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _read_pid(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
