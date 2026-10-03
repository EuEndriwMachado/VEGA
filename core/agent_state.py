from __future__ import annotations

import re
import unicodedata
from typing import Any

from core.learning import LearningManager


class AgentStateManager:
    """Tracks the active task, goal, app context and focus session without replacing the current VEGA state systems."""

    FOCUS_RULES = (
        ("estudo", ("estudar", "estudo", "foco", "sessao", "sessão", "trabalho")),
        ("agenda", ("agenda", "organizar", "dia", "compromisso", "lembrete")),
    )

    GOAL_RULES = (
        (
            "powerpoint",
            ("ppt", "powerpoint", "apresentacao", "apresentação", "slide"),
            {"current_app": "powerpoint", "current_object": "apresentação", "current_task": "organizar apresentação", "current_goal": "apresentação"},
        ),
        (
            "study",
            ("fisica", "física", "matematica", "matemática", "quimica", "química", "historia", "biologia"),
            {"current_goal": "física", "current_task": "estudar física"},
        ),
        (
            "daily_plan",
            ("organizar meu dia", "organiza meu dia", "agenda do dia", "resumo do dia"),
            {"current_task": "organizar rotina do dia", "current_goal": "organizar dia"},
        ),
    )

    def __init__(self, context=None):
        self.context = context
        self.current_goal = None
        self.current_task = None
        self.current_app = None
        self.current_object = None
        self.focus_context = "geral"
        self.recent_topics = []
        self.session_context = {}
        self.last_action = None
        self.planned_steps = []
        self.last_verification = {"status": "pending", "summary": "", "next_action": "", "verified": False}
        self.learning_manager = LearningManager()
        self.experience_log = []
        self.last_learning = {"summary": [], "task": None}

    @staticmethod
    def _normalize(text: str) -> str:
        value = (text or "").lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = re.sub(r"[^a-z0-9\s]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    @classmethod
    def _resolve_focus_context(cls, normalized: str) -> str:
        for focus_name, markers in cls.FOCUS_RULES:
            if any(marker in normalized for marker in markers):
                return focus_name
        return "geral"

    @classmethod
    def _resolve_goal_context(cls, normalized: str) -> dict[str, str]:
        for _, markers, payload in cls.GOAL_RULES:
            if any(marker in normalized for marker in markers):
                return dict(payload)

        current_goal = None
        if "fisica" in normalized or "física" in normalized:
            current_goal = "física"
        elif "matematica" in normalized or "matemática" in normalized:
            current_goal = "matemática"
        elif "quimica" in normalized or "química" in normalized:
            current_goal = "química"

        if current_goal:
            return {"current_goal": current_goal, "current_task": f"estudar {current_goal}"}

        return {}

    def _sync_context(self):
        if self.context is None:
            return

        self.context.current_goal = self.current_goal
        self.context.current_task = self.current_task or self.context.current_task
        self.context.current_app = self.current_app or self.context.current_app
        self.context.current_object = self.current_object or self.context.current_object

    def _update_recent_topics(self):
        if self.current_goal is None:
            return

        if self.current_goal not in self.recent_topics:
            self.recent_topics.append(self.current_goal)

        if len(self.recent_topics) > 5:
            self.recent_topics = self.recent_topics[-5:]

    def observe(self, text: str, **context_updates):
        stripped = (text or "").strip()
        if not stripped:
            return self

        normalized = self._normalize(stripped)
        if not normalized:
            return self

        for key, value in context_updates.items():
            if value is not None:
                setattr(self, key, value)

        if self.context is not None and ("atual" in normalized or "organizar" in normalized):
            self.context.current_task = self.context.current_task or "organizar rotina"

        self.focus_context = self._resolve_focus_context(normalized)

        next_context = self._resolve_goal_context(normalized)
        for key, value in next_context.items():
            setattr(self, key, value)

        if self.current_goal is not None and self.current_goal not in self.recent_topics:
            self._update_recent_topics()

        self._sync_context()
        self.last_action = stripped
        return self

    def snapshot(self):
        return {
            "current_goal": self.current_goal,
            "current_task": self.current_task,
            "current_app": self.current_app,
            "current_object": self.current_object,
            "focus_context": self.focus_context,
            "recent_topics": list(self.recent_topics),
            "session_context": dict(self.session_context),
            "last_action": self.last_action,
        }

    def plan_current_goal(self, task: str | None = None):
        target = (task or self.current_goal or self.current_task or self.last_action or "").strip()
        if not target:
            return []

        from core.brain.agent_framework import AgentOrchestrator

        steps = AgentOrchestrator().plan(target)
        self.planned_steps = list(steps)
        self.session_context["plan"] = list(steps)
        self.session_context["goal"] = target
        return list(steps)

    def record_verification(self, task: str | None = None, success: bool = True, evidence: str = "", retries: int = 0):
        target = (task or self.current_goal or self.current_task or self.last_action or "").strip()
        from core.brain.agent_framework import AgentOrchestrator

        outcome = AgentOrchestrator().verify_execution(target, evidence or "", success=success, retries=retries)
        self.last_verification = outcome
        self.session_context["last_verification"] = outcome
        self.session_context["verification_status"] = outcome["status"]
        return outcome

    def record_learning(self, task: str | None = None, success: bool = True, evidence: str = "", strategy: str = "", result: str = ""):
        target = (task or self.current_goal or self.current_task or self.last_action or "").strip()
        if not target:
            return []

        entry = self.learning_manager.record_outcome(target, success=success, evidence=evidence, strategy=strategy, result=result)
        self.experience_log.append(entry)
        self.last_learning = {
            "task": target,
            "summary": self.learning_manager.get_adaptation_summary(target),
            "last_entry": entry,
        }
        self.session_context["learning"] = self.last_learning
        return self.last_learning["summary"]

    def get_adaptation_summary(self, task: str | None = None, limit: int = 5):
        target = (task or self.current_goal or self.current_task or self.last_action or "").strip()
        summary = self.learning_manager.get_adaptation_summary(target, limit=limit)
        self.last_learning = {
            "task": target,
            "summary": summary,
        }
        self.session_context["learning"] = self.last_learning
        return summary

    def render_context_prompt(self) -> str:
        data = self.snapshot()
        parts = []
        if data["current_goal"]:
            parts.append(f"Objetivo atual: {data['current_goal']}")
        if data["current_task"]:
            parts.append(f"Tarefa atual: {data['current_task']}")
        if data["current_app"]:
            parts.append(f"Aplicativo/contexto: {data['current_app']}")
        if data["focus_context"] and data["focus_context"] != "geral":
            parts.append(f"Contexto de foco: {data['focus_context']}")
        if data["recent_topics"]:
            parts.append("Tópicos recentes: " + ", ".join(data["recent_topics"]))

        if not self.planned_steps:
            target = self.current_goal or self.current_task or self.last_action
            if target:
                self.plan_current_goal(target)

        if self.planned_steps:
            parts.append("Plano atual:")
            for index, step in enumerate(self.planned_steps, start=1):
                parts.append(f"{index}. {step}")

        if self.last_verification.get("verified"):
            parts.append(f"Verificação: {self.last_verification['status']} - {self.last_verification['summary']}")
            parts.append(f"Próxima ação: {self.last_verification['next_action']}")

        adaptation_summary = self.get_adaptation_summary()
        if adaptation_summary:
            parts.append("Aprendizado recente:")
            for item in adaptation_summary:
                parts.append(f"- {item}")

        if not parts:
            return ""
        return "CONTEXTO DO AGENTE\n" + "\n".join(parts)
