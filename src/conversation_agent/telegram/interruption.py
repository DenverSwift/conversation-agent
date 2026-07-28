"""Generation epochs used to invalidate work superseded by new messages."""

from __future__ import annotations


class InterruptionController:
    def __init__(self) -> None:
        self._epochs: dict[str, int] = {}

    def interrupt(self, contact_id: str) -> int:
        epoch = self._epochs.get(contact_id, 0) + 1
        self._epochs[contact_id] = epoch
        return epoch

    def current(self, contact_id: str) -> int:
        return self._epochs.get(contact_id, 0)

    def is_stale(self, contact_id: str, epoch: int) -> bool:
        return self.current(contact_id) != epoch
