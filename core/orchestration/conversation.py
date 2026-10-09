# -*- coding: utf-8 -*-
# ==============================================================
# CONVERSATION ORCHESTRATOR
# ==============================================================
#
# Camada de orquestração da conversa da VEGA. Responsabilidades:
#
# 1. Contexto em quatro camadas separadas:
#    - imediato (últimos turnos — vai integralmente ao modelo);
#    - sessão (resumo de assuntos/decisões — vai ao modelo como bloco curto);
#    - tarefa (tarefa em execução, alvo, correções — vai ao modelo);
#    - persistente (delegada à VegaMemory, recuperada seletivamente).
#
# 2. Resolução de referências ("isso", "ele", "aquilo", "o anterior",
#    "e quando...") via reescrita do turno do usuário com o antecedente,
#    sem depender do modelo para adivinhar o histórico.
#
# 3. Contexto de tarefa: acompanha a última ação/comando (ex.: app aberto),
#    permite correções ("não, deixa", "procura pela pasta X") e cancelamento
#    ("não, deixa" abandona o contexto de tarefa).
#
# 4. Instrução conversacional condensada (anti-repetição, continuidade,
#    perguntas só quando necessário) injetada como mensagem de sistema.
#
# O orquestrador não fala com a API do Gemini e não executa comandos;
# o VegaBrain continua sendo o ponto de entrada (process/process_stream)
# e delega a preparação do contexto para cá. Isso mantém a compatibilidade
# com todos os testes existentes.
#
# ==============================================================

import re
import time
import threading


# --------------------------------------------------------------
# Normalização
# --------------------------------------------------------------

