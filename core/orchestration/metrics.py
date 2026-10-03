# -*- coding: utf-8 -*-
"""Observabilidade de latência e métricas da conversação da VEGA.

Fornece:
- cronômetro por fase (STT fim → contexto → request → primeiro token →
  TTS primeiro áudio → resposta completa);
- métricas agregadas da sessão (interrupções, erros, estados inválidos).

É um módulo puro, sem dependências do núcleo, para poder ser usado
por qualquer componente (brain, speaker, vega.py, testes).
"""

import time
import threading
from collections import deque


class LatencyTrace:
    """Cronômetro nomeado de fases de um único turno de conversa."""

    PHASES = (
        "speech_end",       # fim da fala do usuário
        "stt_done",         # transcrição pronta
        "context_ready",    # contexto/memória preparados
        "request_started",  # request ao modelo iniciado
        "first_token",      # primeiro evento do modelo
        "tts_started",      # TTS recebeu o primeiro segmento
        "first_audio",      # primeiro áudio reproduzido
        "response_done",    # turno completo
    )

    def __init__(self, turn_id: int = 0):
        self.turn_id = turn_id
        self._t0 = time.perf_counter()
        self._marks: dict = {}
        self._lock = threading.Lock()

    def mark(self, phase: str):
        """Registra o instante de uma fase (idempotente: só o primeiro marca)."""
        with self._lock:
            if phase not in self._marks:
                self._marks[phase] = time.perf_counter() - self._t0

    def elapsed_to(self, phase: str) -> float | None:
        with self._lock:
            value = self._marks.get(phase)
        return None if value is None else round(value * 1000.0, 1)

    def deltas(self) -> dict:
        """Intervalos entre fases consecutivas em ms."""
        out = {}
        prev_name = None
        with self._lock:
            marks = dict(self._marks)
        for phase in self.PHASES:
            if phase not in marks:
                prev_name = phase if prev_name is None else prev_name
                continue
            if prev_name is not None and prev_name in marks:
                out[f"{prev_name}->{phase}"] = round(
                    (marks[phase] - marks[prev_name]) * 1000.0, 1
                )
            prev_name = phase
        return out

    def report(self) -> str:
        parts = []
        for phase in self.PHASES:
            value = self.elapsed_to(phase)
            if value is not None:
                parts.append(f"{phase}={value}ms")
        return " ".join(parts) if parts else "(sem fases registradas)"

    def __repr__(self):
        return f"<LatencyTrace turn={self.turn_id} {self.report()}>"


class ConversationMetrics:
    """Métricas objetivas agregadas da sessão (thread-safe)."""

    def __init__(self, history: int = 200):
        self._lock = threading.Lock()
        self.turns = 0
        self.interruptions = 0
        self.cancelled_turns = 0
        self.errors = 0
        self.invalid_state_transitions = 0
        self.recovered_errors = 0
        self.tool_failures = 0
        self.context_misses = 0  # frases com referência sem antecedente
        self.duplicated_responses = 0
        self.first_token_ms: deque = deque(maxlen=history)
        self.first_audio_ms: deque = deque(maxlen=history)
        self.total_turn_ms: deque = deque(maxlen=history)

    # --------------------------------------------------------------

    def record_turn(self, trace: LatencyTrace | None):
        with self._lock:
            self.turns += 1
            if trace is None:
                return
            ft = trace.elapsed_to("first_token")
            fa = trace.elapsed_to("first_audio")
            td = trace.elapsed_to("response_done")
            if ft is not None:
                self.first_token_ms.append(ft)
            if fa is not None:
                self.first_audio_ms.append(fa)
            if td is not None:
                self.total_turn_ms.append(td)

    def record(self, **counters):
        with self._lock:
            for key, value in counters.items():
                if hasattr(self, key):
                    setattr(self, key, getattr(self, key) + int(value))

    def snapshot(self) -> dict:
        def summary(values):
            if not values:
                return {"count": 0, "avg_ms": None, "min_ms": None, "max_ms": None, "p50_ms": None}
            ordered = sorted(values)
            return {
                "count": len(ordered),
                "avg_ms": round(sum(ordered) / len(ordered), 1),
                "min_ms": round(ordered[0], 1),
                "max_ms": round(ordered[-1], 1),
                "p50_ms": round(ordered[len(ordered) // 2], 1),
            }

        with self._lock:
            return {
                "turns": self.turns,
                "interruptions": self.interruptions,
                "cancelled_turns": self.cancelled_turns,
                "errors": self.errors,
                "recovered_errors": self.recovered_errors,
                "invalid_state_transitions": self.invalid_state_transitions,
                "tool_failures": self.tool_failures,
                "context_misses": self.context_misses,
                "duplicated_responses": self.duplicated_responses,
                "first_token": summary(self.first_token_ms),
                "first_audio": summary(self.first_audio_ms),
                "total_turn": summary(self.total_turn_ms),
            }

    def summary_line(self) -> str:
        data = self.snapshot()
        return (
            "[METRICS] turnos={turns} interrupções={interruptions} "
            "cancelados={cancelled_turns} erros={errors} "
            "1ºtoken(avg)={ft}ms 1ºáudio(avg)={fa}ms".format(
                turns=data["turns"],
                interruptions=data["interruptions"],
                cancelled_turns=data["cancelled_turns"],
                errors=data["errors"],
                ft=data["first_token"]["avg_ms"],
                fa=data["first_audio"]["avg_ms"],
            )
        )


# Instância global compartilhada por toda a VEGA (um ponto único de métricas).
SESSION_METRICS = ConversationMetrics()
