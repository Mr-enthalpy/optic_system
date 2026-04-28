from __future__ import annotations
from threading import Lock
from typing import Callable

from .events import Event


EventHandler = Callable[[Event], None]


class EventBus:
    def __init__(self):
        self._lock = Lock()
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        with self._lock:
            self._handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        with self._lock:
            self._handlers = [h for h in self._handlers if h is not handler]

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = list(self._handlers)
        for h in handlers:
            try:
                h(event)
            except Exception:
                pass