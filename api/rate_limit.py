import time
from collections.abc import Callable


class FixedWindowLimiter:
    """Ventana fija en memoria. Vale para un unico proceso: al reiniciar se pierden los contadores."""

    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self.windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = self.clock()
        started, count = self.windows.get(key, (now, 0))
        if now - started >= self.window:
            # Una ventana vencida permite limpiar las demas, asi la memoria no crece con cada IP nueva.
            self.windows = {k: v for k, v in self.windows.items() if now - v[0] < self.window}
            started, count = now, 0
        if count >= self.limit:
            return False
        self.windows[key] = (started, count + 1)
        return True
