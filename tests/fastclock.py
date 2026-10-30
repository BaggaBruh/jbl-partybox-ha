"""Simulated time for tests: when nothing is ready, the clock jumps to the next timer.

Importing this replaces time.monotonic, so the code under test sees the same clock. A deadlock
(nothing to do and no timer left) raises instead of hanging.
"""
import asyncio
import selectors
import time

_now = [1000.0]
time.monotonic = lambda: _now[0]


class _JumpingSelector:
    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def select(self, timeout=None):
        # Besides the loop's own wakeup pipe there may be test sockets; give those a moment
        pause = 0.002 if len(self._real.get_map()) > 1 else 0
        events = self._real.select(pause if timeout is None or timeout > 0 else 0)
        if events or timeout == 0:
            return events
        if timeout is None:
            raise RuntimeError("deadlock: the test is waiting for something that will never happen")
        _now[0] += timeout
        return events


class _Loop(asyncio.SelectorEventLoop):
    def __init__(self):
        super().__init__(_JumpingSelector(selectors.DefaultSelector()))

    def time(self):
        return _now[0]


def run(coro):
    with asyncio.Runner(loop_factory=_Loop) as runner:
        return runner.run(coro)
