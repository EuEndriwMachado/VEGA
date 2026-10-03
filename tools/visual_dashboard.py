"""Simple visual agenda dashboard for VEGA daily support and reminders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover - optional in headless environments.
    tk = None
    ttk = None


def _read_json(path: str | Path, default: Any):
    file_path = Path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default
    return payload


def build_dashboard_snapshot(daily_support_path: str | Path = "data/daily_support.json", scheduler_path: str | Path = "data/scheduler.json") -> dict[str, Any]:
    daily_data = _read_json(daily_support_path, {})
    scheduler_data = _read_json(scheduler_path, [])

    notes = []
    if isinstance(daily_data, dict):
        for note in daily_data.get("notes", []):
            if isinstance(note, dict) and note.get("text"):
                notes.append(note["text"])

    focus = None
    if isinstance(daily_data, dict):
        focus_payload = daily_data.get("focus")
        if isinstance(focus_payload, dict):
            focus = {
                "goal": focus_payload.get("goal", "Sem objetivo"),
                "duration_minutes": focus_payload.get("duration_minutes", 25),
                "steps": [
                    {"text": step.get("text", ""), "done": bool(step.get("done", False))}
                    for step in focus_payload.get("steps", [])
                    if isinstance(step, dict)
                ],
            }

    reminders = []
    if isinstance(scheduler_data, list):
        for item in scheduler_data:
            if not isinstance(item, dict):
                continue
            if item.get("active", True):
                reminders.append({
                    "text": str(item.get("text", "Lembrete")),
                    "due_at": item.get("due_at", "Sem prazo"),
                    "kind": item.get("kind", "reminder"),
                })

    return {
        "notes": notes,
        "focus": focus,
        "reminders": reminders,
    }


def format_dashboard_text(snapshot: dict[str, Any]) -> str:
    notes = snapshot.get("notes") or ["Nenhuma nota rápida hoje."]
    focus = snapshot.get("focus")
    reminders = snapshot.get("reminders") or [{"text": "Nenhum lembrete ativo.", "due_at": "-"}]

    lines = ["VEGA · Agenda do dia", "=" * 24]
    lines.append("Notas:")
    for index, note in enumerate(notes[:5], start=1):
        lines.append(f"{index}. {note}")

    lines.append("")
    if focus:
        lines.append(f"Foco: {focus['goal']} ({focus['duration_minutes']} min)")
        pending_steps = [step for step in focus.get("steps", []) if not step.get("done")]
        if pending_steps:
            lines.append("Próximas etapas:")
            for index, step in enumerate(pending_steps[:3], start=1):
                lines.append(f"{index}. {step['text']}")
        else:
            lines.append("Todas as etapas desta sessão já foram concluídas.")
    else:
        lines.append("Foco: nenhuma sessão ativa no momento.")

    lines.append("")
    lines.append("Lembretes:")
    for reminder in reminders[:5]:
        lines.append(f"- {reminder['text']} [{reminder['kind']}] {reminder['due_at']}")

    return "\n".join(lines)


class VEGAAgendaDashboard:
    def __init__(self, root: Any, daily_support_path: str | Path = "data/daily_support.json", scheduler_path: str | Path = "data/scheduler.json"):
        self.root = root
        self.daily_support_path = daily_support_path
        self.scheduler_path = scheduler_path
        self.root.title("VEGA · Agenda")
        self.root.geometry("720x520")
        self.root.minsize(540, 420)

        self.text_widget = tk.Text(self.root, wrap="word", font=("Segoe UI", 10), padx=12, pady=12)
        self.text_widget.pack(fill="both", expand=True, padx=12, pady=(12, 8))

        refresh_button = ttk.Button(self.root, text="Atualizar", command=self.refresh)
        refresh_button.pack(pady=(0, 12))

        self.refresh()

    def refresh(self):
        snapshot = build_dashboard_snapshot(self.daily_support_path, self.scheduler_path)
        self.text_widget.delete("1.0", "end")
        self.text_widget.insert("1.0", format_dashboard_text(snapshot))


def main():
    if tk is None or ttk is None:
        snapshot = build_dashboard_snapshot()
        print(format_dashboard_text(snapshot))
        return 0

    try:
        root = tk.Tk()
        VEGAAgendaDashboard(root)
        root.mainloop()
        return 0
    except Exception:  # pragma: no cover - GUI fallback for headless execution.
        snapshot = build_dashboard_snapshot()
        print(format_dashboard_text(snapshot))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
