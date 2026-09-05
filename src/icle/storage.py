"""Thread/process serialization for JSON read-modify-write transactions."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import threading

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def transaction(path: Path):
    path = path.resolve()
    key = str(path)
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, 'held', None)
        if held is None:
            held = _local.held = set()
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a+') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