def _fold(text: str) -> str:
    value = (text or "").lower()
    replacements = {"á": "a", "à": "a", "ã": "a", "â": "a", "é": "e", "ê": "e",
                    "í": "i", "ó": "o", "õ": "o", "ô": "o", "ú": "u", "ç": "c"}
    value = "".join(replacements.get(ch, ch) for ch in value)
    value = re.sub(r"[.,!?;:]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


# --------------------------------------------------------------
# Referências anafóricas
# --------------------------------------------------------------

_DEMONSTRATIVES = (
    "isso", "isto", "aquilo", "ele", "ela", "eles", "elas",
    "esse", "essa", "esquilo", "o anterior", "a anterior",
    "o assunto", "tal", "dessa", "desse", "disso",
)

_CONTINUATION_PATTERNS = (
    r"^e (?:quando|onde|quem|por que|porque|como|quanto|qual)",
    r"^(?:e )?(?:mais|menos|outra vez|de novo|tambem|também)",
    r"^(?:nao|não)[, ]",
    r"^(?:mas|porem|porém|entao|então|agora)",
    r"^(?:por que|porque|como|qual|quanto|onde|quando)\b",
    r"^(?:cancele|cancela|para|pare|deixa)",
)


class TaskContext:
    """Contexto da tarefa atual (separado do contexto de conversa)."""

    def __init__(self):
        self.name = None        # tipo da tarefa: "open_app", "search", ...
        self.target = None      # alvo específico: "chrome", "notebooks", ...
        self.details = {}       # parâmetros adicionais
        self.state = None       # "pending", "done", "cancelled"
        self.started_at = None

    def start(self, name, target=None, **details):
        self.name = name
        self.target = target
        self.details = dict(details)
        self.state = "pending"
        self.started_at = time.time()

    def complete(self):
        self.state = "done"

    def cancel(self):
        self.state = "cancelled"

    def snapshot(self):
        return {
            "name": self.name,
            "target": self.target,
            "state": self.state,
            "details": dict(self.details),
        }


class ConversationOrchestrator:
    """Orquestra contexto, referências, tarefa atual e instrução conversacional."""

    def __init__(self, max_immediate_turns: int = 10, max_session_topics: int = 8):
        self._lock = threading.RLock()

        # Contexto imediato: turnos (user/assistant) recentes.
        self.immediate = []

        # Contexto de sessão: assuntos tratados nesta sessão.
        self.session_topics = []            # [{"topic": str, "at": ts}]

        # Contexto de tarefa.
        self.task = TaskContext()

        # Anti-repetição.
        self.recent_openings = []           # começos das respostas recentes

        # Assunto do último dado verificado por ferramenta (cotação, clima...).
        # Antecedente preferencial de "e por que isso?".
        self.last_tool_subject = ""

        self.max_immediate_turns = max_immediate_turns
        self.max_session_topics = max_session_topics

    # ==============================================================
    # REGISTRO DE TURNOS
    # ==============================================================

    def record_user_turn(self, text: str, rewritten: str | None = None):
        with self._lock:
            self.immediate.append({
                "role": "user",
                "content": rewritten or text,
                "original": text,
                "at": time.time(),
            })
            if len(text) > 25:
                self._note_session_topic(text)
            self._trim()

    def record_assistant_turn(self, text: str):
        with self._lock:
            self.immediate.append({
                "role": "assistant",
                "content": text,
                "at": time.time(),
            })
            opening = " ".join(_fold(text).split()[:6])
            if opening:
                self.recent_openings.append(opening)
                self.recent_openings = self.recent_openings[-6:]
            self._trim()

    def _trim(self):
        limit = self.max_immediate_turns * 2
        if len(self.immediate) > limit:
            # Pergunta/resposta mais antigas vão para o resumo da sessão.
            dropped = self.immediate[:-limit]
            self.immediate = self.immediate[-limit:]
            for entry in dropped:
                if entry["role"] == "user" and len(entry.get("original", "")) > 25:
                    self._note_session_topic(entry["original"])

    def _note_session_topic(self, text: str):
        topic = _fold(text)
        topic = re.sub(r"^(vega|como vai|ola|oi)\b[, ]*", "", topic)
        if len(topic) > 60:
            topic = topic[:57] + "..."
        if topic and (not self.session_topics or self.session_topics[-1]["topic"] != topic):
            self.session_topics.append({"topic": topic, "at": time.time()})
            self.session_topics = self.session_topics[-self.max_session_topics:]

    # ==============================================================
    # TAREFA ATUAL
    # ==============================================================

    def start_task(self, name: str, target=None, **details):
        with self._lock:
            self.task.start(name, target, **details)

    def complete_task(self):
        with self._lock:
            self.task.complete()

    def cancel_task(self):
        with self._lock:
            self.task.cancel()

    # --------------------------------------------------------------
    # Detecção de intenção sobre a tarefa
    # --------------------------------------------------------------

    _CANCEL_PATTERNS = (
        "nao deixa", "não deixa", "deixa", "deixa pra la", "deixa pra lá",
        "cancela", "cancelar", "esquece", "esqueça isso", "nao precisa mais",
        "não precisa mais", "aborta", "abortar", "nao agora", "não agora",
    )

    _CORRECTION_PATTERNS = (
        "nao ", "não ", "na ", "melhor ", "na verdade ", "na verdade,", "ao inves", "ao invés",
        "quero dizer", "mudando", "trocando", "engraçado", "errado",
    )

    def classify_turn_intent(self, text: str) -> str:
        """Classifica o turno: 'cancel', 'correction', 'continuation' ou 'new'."""
        folded = re.sub(r"[,;.!?:]+", " ", _fold(text))
        folded = re.sub(r"\s+", " ", folded).strip()
        if not folded:
            return "new"
        for pattern in self._CANCEL_PATTERNS:
            if folded == pattern or folded.startswith(pattern + " ") or folded.startswith(pattern + ","):
                return "cancel"
        for prefix in self._CORRECTION_PATTERNS:
            if folded.startswith(prefix):
                return "correction"
        for pattern in _CONTINUATION_PATTERNS:
            if re.match(pattern, folded):
                return "continuation"
        if any(re.search(rf"\b{re.escape(word)}\b", folded) for word in _DEMONSTRATIVES):
            return "continuation"
        # Continuação contextual: mesmo assunto e tarefa em andamento.
        with self._lock:
            task_pending = self.task.state == "pending"
        if task_pending:
            return "continuation"
        return "new"

    def handle_turn_intent(self, text: str) -> str | None:
        """Aplica a intenção ao contexto de tarefa.

        Retorna texto de resposta direta quando o turno deve ser tratado
        pelo orquestrador (ex.: cancelamento), None quando o fluxo normal
        do cérebro deve continuar.
        """
        intent = self.classify_turn_intent(text)
        folded = _fold(text)

        if intent == "cancel":
            task = self.task
            if task.state == "pending":
                task.cancel()
                if task.name == "open_app" and task.target:
                    return f"Tudo bem, deixo o {task.target} aberto."
                return "Tudo bem, cancelo isso."
            # Cancelamento sem tarefa pendente: nada a responder aqui.
            return None

        if intent == "correction":
            # A correção substitui a tarefa: o cérebro reprocessa a nova
            # instrução com o contexto atualizado.
            with self._lock:
                if self.task.state == "pending":
                    self.task.details["corrected"] = True
            return None

        if intent == "continuation" and self.task.state == "pending":
            # "pesquisa por notebooks" depois de "abra o chrome": mantém a
            # tarefa aberta e anota a continuação como detalhe.
            with self._lock:
                self.task.details.setdefault("followups", []).append(text[:120])
            return None

        return None

    # ==============================================================
    # REESCRITA DE REFERÊNCIAS
    # ==============================================================

    def resolve_references(self, text: str) -> tuple[str, bool]:
        """Reescreve referências vagas usando o contexto imediato.

        Retorna (texto_reescrito, houve_reescrita).

        Regras:
        - "isso disso aquilo" + dado de ferramenta recente → substitui pelo
          assunto do dado (ex.: "e por que isso?" → "e por que a cotação do
          dólar está nesse valor?");
        - "isso disso aquilo" + tarefa pendente com alvo → substitui pelo alvo;
        - sem antecedente → marca contexto como perdido (métrica) e devolve
          o texto original (o modelo decide se pergunta de esclarecimento).
        """
        folded_words = set(_fold(text).split())
        has_reference = bool(folded_words & {w for w in _DEMONSTRATIVES if " " not in w})

        if not has_reference:
            return text, False

        # O dado verificado mais recente é o antecedente mais provável: a fala
        # anterior foi uma consulta real (cotação, clima) e "isso" aponta para
        # o valor consultado, não para um tema genérico da sessão.
        receipt_antecedent = self._receipt_antecedent()

        with self._lock:
            antecedent = receipt_antecedent
            if not antecedent:
                if self.task.target and self.task.state in ("pending", "done"):
                    antecedent = self.task.target
                elif self.session_topics:
                    antecedent = self.session_topics[-1]["topic"]

        if not antecedent:
            return text, False

        # Reescreve de forma natural: "e por que isso?" vira "e por que a
        # cotação do dólar está nesse valor?". Colar o recibo inteiro aqui
        # produzia frases quebradas ("e porque Cotação USD→BRL: 5,43...").
        rewritten = self._rewrite_with_antecedent(text, antecedent)
        if rewritten is None:
            return text, False
        return rewritten, True

    # Conectores causais: a pergunta é sobre o MOTIVO do dado consultado.
    _CAUSAL_MARKERS = ("por que", "porque", "porquê", "pq")

    def _rewrite_with_antecedent(self, text: str, antecedent: str):
        """Troca o demonstrativo pelo antecedente, ajustando a frase."""
        folded = _fold(text)
        is_causal = any(marker in folded for marker in self._CAUSAL_MARKERS)

        replacement = antecedent
        # Pergunta causal sobre um dado: o antecedente é o SUJEITO, não o valor.
        # "e por que isso?" -> "e por que a cotação do dólar está nesse valor?"
        if is_causal:
            replacement = f"{antecedent} está nesse valor"

        for word in ("isso", "disso", "aquilo", "isto"):
            pattern = rf"\b{word}\b(?!\s+mesmo)"
            if re.search(pattern, text, flags=re.IGNORECASE):
                return re.sub(
                    pattern,
                    replacement,
                    text,
                    count=1,
                    flags=re.IGNORECASE,
                )
        return None

    # --------------------------------------------------------------
    # Antecedente de dado verificado (ferramenta)
    # --------------------------------------------------------------

    def note_tool_receipt(self, subject: str, topic: str = ""):
        """Registra o assunto do último dado verificado por uma ferramenta.

        ``subject`` é o recibo completo (para o bloco de contexto); ``topic`` é
        a forma curta usada para reescrever referências ("a cotação do dólar").
        """
        subject = str(subject or "").strip()
        if not subject:
            return
        with self._lock:
            self.last_tool_subject = subject
            self.last_tool_topic = str(topic or "").strip() or subject

    def _receipt_antecedent(self) -> str:
        with self._lock:
            return getattr(self, "last_tool_topic", "") or getattr(self, "last_tool_subject", "")

    # ==============================================================
    # MONTAGEM DO CONTEXTO PARA O MODELO
    # ==============================================================

    def build_context_block(self) -> str:
        """Bloco de sistema com contexto de sessão, tarefa e instrução conversacional."""
        with self._lock:
            topics = [entry["topic"] for entry in self.session_topics]
            task = self.task.snapshot()
            immediate_topics = [
                entry["original"] for entry in self.immediate
                if entry["role"] == "user"
            ][-3:]

        lines = []

        if immediate_topics:
            lines.append(
                "ASSUNTOS RECENTES DA CONVERSA (em ordem): "
                + " | ".join(t[:80] for t in immediate_topics)
            )

        if task["name"]:
            status_map = {"pending": "em andamento", "done": "concluída", "cancelled": "cancelada"}
            task_line = (
                f"TAREFA ATUAL: {task['name']}"
                + (f" (alvo: {task['target']})" if task["target"] else "")
                + f" — status: {status_map.get(task['state'], task['state'])}"
            )
            if task.get("details", {}).get("corrected"):
                task_line += " — o usuário já corrigiu esta tarefa antes; considere a última instrução."
            lines.append(task_line)

        if topics:
            lines.append("ASSUNTOS DESTA SESSÃO: " + "; ".join(topics[-4:]))

        instruction = (
            "REGRAS DE CONVERSA CONTÍNUA:\n"
            "- Você está em uma conversa contínua por voz. Trate cada fala como parte de um diálogo, não como pedido isolado.\n"
            "- Frases curtas ou incompletas (ex.: 'e quando isso aconteceu?', 'pesquisa por notebooks') geralmente CONTINUAM o assunto ou a tarefa anterior. Use o contexto acima em vez de dizer que não entendeu.\n"
            "- 'isso', 'ele', 'aquilo' se referem ao assunto/tarefa acima. Se faltar informação essencial, faça UMA pergunta curta de esclarecimento.\n"
            "- Se o usuário corrigir você, aceite a correção sem reclamar e siga a nova instrução.\n"
            "- Não repita respostas anteriores: varie a abertura e o fechamento.\n"
            "- Não cumprimente novamente nem se apresente em turnos seguintes.\n"
            "- Responda proporcionalmente: respostas de voz devem ser curtas e diretas; aprofunde apenas se o usuário pedir.\n"
            "- Quando concluir ou abandonar uma tarefa, deixe claro de forma natural em uma frase."
        )
        lines.append(instruction)

        if not lines:
            return ""
        return "CONTEXTO CONVERSACIONAL\n" + "\n".join(lines)

    # ==============================================================
    # ANTI-REPETIÇÃO
    # ==============================================================

    def looks_repeated(self, response: str) -> bool:
        opening = " ".join(_fold(response).split()[:6])
        with self._lock:
            return bool(opening) and opening in self.recent_openings

    def varied_answer(self, response: str) -> str:
        """Se a resposta repete a abertura recente, devolve uma variação mínima."""
        if not self.looks_repeated(response):
            return response
        # Prefixos alternativos curtos para desambiguar a abertura repetida.
        alternatives = ("De novo por aqui: ", "Deixa eu reformular: ", "Resumindo: ")
        return alternatives[len(self.recent_openings) % len(alternatives)] + response

    # ==============================================================
    # SNAPSHOT / RESET
    # ==============================================================

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "immediate_turns": len(self.immediate),
                "session_topics": [t["topic"] for t in self.session_topics],
                "task": self.task.snapshot(),
                "recent_openings": list(self.recent_openings),
            }

    def clear(self):
        with self._lock:
            self.immediate.clear()
            self.session_topics.clear()
            self.recent_openings.clear()
            self.last_tool_subject = ""
            self.last_tool_topic = ""
            self.task = TaskContext()
