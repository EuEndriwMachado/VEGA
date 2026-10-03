"""Small persistent scheduler for reminders and routines."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable


class VegaScheduler:
    def __init__(self, path="data/scheduler.json", on_due: Callable[[dict], None] | None = None):
        self.path = Path(path)
        self.on_due = on_due
        self._lock = threading.RLock()
        self._items = self._load()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="VegaScheduler", daemon=True)
        self._thread.start()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, text: str, due_at: datetime, repeat_minutes: int | None = None, kind="reminder"):
        item = {"id": f"task_{int(time.time() * 1000)}", "text": text, "due_at": due_at.isoformat(), "repeat_minutes": repeat_minutes, "kind": kind, "active": True}
        with self._lock:
            self._items.append(item)
            self._save()
        return item

    def add_in(self, text: str, minutes: float, repeat_minutes: int | None = None, kind="reminder"):
        return self.add(
            text,
            datetime.now() + timedelta(minutes=float(minutes)),
            repeat_minutes,
            kind=kind,
        )

    def list(self):
        with self._lock:
            return [dict(item) for item in self._items if item.get("active", True)]

    def cancel(self, item_id: str):
        with self._lock:
            for item in self._items:
                if item.get("id") == item_id:
                    item["active"] = False
                    self._save()
                    return True
        return False

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(1.0):
            now = datetime.now()
            due = []
            with self._lock:
                for item in self._items:
                    if not item.get("active", True):
                        continue
                    try:
                        target = datetime.fromisoformat(item["due_at"])
                    except (KeyError, ValueError):
                        item["active"] = False
                        continue
                    if target <= now:
                        due.append(dict(item))
                        repeat = item.get("repeat_minutes")
                        if repeat:
                            item["due_at"] = (now + timedelta(minutes=float(repeat))).isoformat()
                        else:
                            item["active"] = False
                if due:
                    self._save()
            for item in due:
                if self.on_due:
                    try:
                        self.on_due(item)
                    except Exception:
                        pass
