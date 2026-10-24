import asyncio
import time

from emberline.contracts import ClockState
from emberline.runtime.clock import FastClock, SimClock, sim_now_ms

ANCHOR_SIM = 1_736_301_600_000
ANCHOR_WALL = 1_761_000_000_000


def state(playing: bool, speed: float = 240.0, **kw) -> ClockState:
    base = dict(
        scenario_id="fixture",
        mode="replay",
        playing=playing,
        speed=speed,
        anchor_sim_ms=ANCHOR_SIM,
        anchor_wall_ms=ANCHOR_WALL,
        epoch=0,
        min_sim_ms=ANCHOR_SIM - 3_600_000,
        max_sim_ms=ANCHOR_SIM + 86_400_000,
    )
    base.update(kw)
    return ClockState(**base)


def test_sim_now_ms_formula():
    assert sim_now_ms(state(False), ANCHOR_WALL + 50_000) == ANCHOR_SIM
    assert sim_now_ms(state(True), ANCHOR_WALL + 1000) == ANCHOR_SIM + 240_000
    assert sim_now_ms(state(True), ANCHOR_WALL + 10**9) == ANCHOR_SIM + 86_400_000
    assert sim_now_ms(state(True, speed=-240.0), ANCHOR_WALL + 10**9) == ANCHOR_SIM - 3_600_000
    assert sim_now_ms(state(False, anchor_sim_ms=0), ANCHOR_WALL) == ANCHOR_SIM - 3_600_000


def live_state(speed: float) -> ClockState:
    now = int(time.time() * 1000)
    return state(True, speed=speed, anchor_wall_ms=now)


def test_wait_until_sim_playing_returns_true_quickly():
    clock = SimClock(live_state(900.0))
    start = time.perf_counter()
    assert asyncio.run(clock.wait_until_sim(ANCHOR_SIM + 60_000)) is True
    assert time.perf_counter() - start < 1.0  # 60 s sim at 900x ~ 67 ms wall


def test_wake_returns_false():
    async def run() -> bool:
        clock = SimClock(state(False))
        asyncio.get_running_loop().call_later(0.05, clock.wake)
        return await clock.wait_until_sim(ANCHOR_SIM + 60_000)

    start = time.perf_counter()
    assert asyncio.run(run()) is False
    assert time.perf_counter() - start < 0.9


def test_update_wakes_only_on_relevant_change():
    clock = SimClock(state(False))
    clock.update(state(False, status="computing"))
    assert not clock._event.is_set()
    clock.update(state(True))
    assert clock._event.is_set()


def test_fast_clock():
    assert asyncio.run(FastClock().wait_until_sim(10**15)) is True
    assert FastClock().sim_now() == 2**53
