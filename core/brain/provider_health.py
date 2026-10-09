# -*- coding: utf-8 -*-
"""Saúde dos provedores de IA — diagnóstico honesto de falha.

Antes, qualquer erro de qualquer provedor virava a mesma frase genérica
("Não consegui responder agora. Tente novamente em alguns segundos."). O
efeito prático era a VEGA mentindo sobre a própria saúde: quando a conta da
OpenAI ficava sem crédito, ela dizia que era problema de rede, e o usuário
perdia tempo reiniciando roteador.

Este módulo classifica a falha pela sua CAUSA REAL (cota esgotada, limite de
requisições, chave inválida, rede, modelo inexistente) e produz uma mensagem
verdadeira. É diagnóstico, não política: quem decide o que fazer com a falha
continua sendo o `_stream_with_fallback` e o failover de provedores.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import json


class FailureKind(str, Enum):
    """Causas de falha, da mais definitiva para a mais transitória."""

    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    AUTH_INVALID = "auth_invalid"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNSUPPORTED = "unsupported"
    NETWORK = "network"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# Trechos que identificam a causa. A ordem importa: o primeiro que casar vence,
# então os casos mais específicos vêm antes dos genéricos.
_SIGNATURES = (
    # Cota/billing — definitivo até o usuário agir. A OpenAI usa
    # "insufficient_quota" / "credit_balance_exhausted"; o Gemini usa
    # "exceeded your current quota" com status 429.
    (FailureKind.QUOTA_EXHAUSTED, (
        "insufficient_quota",
        "credit_balance_exhausted",
        "no credits remaining",
        "exceeded your current quota",
        "billing",
        "quota exceeded",
    )),
    # Chave inválida/revogada — definitivo.
    (FailureKind.AUTH_INVALID, (
        "invalid_api_key",
        "incorrect api key",
        "invalid api key",
        "api key not valid",
        "unauthorized",
        "permission denied",
        "authentication",
    )),
    # Modelo que não existe (ou foi descontinuado) para aquele provedor.
    (FailureKind.MODEL_UNAVAILABLE, (
        "model not found",
        "does not exist",
        "unknown model",
        "no such model",
        "is not supported",
        "unsupported model",
    )),
    # Limite temporário de requisições — vale tentar de novo depois.
    (FailureKind.RATE_LIMITED, (
        "rate limit",
        "rate_limit",
        "too many requests",
        "resource_exhausted",
        "overloaded",
        "try again later",
    )),
    # Bloqueio de conteúdo / parâmetro recusado.
    (FailureKind.UNSUPPORTED, (
        "content filter",
        "safety",
        "blocked",
        "invalid_request_error",
    )),
    # Rede de verdade.
    (FailureKind.NETWORK, (
        "connection",
        "timeout",
        "timed out",
        "unreachable",
        "resolve",
        "ssl",
        "proxy",
        "network",
        "getaddrinfo",
        "connection reset",
    )),
)

# Nomes amigáveis para a VEGA falar do provedor sem jargão de API.
PROVIDER_LABELS = {
    "openai": "OpenAI",
    "gemini": "Gemini",
    "openrouter": "OpenRouter",
    "ollama": "Ollama",
    "anthropic": "Anthropic",
}

# O que o usuário pode FAZER com cada tipo de falha.
_ACTIONS = {
    FailureKind.QUOTA_EXHAUSTED: (
        "Adicione créditos ou aumente o plano para voltar a usar este provedor."
    ),
    FailureKind.AUTH_INVALID: (
        "Confira a chave no environment/.env — ela está inválida ou revogada."
    ),
    FailureKind.MODEL_UNAVAILABLE: (
        "Escolha outro modelo no .env ou confirme se este ainda está disponível."
    ),
    FailureKind.RATE_LIMITED: (
        "Aguarde alguns instantes — é limite temporário, não falta de crédito."
    ),
    FailureKind.NETWORK: (
        "Verifique a conexão de rede deste computador."
    ),
    FailureKind.UNSUPPORTED: (
        "O pedido foi recusado pelas regras do provedor."
    ),
}


@dataclass(frozen=True)
class ProviderFailure:
    """Uma falha de provedor já classificada, com causa e ação sugerida."""

    kind: FailureKind
    provider: str
    detail: str = ""
    status_code: Optional[int] = None

    @property
    def label(self) -> str:
        return PROVIDER_LABELS.get(self.provider, self.provider)

    @property
    def is_terminal(self) -> bool:
        """Falhas definitivas: tentar o MESMO provedor de novo não adianta."""
        return self.kind in {
            FailureKind.QUOTA_EXHAUSTED,
            FailureKind.AUTH_INVALID,
            FailureKind.MODEL_UNAVAILABLE,
        }

    def suggest_action(self) -> str:
        return _ACTIONS.get(self.kind, "")

    def describe(self) -> str:
        """Frase verdadeira sobre o que aconteceu, em português natural."""
        base = f"O {self.label} falhou"
        if self.kind == FailureKind.QUOTA_EXHAUSTED:
            base = f"A conta do {self.label} está sem créditos"
        elif self.kind == FailureKind.AUTH_INVALID:
            base = f"A chave do {self.label} está inválida"
        elif self.kind == FailureKind.RATE_LIMITED:
            base = f"O {self.label} está no limite de requisições"
        elif self.kind == FailureKind.MODEL_UNAVAILABLE:
            base = f"O modelo configurado no {self.label} não está disponível"
        elif self.kind == FailureKind.NETWORK:
            base = f"Não consegui alcançar o {self.label}"
        elif self.kind == FailureKind.UNSUPPORTED:
            base = f"O {self.label} recusou este pedido"
        elif self.kind == FailureKind.CANCELLED:
            return "Resposta cancelada."

        action = self.suggest_action()
        sentence = f"{base}." if base.endswith(".") else f"{base}."
        return f"{sentence} {action}".strip()

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "kind": self.kind.value,
            "status_code": self.status_code,
            "detail": self.detail[:300],
            "message": self.describe(),
        }


def _payload_text(error) -> str:
    """Extrai texto pesquisável de uma exceção, inclusive JSON aninhado.

    Cobre as três formas que os SDKs usam na prática: resposta HTTP
    (`error.response`), corpo estruturado (`error.body`) e atributos soltos
    (`error.message` / `error.code` / `error.status`), que é como o SDK do
    Gemini entrega `RESOURCE_EXHAUSTED`.
    """
    parts = [str(error)]

    for attribute in ("message", "code", "status", "type", "detail"):
        value = getattr(error, attribute, None)
        if value not in (None, ""):
            parts.append(str(value))

    response = getattr(error, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            parts.append(str(status))
        for attribute in ("text", "content"):
            value = getattr(response, attribute, None)
            if value:
                try:
                    parts.append(value.decode("utf-8", "ignore") if isinstance(value, bytes) else str(value))
                except Exception:
                    pass

    body = getattr(error, "body", None)
    if body is not None:
        try:
            parts.append(json.dumps(body, default=str))
        except Exception:
            parts.append(str(body))

    return " ".join(part for part in parts if part).lower()


def _status_code(error) -> Optional[int]:
    for attribute in ("status_code", "code", "http_status"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def classify_provider_error(error, provider: str) -> ProviderFailure:
    """Classifica a falha de um provedor pela causa real, não pelo provedor.

    Aceita `ProviderFailure` já pronto (idempotente) para que camadas
    superiores possam reclassificar sem perder informação.
    """
    if isinstance(error, ProviderFailure):
        return error

    text = _payload_text(error)
    status = _status_code(error)

    kind = FailureKind.UNKNOWN
    for candidate, signatures in _SIGNATURES:
        if any(signature in text for signature in signatures):
            kind = candidate
            break

    # Sem assinatura explícita, o status HTTP ainda diz muito.
    if kind == FailureKind.UNKNOWN and status is not None:
        if status == 402:
            kind = FailureKind.QUOTA_EXHAUSTED
        elif status == 429:
            # 429 da OpenAI sem texto é quase sempre cota; o Gemini usa para
            # limite de requisições. O texto já foi checado acima, então aqui
            # o mais provável é limite temporário.
            kind = FailureKind.RATE_LIMITED
        elif status in (401, 403):
            kind = FailureKind.AUTH_INVALID
        elif status == 404:
            kind = FailureKind.MODEL_UNAVAILABLE
        elif status >= 500:
            kind = FailureKind.NETWORK

    return ProviderFailure(
        kind=kind,
        provider=provider,
        detail=str(error),
        status_code=status,
    )
