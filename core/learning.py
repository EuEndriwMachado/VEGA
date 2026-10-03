from __future__ import annotations

from datetime import datetime


class LearningManager:
    """Keeps a compact history of outcomes so the agent can reuse successful strategies and avoid repeated failures."""

    def __init__(self, max_history: int = 25):
        self.max_history = max_history
        self.history = []

    def record_outcome(self, task: str, success: bool, evidence: str = "", strategy: str = "", result: str = "") -> dict:
        normalized_task = (task or "tarefa").strip()
        normalized_strategy = (strategy or ("manter o fluxo atual" if success else "revisar a causa raiz e validar novamente")).strip()

        entry = {
            "task": normalized_task,
            "success": bool(success),
            "evidence": (evidence or "").strip(),
            "strategy": normalized_strategy,
            "result": (result or "").strip(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        return entry

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join((text or "").lower().split())

    def get_adaptation_summary(self, task: str | None = None, limit: int = 5) -> list[str]:
        target = self._normalize(task)
        relevant = self.history

        if target:
            relevant = [
                item for item in self.history
                if self._normalize(item.get("task", "")) == target
                or target in self._normalize(item.get("task", ""))
                or target in self._normalize(item.get("strategy", ""))
            ]

        if not relevant:
            return []

        summary = []
        for item in reversed(relevant[-limit:]):
            if item.get("success"):
                summary.append(f"Uso recomendado: {item.get('strategy', 'manter o fluxo atual')}")
            else:
                summary.append(f"Revisar: {item.get('strategy', 'revisar a causa raiz e validar novamente')}")

        deduped = []
        seen = set()
        for entry in summary:
            key = entry.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(entry)

        return deduped[:limit]
