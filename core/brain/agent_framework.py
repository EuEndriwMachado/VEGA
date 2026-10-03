from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional
import re


class PermissionManager:
    """Centraliza classificação de risco e confirmação antes de ações sensíveis."""

    SAFE_PATTERNS = (
        "consultar horario",
        "consultar hora",
        "calculo",
        "calcular",
        "ler arquivo publico",
        "pesquisar",
        "resumo",
        "conversar",
    )
    MODERATE_PATTERNS = (
        "criar arquivo",
        "abrir aplicativo",
        "modificar arquivo",
        "escrever arquivo",
        "criar documento",
        "abrir vs code",
        "executar script",
    )
    DANGEROUS_PATTERNS = (
        "apagar arquivos",
        "deletar arquivos",
        "remover dados",
        "instalar software",
        "alterar configuracoes criticas",
        "reconfigurar sistema",
        "formatar disco",
        "apagar tudo",
    )

    @staticmethod
    def _normalize(text: str) -> str:
        value = (text or "").lower()
        value = re.sub(r"[^a-z0-9\s]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def evaluate(self, action: str) -> str:
        normalized = self._normalize(action)
        if any(pattern in normalized for pattern in self.DANGEROUS_PATTERNS):
            return "dangerous"
        if any(pattern in normalized for pattern in self.MODERATE_PATTERNS):
            return "moderate"
        if any(pattern in normalized for pattern in self.SAFE_PATTERNS):
            return "safe"
        if any(keyword in normalized for keyword in ("arquivo", "aplicar", "abrir", "gravar", "configur")):
            return "moderate"
        return "safe"

    def request_confirmation(self, action: str) -> bool:
        return self.evaluate(action) in {"dangerous", "moderate"}

    def authorize(self, action: str, confirmed: bool = False) -> bool:
        level = self.evaluate(action)
        if level == "safe":
            return True
        return bool(confirmed)


@dataclass
class Skill:
    name: str
    description: str
    version: str = "1.0.0"
    permissions: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    entrypoint: Optional[Callable[..., Any]] = None

    def execute(self, **kwargs: Any) -> Any:
        if self.entrypoint is None:
            raise ValueError(f"Skill '{self.name}' não possui entrypoint.")
        return self.entrypoint(**kwargs)


class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill) -> Skill:
        self._skills[skill.name] = skill
        return skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list(self) -> List[Skill]:
        return list(self._skills.values())

    def execute(self, name: str, **kwargs: Any) -> Any:
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"Skill '{name}' não registrada.")
        return skill.execute(**kwargs)


class BaseAgent:
    def __init__(self, name: str, role: str, capabilities: Iterable[str] = ()):
        self.name = name
        self.role = role
        self.capabilities = list(capabilities)

    def handle(self, task: str, context: Optional[Dict[str, Any]] = None) -> str:
        return f"[{self.name}] processei: {task}"


class AgentOrchestrator:
    """Orquestra tarefas simples versus complexas, preservando o núcleo atual da Vega."""

    COMPLEX_MARKERS = (
        "sistema de login",
        "crie um sistema",
        "plano",
        "arquitetura",
        "aplicativo",
        "implement",
        "autentic",
        "teste",
        "debug",
        "integrar",
        "migrar",
        "projeto",
        "fluxo",
    )

    PLAN_TEMPLATES = {
        "auth": [
            "1. Analisar o objetivo e o contexto do projeto.",
            "2. Identificar a estrutura e as dependências relevantes.",
            "3. Implementar a funcionalidade principal.",
            "4. Criar ou ajustar testes mínimos de validação.",
            "5. Verificar o resultado e corrigir pontos de risco.",
        ],
        "default": [
            "1. Entender o pedido e confirmar o objetivo.",
            "2. Executar a ação principal de forma direta.",
            "3. Verificar o resultado final.",
        ],
    }

    FAILURE_MARKERS = (
        "falhou",
        "erro",
        "falha",
        "problema",
        "quebrou",
        "inconsistente",
        "incorreto",
    )

    def __init__(self, skill_registry: Optional[SkillRegistry] = None):
        self.skill_registry = skill_registry or SkillRegistry()
        self.permission_manager = PermissionManager()
        self.agents = {
            "planner": BaseAgent("PlannerAgent", "planejamento", ["planejar", "quebrar tarefas"]),
            "coder": BaseAgent("CoderAgent", "programacao", ["codificar", "refatorar"]),
            "research": BaseAgent("ResearchAgent", "pesquisa", ["pesquisar", "comparar"]),
            "reviewer": BaseAgent("ReviewerAgent", "revisao", ["revisar", "validar"]),
        }

    def is_complex_task(self, task: str) -> bool:
        normalized = (task or "").lower()
        return any(marker in normalized for marker in self.COMPLEX_MARKERS) and len(normalized.split()) >= 5

    def plan(self, task: str) -> List[str]:
        normalized = (task or "").lower()
        if not normalized:
            return ["1. Entender a tarefa."]

        if any(marker in normalized for marker in ("login", "autentic", "sistema", "aplicativo", "projeto")):
            return list(self.PLAN_TEMPLATES["auth"])

        return list(self.PLAN_TEMPLATES["default"])

    def handle_task(self, task: str, context: Optional[Dict[str, Any]] = None) -> str:
        if self.is_complex_task(task):
            steps = self.plan(task)
            return "Vou dividir isso em etapas: " + "; ".join(steps)
        return f"Vou resolver diretamente: {task}"

    def verify_execution(
        self,
        task: str,
        evidence: str,
        success: bool = True,
        retries: int = 0,
    ) -> Dict[str, Any]:
        evidence_text = evidence or ""
        evidence_normalized = evidence_text.lower()

        if success:
            outcome = {
                "status": "succeeded",
                "summary": "Verificação concluída com sucesso. O resultado atende ao objetivo solicitado.",
                "next_action": "Prosseguir para a próxima etapa do plano.",
            }
            return {
                "status": outcome["status"],
                "summary": outcome["summary"],
                "next_action": outcome["next_action"],
                "task": task,
                "evidence": evidence_text,
                "retries": retries,
                "verified": True,
            }

        needs_retry = any(marker in evidence_normalized for marker in self.FAILURE_MARKERS)
        status = "needs_retry" if needs_retry else "needs_review"
        summary = "A verificação indicou problema no resultado atual e exige verificar novamente antes de seguir."
        next_action = (
            "Parar e solicitar revisão humana ou replanejar a solução antes de continuar."
            if retries >= 2
            else "Corrigir a causa raiz, ajustar a execução e verificar novamente."
        )

        return {
            "status": status,
            "summary": summary,
            "next_action": next_action,
            "task": task,
            "evidence": evidence_text,
            "retries": retries,
            "verified": True,
        }

    def classify(self, task: str) -> str:
        return "complex" if self.is_complex_task(task) else "simple"
