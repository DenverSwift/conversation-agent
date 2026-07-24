from __future__ import annotations

import pytest

from conversation_agent.runtime import AlreadyRunningError, SingleInstanceLock


def test_second_instance_does_not_start(tmp_path) -> None:
    first = SingleInstanceLock(tmp_path)
    second = SingleInstanceLock(tmp_path)

    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()
