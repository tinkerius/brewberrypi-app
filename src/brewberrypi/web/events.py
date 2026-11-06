"""Server-sent events: the server pushes state changes to every open event stream (spec 8.4, IF-070..076).

A new stream first gets a `snapshot` with the whole state (IF-071). After that the hub pushes:

- `hardware`, `process`, `recipes`, `cloudSync`: the full payload, only when it changed (IF-072). The hub
  checks every `events.checkMilliseconds`, and at once after every command, so a command's effect reaches all
  clients before its HTTP reply is sent;
- `chart`: one ChartUpdate per chart sample that added a point (IF-073);
- `ping`: every `events.heartbeatMilliseconds`, so clients notice a dead connection (IF-074).

Each stream sends from its own task. Pending state events are coalesced (only the latest payload of a kind
is sent), so a slow browser never delays the others or the engine. Chart updates cannot be coalesced: a
stream that falls too far behind is ended, and the browser reconnects and gets a fresh snapshot (IF-075).
A failure while building or sampling is logged and never stops the hub.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from brewberrypi.clock import Clock

log = logging.getLogger(__name__)

STATE_EVENTS = ("hardware", "process", "recipes", "cloudSync")
COALESCED = (*STATE_EVENTS, "ping")
MAX_BACKLOG = 100
"""Pending events after which a stream is ended (it reconnects and starts over with a snapshot)."""
RETRY_MILLISECONDS = 1000
"""Reconnect delay the browser's EventSource uses after a lost connection (`retry:` field)."""


def frame(event: str, data: Any) -> str:
    """One server-sent event; the data is a single line of JSON."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Subscriber:
    """One open event stream."""

    def __init__(self) -> None:
        self._pending: list[tuple[str, str]] = []
        self._wake = asyncio.Event()
        self.ended = False
        """Set when the stream must close: backlog too long, or the server shuts down."""

    def offer(self, event: str, text: str) -> None:
        if self.ended:
            return
        if event in COALESCED:
            for index, (pending, _) in enumerate(self._pending):
                if pending == event:
                    self._pending[index] = (event, text)
                    self._wake.set()
                    return
        self._pending.append((event, text))
        if len(self._pending) > MAX_BACKLOG:
            log.warning("an event stream fell behind; it is closed and will reconnect")
            self.end()
        self._wake.set()

    def end(self) -> None:
        self.ended = True
        self._pending.clear()
        self._wake.set()

    async def next(self) -> list[str]:
        """Wait for events and return them in order; an empty list means the stream must end."""
        while not self._pending and not self.ended:
            await self._wake.wait()
            self._wake.clear()
        pending, self._pending = self._pending, []
        return [] if self.ended else [text for _, text in pending]


class EventHub:
    def __init__(  # noqa: PLR0913  (each source is injected so tests can drive it)
        self,
        clock: Clock,
        state: dict[str, Callable[[], Any]],
        chart: Callable[[], Any],
        sample_chart: Callable[[], Any],
        *,
        check_seconds: float,
        chart_seconds: float,
        heartbeat_seconds: float,
    ) -> None:
        """`state` builds the payload of each state event; `chart` the whole ChartData; `sample_chart` takes
        one chart sample and returns its ChartUpdate or `None`."""
        self._clock = clock
        self._state = state
        self._chart = chart
        self._sample_chart = sample_chart
        self._check_seconds = check_seconds
        self._chart_seconds = chart_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._subscribers: set[Subscriber] = set()
        self._last: dict[str, str] = {}
        """The last pushed frame of each state event."""
        for event in state:
            text = self._build(event)
            if text is not None:
                self._last[event] = text

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> Subscriber:
        """A new stream with the snapshot queued (IF-071)."""
        subscriber = Subscriber()
        snapshot: dict[str, Any] = {}
        for event, build in self._state.items():
            try:
                snapshot[event] = build()
            except Exception:
                log.exception("building %s for a snapshot failed", event)
        try:
            snapshot["chart"] = self._chart()
        except Exception:
            log.exception("building the chart for a snapshot failed")
        snapshot["heartbeatMilliseconds"] = round(self._heartbeat_seconds * 1000)
        subscriber.offer("snapshot", frame("snapshot", snapshot))
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    def close(self) -> None:
        """End every stream (server shutdown), so open streams do not hold up the shutdown."""
        for subscriber in list(self._subscribers):
            subscriber.end()
        self._subscribers.clear()

    def check(self) -> None:
        """Push every state payload that changed since it was last pushed (IF-072)."""
        for event in self._state:
            text = self._build(event)
            if text is not None and text != self._last.get(event):
                self._last[event] = text
                self._publish(event, text)

    async def run(self) -> None:
        """Run the check, chart and heartbeat loops until cancelled."""
        await asyncio.gather(self._check_loop(), self._chart_loop(), self._heartbeat_loop())

    async def _check_loop(self) -> None:
        while True:
            await self._clock.sleep(self._check_seconds)
            self.check()

    async def _chart_loop(self) -> None:
        while True:
            await self._clock.sleep(self._chart_seconds)
            try:
                update = self._sample_chart()
            except Exception:
                log.exception("taking a chart sample failed")
                continue
            if update is not None:
                self._publish("chart", frame("chart", update))

    async def _heartbeat_loop(self) -> None:
        while True:
            await self._clock.sleep(self._heartbeat_seconds)
            self._publish("ping", frame("ping", {}))

    def _publish(self, event: str, text: str) -> None:
        for subscriber in list(self._subscribers):
            subscriber.offer(event, text)
            if subscriber.ended:
                self._subscribers.discard(subscriber)

    def _build(self, event: str) -> str | None:
        """The event's frame, or `None` (logged) if building fails. The builders always create their dicts in
        the same key order, so equal frames mean equal state."""
        try:
            return frame(event, self._state[event]())
        except Exception:
            log.exception("building %s failed", event)
            return None
