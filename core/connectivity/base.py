# -*- coding: utf-8 -*-
"""Contrato base dos conectores de dispositivo da VEGA.

Um conector sabe:

- se está **disponível** (ferramenta externa presente no sistema);
- como **descobrir** dispositivos (opcional);
- como **executar** uma ação nomeada (abrir, tocar, enviar...).

Nenhum método levanta exceção para fora: erros viram `ConnectorResult`
com `ok=False` e uma mensagem pronta para ser falada.
"""

from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Optional


def fold_text(value) -> str:
    """Normaliza texto para comparação: minúsculas, sem acento, sem pontuação.

    Utilitário compartilhado — antes cada módulo de conectividade tinha a
    sua própria cópia desta função.
    """
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def run_external(args, timeout=12, runner=None):
    """Executa um comando externo com segurança. Retorna (ok, stdout, stderr).

    `runner` permite injetar um executor falso em testes, sem tocar no
    sistema real. Nunca levanta: falhas viram tuplas com ok=False.
    """
    if runner is not None:
        return runner(args)

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, "", "tempo esgotado"
    except (OSError, ValueError) as error:
        return False, "", str(error)

    return (
        completed.returncode == 0,
        (completed.stdout or "").strip(),
        (completed.stderr or "").strip(),
    )


@dataclass
class ConnectorResult:
    """Resultado uniforme de qualquer operação de conectividade."""

    ok: bool
    message: str = ""
    data: Any = None

    @classmethod
    def success(cls, message: str = "", data: Any = None) -> "ConnectorResult":
        return cls(ok=True, message=message, data=data)

    @classmethod
    def failure(cls, message: str) -> "ConnectorResult":
        return cls(ok=False, message=message)


@dataclass
class ConnectorStatus:
    """Estado de disponibilidade de um conector."""

    name: str
    available: bool
    detail: str = ""
    install_hint: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "available": self.available,
            "detail": self.detail,
            "install_hint": self.install_hint,
        }


class Connector:
    """Base dos conectores. Subclasses declaram `name` e `install_hint`."""

    name: str = "connector"
    install_hint: str = ""

    def __init__(self, registry=None, runner: Optional[Callable] = None):
        # `runner` permite injetar um executor de comandos (usado em testes
        # para não tocar no sistema real).
        self.registry = registry
        self._runner = runner
        self._actions: dict[str, Callable[..., ConnectorResult]] = {}
        self._register_actions()

    # --------------------------------------------------------------
    # Disponibilidade
    # --------------------------------------------------------------

    def status(self) -> ConnectorStatus:
        """Sobrescrever nas subclasses. Padrão: indisponível."""
        return ConnectorStatus(
            name=self.name,
            available=False,
            detail="Conector não implementado.",
            install_hint=self.install_hint,
        )

    def is_available(self) -> bool:
        try:
            return bool(self.status().available)
        except Exception:
            return False

    def unavailable_message(self) -> str:
        status = self.status()
        if status.install_hint:
            return f"{status.detail} {status.install_hint}".strip()
        return status.detail or f"O recurso {self.name} não está disponível."

    # --------------------------------------------------------------
    # Ações
    # --------------------------------------------------------------

    def _register_actions(self):
        """Subclasses registram handlers aqui (nome -> callable)."""
        self._actions = {}

    def register_action(self, name: str, handler: Callable[..., ConnectorResult]):
        self._actions[name] = handler

    def can_handle(self, action: str) -> bool:
        return action in self._actions

    def validate(self, action: str, **kwargs) -> str:
        """Pré-validação de argumentos obrigatórios.

        Subclasses sobrescrevem para reportar erros de uso ANTES de checar
        a disponibilidade — assim "qual conteúdo?" vence "instale o
        pychromecast" quando o usuário simplesmente não disse o que tocar.
        Retorna uma mensagem de erro, ou string vazia se estiver tudo certo.
        """
        return ""

    def execute(self, action: str, **kwargs) -> ConnectorResult:
        """Executa uma ação com segurança total (nunca levanta).

        Ordem: ação existe -> argumentos válidos -> ferramenta disponível.
        Isso garante a mensagem mais específica para o que o usuário pediu.
        """
        handler = self._actions.get(action)
        if handler is None:
            return ConnectorResult.failure(
                f"Ação '{action}' não é suportada por {self.name}."
            )

        problem = self.validate(action, **kwargs)
        if problem:
            return ConnectorResult.failure(problem)

        if not self.is_available():
            return ConnectorResult.failure(self.unavailable_message())

        try:
            result = handler(**kwargs)
        except TypeError as error:
            return ConnectorResult.failure(
                f"Argumentos inválidos para {action}: {error}"
            )
        except Exception as error:
            return ConnectorResult.failure(
                f"Falha ao executar {action}: {error}"
            )

        if isinstance(result, ConnectorResult):
            return result
        if isinstance(result, str):
            return ConnectorResult.success(result)
        return ConnectorResult.success(str(result) if result is not None else "")

    # --------------------------------------------------------------
    # Descoberta (opcional)
    # --------------------------------------------------------------

    def discover(self) -> list:
        """Retorna dispositivos encontrados (lista de Device). Padrão: vazio."""
        return []

    def actions(self) -> list:
        return sorted(self._actions.keys())