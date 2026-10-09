# -*- coding: utf-8 -*-
"""Roteador semântico: a IA entende a fala; o código valida e executa.

Por que existe
--------------
O parser local (catálogo de intenções) é o ouvido primário: determinístico,
testável, offline e de custo zero. Mas é limitado ao que foi previsto — uma
fala válida e nunca prevista ("deixa o pc mais claro") cai no vazio e vira
conversa fiada em vez de ação.

Este módulo é o FALLBACK e só é acionado quando o parser local devolve None.
O modelo recebe um catálogo compacto de capacidades reais e devolve JSON:

    {"intent": "brightness_up", "params": {}, "say": "Deixo mais claro."}

O código VALIDA: a intenção precisa existir no catálogo, os parâmetros são
coagidos ao schema, e a ação sempre passa pelo mesmo executor do parser
(comando direto, com toda a segurança de PIN/rosto já existente).

Custo
-----
Uma chamada curta, apenas quando o parser falhou. Conversa pura NUNCA passa
por aqui: ela vai direto para o caminho normal da IA (sem gate, sem JSON).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

# Intenções suspeitas que o modelo pode inventar, bloqueadas por nome.
_BLOCKED_INTENTS = (
    "shutdown", "format", "rm_rf", "delete_everything",
    "shell", "eval", "exec", "system", "run",
)

# Frases que indicam pedido de ação (vs. conversa/pesquisa). O roteador só
# entra quando algo assim aparece; sem isso, o custo seria cobrado em fala
# casual, o que derrota a economia.
_ACTION_HINTS = (
    "abre", "abra", "abrir", "fecha", "feche", "fechar", "encerra", "encerre",
    "liga", "ligar", "desliga", "desligar", "ative", "ativar", "desative",
    "desativar", "aumenta", "aumentar", "abaixa", "abaixar", "diminui",
    "diminuir", "deixa", "deixe", "deixar", "troca", "toca", "tocar", "pausa",
    "pausar", "para", "parar", "pula", "pular", "próxima", "proxima",
    "anterior", "silencia", "silenciar", "muta", "mudo", "desmuta", "volume",
    "brilho", "maximiza", "minimiza", "restaura", "copia", "copiar", "cola",
    "colar", "salva", "salvar", "executa", "execute", "roda", "rodar",
    "atualiza", "atualizar", "instala", "instalar", "limpa", "limpar",
    "organiza", "organizar", "cria", "criar", "faz", "fazer", "configura",
    "configurar",
)


def _fold(text: str) -> str:
    """Normaliza para detecção barata (sem depender do parser)."""
    value = str(text or "").lower().strip()
    replacements = {"á": "a", "à": "a", "ã": "a", "â": "a", "é": "e", "ê": "e",
                    "í": "i", "ó": "o", "õ": "o", "ô": "o", "ú": "u", "ç": "c"}
    return "".join(replacements.get(char, char) for char in value)


class SemanticRouter:
    """Fallback por IA quando o parser local não reconhece a fala."""

    def __init__(
        self,
        ai,
        capabilities,
        max_intents: int = 45,
        max_params: int = 4,
    ):
        """`capabilities` descreve o que o código sabe fazer.

        Aceita DUAS formas, para não quebrar nenhum chamador:

        - uma lista de dicts ``{"name", "domain"}`` (o inventário antigo);
        - um `CapabilityCatalog`, que é a forma completa: além de nome e
          domínio, ele traz descrição, tags e prioridade — e, com isso, a
          seleção por RELEVÂNCIA em vez do corte cego nas primeiras N.

        O corte cego era um bug real: metade do catálogo (tudo o que vinha
        depois da 45ª regra) nunca chegava ao modelo, então o fallback
        semântico ficava cego justamente para as capacidades menos comuns.
        """
        self.ai = ai
        self.max_params = max_params
        self.max_intents = max(1, int(max_intents or 45))
        self._catalog = capabilities if hasattr(capabilities, "select_relevant") else None
        if self._catalog is not None:
            inventory = self._catalog.describe()
        else:
            inventory = list(capabilities or [])
        self.capabilities = inventory[: self.max_intents]
        self._valid_intents = {entry["name"] for entry in inventory}

    def _capabilities_for(self, text: str) -> list:
        """Inventário a enviar ao modelo para esta fala.

        Com o catálogo disponível, entrega as capacidades mais prováveis
        (seleção por relevância) em vez das primeiras N da lista.
        Sem catálogo, mantém o comportamento antigo.
        """
        if self._catalog is None:
            return self.capabilities
        try:
            return self._catalog.describe_relevant(text, limit=self.max_intents)
        except Exception:
            return self.capabilities

    # ------------------------------------------------------------------
    # Gate de custo: só entra quando parece um pedido de ação
    # ------------------------------------------------------------------

    def looks_like_action(self, text: str) -> bool:
        folded = _fold(text)
        if not folded:
            return False
        # Perguntas e conversa não são ações, mesmo que contenham verbos.
        if re.match(r"^(?:o que|qual|quem|quando|onde|por que|porque|como)\b", folded):
            return False
        return any(
            re.search(rf"\b{re.escape(hint)}\b", folded)
            for hint in _ACTION_HINTS
        )

    # ------------------------------------------------------------------
    # Prompt do modelo
    # ------------------------------------------------------------------

    def _build_prompt(self, text: str) -> str:
        capability_lines = [
            f"- {entry['name']} ({entry['domain']})"
            for entry in self._capabilities_for(text)
        ]
        return (
            "VOCÊ É UM CLASSIFICADOR. Sua única saída permitida é um objeto JSON.\n"
            "Você NÃO conversa, NÃO explica, NÃO pergunta e NÃO responde sobre "
            "o assunto da fala. Qualquer saída que não seja JSON é um erro.\n\n"
            "O parser local da VEGA não reconheceu a fala abaixo. Decida se ela "
            "pede uma das capacidades listadas.\n\n"
            "CAPACIDADES (use exatamente um destes nomes, ou none):\n"
            + "\n".join(capability_lines)
            + "\n\nEXEMPLOS DE SAÍDA CORRETA:\n"
            '{"intent": "brightness_up", "params": {}, "say": "Deixando mais claro."}\n'
            '{"intent": "volume_down", "params": {}, "say": "Baixando o volume."}\n'
            '{"intent": "none", "params": {}, "say": ""}\n\n'
            "REGRAS:\n"
            "- Saída = UM objeto JSON, nada antes, nada depois, sem cercas de código.\n"
            "- 'intent' deve ser um nome EXATO da lista acima, ou 'none'.\n"
            "- Use 'none' quando a fala NÃO pede claramente uma dessas ações.\n"
            "- NUNCA responda sobre o conteúdo da fala (ex.: não fale de clima, "
            "notícia ou qualquer assunto). Você só classifica.\n"
            "- 'say' é uma frase curta em pt-BR do que a VEGA fala se a ação for "
            "executada. Se 'intent' for none, deixe 'say' vazio.\n\n"
            f"FALA: {text}\n\n"
            "JSON:"
        )

    # ------------------------------------------------------------------
    # Validação da resposta do modelo
    # ------------------------------------------------------------------

    def _parse_model_answer(self, answer: str) -> Optional[Dict[str, Any]]:
        if not answer:
            return None
        text = str(answer).strip()
        # Remove cercas de código e texto ao redor do objeto.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _coerce_params(self, params: Any) -> Dict[str, Any]:
        """Mantém apenas valores escalar (str/int/float/bool), limitados."""
        if not isinstance(params, dict):
            return {}
        cleaned: Dict[str, Any] = {}
        for key, value in list(params.items())[: self.max_params]:
            name = str(key).strip()[:40]
            if not name:
                continue
            if isinstance(value, (str, int, float, bool)):
                cleaned[name] = value
        return cleaned

    def route(self, text: str) -> Optional[Dict[str, Any]]:
        """Pede ao modelo a intenção estruturada; valida e devolve.

        Devolve None quando: a fala não parece ação, o modelo falhou, o JSON
        é inválido, a intenção não existe ou está bloqueada. Em todos esses
        casos o fluxo normal da VEGA segue inalterado.
        """
        if not self.looks_like_action(text):
            return None

        prompt = self._build_prompt(text)
        try:
            stream = self.ai.chat_stream(
                [{"role": "user", "content": prompt}],
                num_predict=200,
                include_screen=False,
            )
            answer = "".join(
                str(chunk) for chunk in stream if chunk is not None
            ).strip()
        except Exception as error:
            print(f"[VEGA Router] Chamada indisponível: {error}")
            return None

        payload = self._parse_model_answer(answer)
        if payload is None:
            return None

        intent = str(payload.get("intent") or "").strip()
        if not intent or intent.lower() in {"none", "null", "nada"}:
            return None
        intent = intent.lower()

        if intent in _BLOCKED_INTENTS:
            return None
        if intent not in self._valid_intents:
            return None

        return {
            "intent": intent,
            "params": self._coerce_params(payload.get("params")),
            "say": str(payload.get("say") or "").strip(),
        }