from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from collections import defaultdict

from storyforge.domain.models import EventRecord


class EventBus:
    def __init__(self) -> None:
        self._project_subscribers: dict[str, list[queue.Queue[EventRecord]]] = defaultdict(list)
        self._lock = threading.Lock()

    def publish(self, event: EventRecord) -> EventRecord:
        with self._lock:
            subscribers = list(self._project_subscribers.get(event.project_id, []))
        for subscriber in subscribers:
            subscriber.put(event)
        return event

    def subscribe(self, project_id: str) -> tuple[queue.Queue[EventRecord], Callable[[], None]]:
        channel: queue.Queue[EventRecord] = queue.Queue()
        with self._lock:
            self._project_subscribers[project_id].append(channel)

        def unsubscribe() -> None:
            with self._lock:
                subscribers = self._project_subscribers.get(project_id, [])
                if channel in subscribers:
                    subscribers.remove(channel)
                if not subscribers and project_id in self._project_subscribers:
                    del self._project_subscribers[project_id]

        return channel, unsubscribe
