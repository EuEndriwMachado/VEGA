"""Persistent notes and focused work sessions for VEGA."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path


class VegaDailySupport:
    def __init__(self, path="data/daily_support.json"):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        notes = data.get("notes", [])
        data["notes"] = notes if isinstance(notes, list) else []
        focus = data.get("focus")
        data["focus"] = focus if isinstance(focus, dict) else None
        return data

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_note(self, text):
        content = " ".join(str(text or "").split()).strip()
        if not content:
            return None
        note = {
            "id": f"note_{time.time_ns()}",
            "text": content,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        with self._lock:
            self._data["notes"].append(note)
            self._save()
        return dict(note)

    def list_notes(self, limit=10):
        with self._lock:
            notes = self._data["notes"][-max(1, int(limit)):]
            return [dict(note) for note in reversed(notes) if isinstance(note, dict)]

    def current_focus(self):
        with self._lock:
            focus = self._data.get("focus")
            return dict(focus) if isinstance(focus, dict) else None

    def start_focus(self, goal, steps, duration_minutes=25, reminder_id=None):
        goal = " ".join(str(goal or "").split()).strip()
        if not goal:
            raise ValueError("A sessão de foco precisa de um objetivo.")
        duration = min(240, max(5, int(duration_minutes)))
        clean_steps = [
            {"text": " ".join(str(step).split()).strip(), "done": False}
            for step in (steps or [])
            if str(step).strip()
        ][:8]
        focus = {
            "goal": goal,
            "steps": clean_steps,
            "duration_minutes": duration,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reminder_id": reminder_id,
        }
        with self._lock:
            self._data["focus"] = focus
            self._save()
        return dict(focus)

    def complete_next_focus_step(self):
        with self._lock:
            focus = self._data.get("focus")
            if not isinstance(focus, dict):
                return None
            for index, step in enumerate(focus.get("steps", [])):
                if isinstance(step, dict) and not step.get("done"):
                    step["done"] = True
                    self._save()
                    return index, dict(step)
        return None

    def finish_focus(self):
        with self._lock:
            focus = self._data.get("focus")
            if not isinstance(focus, dict):
                return None
            self._data["focus"] = None
            self._save()
            return dict(focus)