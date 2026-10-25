"""Engine clocks. spec/03 §2, spec/00 §3.2. The hub owns the clock; the engine only reads it."""

from __future__ import annotations

import asyncio
import time

from ember.contracts import ClockState


def sim_now_ms(c: ClockState, wall_ms: int) -> int:
    """00 §3.2: clamp(anchor_sim + (playing ? (wall - anchor_wall) * speed : 0), min, max)."""
    elapsed = (wall_ms - c.anchor_wall_ms) * c.speed if c.playing else 0
    return int(min(max(c.anchor_sim_ms + elapsed, c.min_sim_ms), c.max_sim_ms))


def _wake_fields(c: ClockState | None) -> tuple | None:
    return None if c is None else (c.epoch, c.playing, c.speed, c.active_branch_id)


class SimClock:
    """Hub-anchored sim clock. The poller calls update(); the loop awaits wait_until_sim()."""

    def __init__(self, state: ClockState | None = None) -> None:
        self.state = state
        self._event = asyncio.Event()

    def update(self, state: ClockState) -> None:
        changed = _wake_fields(state) != _wake_fields(self.state)
        self.state = state
        if changed:
            self.wake()

    def sim_now(self) -> int:
        # ponytail: no state yet means "before everything"; the poller fills it within 500 ms.
        if self.state is None:
            return 0
        return sim_now_ms(self.state, int(time.time() * 1000))

    def wake(self) -> None:
        self._event.set()

    async def wait_until_sim(self, t_ms: int) -> bool:
        """True once sim_now >= t_ms; False if woken early (seek, reset, branch, play/pause, speed)."""
        self._event.clear()
        while True:
            now = self.sim_now()
            if now >= t_ms:
                return True
            s = self.state
            if s is None or not s.playing or s.speed <= 0:
                timeout = 1.0
            else:
                timeout = min(0.25, (t_ms - now) / s.speed / 1000)
            try:
                await asyncio.wait_for(self._event.wait(), timeout)
                return False
            except TimeoutError:
                pass


class FastClock:
    """Precompute: everything is already 'now'."""

    def sim_now(self) -> int:
        return 2**53

    async def wait_until_sim(self, t_ms: int) -> bool:
        return True
