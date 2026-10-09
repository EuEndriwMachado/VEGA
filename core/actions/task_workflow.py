'''Fluxos conversacionais de tarefas de uso diário.

Este módulo coleta contexto de uma tarefa, agenda o prazo e devolve uma próxima
pergunta objetiva. As regras ficam isoladas para evitar um parser remendado.
'''

from __future__ import annotations
import json
import re
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
class TaskWorkflow:
    """Conduz a criação de um trabalho sem interromper outros comandos."""

    START_PATTERNS = (
        r"\b(?:bora|vamos|quero|preciso|tenho que|me ajuda a)\s+(?:fazer|com|terminar|organizar)\s+(?:um\s+)?(?:trabalho|projeto|atividade)\b",
        r"\b(?:iniciar|começar|comecar|criar|organizar)\s+(?:um\s+)?(?:trabalho|projeto|atividade)\b",
    )
    TYPE_RULES = (
        ("escola", ("escola", "escolar", "professora", "professor", "curso", "etec", "faculdade")),
        ("trabalho", ("trabalho", "projeto", "atividade")),
    )
    DOMAIN_RULES = (
        ("inglês", ("ingles", "inglês")),
        ("programação", ("programacao", "programação", "codigo", "código")),
        ("matemática", ("matematica", "matemática")),
        ("física", ("fisica", "física")),
    )
    OPENING_RESPONSES = (
        "Bora. Que tipo de trabalho é esse: escola, curso, pessoal ou outra coisa?",
        "Fechado. Me diz primeiro que tipo de trabalho você precisa fazer.",
        "Tá, vamos organizar isso antes que vire uma emergência acadêmica. É trabalho de escola ou de outro tipo?",
    )
    DEADLINE_RESPONSES = (
        "Entendi. E para que dia é a apresentação ou entrega?",
        "Já peguei o contexto. Qual é o prazo real disso?",
        "Certo. Agora me passa a data da entrega — sem esconder o desastre até a véspera, por favor.",
    )
    MODE_RESPONSES = (
        "Prazo registrado. Quer que eu ative o modo Trabalho e monte as etapas agora?",
        "Anotei a entrega. Ativo o modo Trabalho e divido isso em partes menores?",
        "Está no radar. Quer modo Trabalho para a gente transformar esse caos em etapas?",
    )

    def __init__(self, scheduler=None, mode_callback=None, path="data/task_workflow.json"):
        self.scheduler = scheduler
        self.mode_callback = mode_callback
        self.path = Path(path)
        self._lock = threading.RLock()
        self.state = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _fold(text):
        value = unicodedata.normalize("NFKD", str(text or "").lower())
        value = "".join(char for char in value if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", value).strip()

    @classmethod
    def is_start(cls, text):
        folded = cls._fold(text)
        return any(re.search(pattern, folded) for pattern in cls.START_PATTERNS)

    def active(self):
        return bool(self.state.get("status") == "collecting")

    def _pick(self, text, rules):
        folded = self._fold(text)
        for value, markers in rules:
            if any(marker in folded for marker in markers):
                return value
        return ""

    @staticmethod
    def _deadline(text):
        folded = TaskWorkflow._fold(text)
        now = datetime.now().astimezone()
        number_words = {
            "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3,
            "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
            "oito": 8, "nove": 9, "dez": 10,
        }
        amount_pattern = r"(\d+|" + "|".join(number_words) + r")"
        match = re.search(r"(?:daqui a|em)\s+" + amount_pattern + r"\s+(dia|dias|hora|horas)", folded)
        if match:
            raw_amount = match.group(1)
            amount = int(raw_amount) if raw_amount.isdigit() else number_words[raw_amount]
            unit = match.group(2)
            due = now + timedelta(hours=amount if unit.startswith("hora") else amount * 24)
            return due
        match = re.search(r"(?:dia|data)\s+(\d{1,2})(?:\s+de\s+(\d{1,2}))?", folded)
        if match:
            day = int(match.group(1))
            month = int(match.group(2) or now.month)
            year = now.year
            try:
                due = now.replace(year=year, month=month, day=day, hour=18, minute=0, second=0, microsecond=0)
                return due if due >= now else due.replace(year=year + 1)
            except ValueError:
                return None
        if "amanha" in folded:
            return now + timedelta(days=1)
        if "hoje" in folded:
            return now
        return None
    def _schedule(self, task):
        due = task.get("due_at")
        if not due or self.scheduler is None:
            return None
        try:
            scheduled_at = datetime.fromisoformat(due).replace(tzinfo=None)
            return self.scheduler.add(
                f"Entrega: {task['title']}", scheduled_at, kind="deadline"
            )
        except (TypeError, ValueError):
            return None
    @staticmethod
    def _extract_title(details):
        folded = TaskWorkflow._fold(details)
        if "maquete" in folded and ("seiot" in folded or "iot" in folded):
            return "Maquete e apresentação de SEIOT"
        if "apresent" in folded:
            return "Preparar apresentação escolar"
        return "Trabalho escolar"

    @staticmethod
    def _build_steps(task):
        details = task.get("details", "")
        title = task.get("title", "trabalho")
        steps = [
            f"Definir o conteúdo principal de {title.lower()}.",
            "Organizar a explicação em uma sequência simples.",
            "Preparar o material visual e revisar a apresentação.",
        ]
        folded = TaskWorkflow._fold(details)
        if "maquete" in folded:
            steps = [
                "Definir a estrutura e os componentes da maquete.",
                "Escrever a explicação de SEIOT e separar os termos em inglês.",
                "Montar a maquete e conectar cada parte à explicação.",
                "Criar os slides e ensaiar a apresentação.",
            ]
        return steps
    def _set_planned(self, task):
        task["steps"] = self._build_steps(task)
        self.state["status"] = "planned"
        self.state["step"] = "planned"
        self._save()

    def handle(self, text):
        "Retorna resposta quando o fluxo consome a fala; senão None."
        with self._lock:
            folded = self._fold(text)
            if not self.active():
                if not self.is_start(text):
                    return None
                self.state = {"status": "collecting", "step": "type", "created_at": datetime.now().astimezone().isoformat(), "task": {}}
                self._save()
                return self.OPENING_RESPONSES[len(folded) % len(self.OPENING_RESPONSES)]

            task = self.state.setdefault("task", {})
            step = self.state.get("step", "type")
            if step == "type":
                task["type"] = self._pick(text, self.TYPE_RULES) or "trabalho"
                task["domain"] = self._pick(text, self.DOMAIN_RULES)
                self.state["step"] = "details"
                self._save()
                return "Entendi. Me conta o que precisa ser feito, qual é o tema e como você precisa apresentar isso."

            if step == "details":
                task["details"] = str(text).strip()
                task["title"] = self._extract_title(text)
                task["domain"] = task.get("domain") or self._pick(text, self.DOMAIN_RULES)
                self.state["step"] = "deadline"
                self._save()
                return self.DEADLINE_RESPONSES[len(str(text)) % len(self.DEADLINE_RESPONSES)]

            if step == "deadline":
                due = self._deadline(text)
                if due is None:
                    return "Preciso da data ou prazo. Pode dizer, por exemplo, 'daqui a dois dias', 'amanhã' ou 'dia 20'."
                task["due_at"] = due.isoformat()
                task["deadline_label"] = due.astimezone().strftime("%d/%m às %H:%M")
                scheduled = self._schedule(task)
                task["scheduled_id"] = scheduled.get("id") if scheduled else None
                self.state["step"] = "mode"
                self._save()
                return self.MODE_RESPONSES[len(task.get("details", "")) % len(self.MODE_RESPONSES)]

            if step == "mode":
                confirmations = ("sim", "quero", "ativa", "ativar", "pode", "bora")
                refusals = ("nao", "não", "depois", "mais tarde")
                if any(word in folded for word in confirmations):
                    if callable(self.mode_callback):
                        self.mode_callback()
                    self._set_planned(task)
                    steps = "\n".join(f"{index}. {step}" for index, step in enumerate(task["steps"], 1))
                    return f"Modo Trabalho ativado. Dividi isso assim:\n{steps}"
                if any(word in folded for word in refusals):
                    self._set_planned(task)
                    return "Tudo bem. O prazo ficou registrado; quando quiser, eu monto as etapas."
                return self.MODE_RESPONSES[0]

            return None
    def summary(self):
        with self._lock:
            task = self.state.get("task") or {}
            return dict(task) if task else None
