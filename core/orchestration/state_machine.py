# -*- coding: utf-8 -*-
"""Máquina de estados da conversação da VEGA.

Substitui a combinação de booleanos independentes (is_speaking,
is_processing, session_active, news_prompt_active...) por um estado único
e previsível, com transições validadas.

Estados:
    IDLE        - sessão encerrada, aguardando ativação (palma/wake word)
    LISTENING   - microfone aberto, aguardando o usuário falar
    PROCESSING  - STT em andamento / pré-processamento do turno
    THINKING    - contexto montado, aguardando o modelo
    SPEAKING    - TTS reproduzindo (ou enfileirado)
    INTERRUPTED - fala do usuário interrompeu a resposta
    EXECUTING   - executando comando/ferramenta (sem resposta falada)
    ERROR       - falha do turno; recuperação automática para o estado anterior
"""

import threading


class InvalidTransitionError(RuntimeError):
    pass


class ConversationState:
    """Estado único da VEGA com transições explícitas e observadores."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    EXECUTING = "EXECUTING"
    ERROR = "ERROR"

    # Transições permitidas: de -> {conjunto de estados válidos}
    TRANSITIONS = {
        IDLE: {LISTENING},
        LISTENING: {PROCESSING, IDLE, SPEAKING},
        PROCESSING: {THINKING, SPEAKING, LISTENING, EXECUTING, ERROR, IDLE, INTERRUPTED},
        THINKING: {SPEAKING, INTERRUPTED, ERROR, LISTENING, IDLE, EXECUTING},
        SPEAKING: {LISTENING, INTERRUPTED, IDLE, ERROR, THINKING},
        INTERRUPTED: {LISTENING, PROCESSING, IDLE},
        EXECUTING: {LISTENING, IDLE, SPEAKING, ERROR},
        ERROR: {LISTENING, IDLE, PROCESSING, THINKING, SPEAKING, EXECUTING},
    }

    def __init__(self, initial=None, on_change=None):
        self._lock = threading.RLock()
        self._state = initial or self.IDLE
        self._previous = self._state
        self._on_change = on_change

    # --------------------------------------------------------------
    # API principal
    # --------------------------------------------------------------

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def previous(self) -> str:
        with self._lock:
            return self._previous

    def transition(self, new_state: str, force: bool = False) -> str:
        """Muda de estado validando a transição. Retorna o estado atual."""
        with self._lock:
            current = self._state
            if new_state == current:
                return current
            allowed = self.TRANSITIONS.get(current, set())
            if not force and new_state not in allowed:
                raise InvalidTransitionError(
                    f"Transição inválida: {current} -> {new_state}"
                )
            self._previous = current
            self._state = new_state
        callback = self._on_change
        if callback:
            try:
                callback(self._previous, new_state)
            except Exception:
                pass
        return new_state

    # Consultas de conveniência (leitura atômica de um único campo)
    def is_speaking(self) -> bool:
        with self._lock:
            return self._state == self.SPEAKING

    def is_active(self) -> bool:
        with self._lock:
            return self._state != self.IDLE

    def is_busy(self) -> bool:
        """Ocupada = não pode iniciar um turno novo sem interromper."""
        with self._lock:
            return self._state in {
                self.PROCESSING, self.THINKING, self.SPEAKING, self.EXECUTING,
            }

    def can_listen(self) -> bool:
        with self._lock:
            return self._state in {self.LISTENING, self.SPEAKING}

    # --------------------------------------------------------------
    # Diagnóstico
    # --------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "previous": self._previous,
            }
