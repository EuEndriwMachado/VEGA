# -*- coding: utf-8 -*-
"""Catálogo de intenções do parser de comandos da VEGA.

Por que existe
--------------
O parser reconhece a fala do usuário e a traduz em uma capacidade executável.
Isso é o "ouvido" da VEGA: determinístico, testável, offline e de custo zero.
O que estava errado não era existir — era a FORMA: ~90 reconhecedores escritos
como uma sequência de `if self._is_X_request(...)`, cada frase nova exigindo um
`if` e várias regex novas na mão.

Aqui cada capacidade é um DADO (`Rule`), não um `if`:

    Rule(
        name="volume_up",
        resolver=lambda ctx: ctx.contains("aumenta", "volume"),
        handler=lambda ctx: ctx.commands.volume_up(),
    )

Adicionar uma frase passa a ser adicionar um item ao catálogo, não escrever
lógica nova. O comportamento permanece idêntico ao anterior: as regras são
avaliadas na mesma ordem de prioridade, e a resolução continua local.

Ordem importa
-------------
As regras são avaliadas em ordem de prioridade (menor número primeiro) e a
primeira que casar vence. Isso preserva a intenção dos antigos comentários do
parser: por exemplo, "aumenta o volume da tv" precisa ser resolvido pelo
roteador de conectividade ANTES do volume genérico do PC — e
"fecha o programa Spotify" precisa de app nomeado ANTES do fechar janela
genérico.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


# Faixas de prioridade. Valores menores são avaliados primeiro; o espaço entre
# elas permite inserir regras novas sem renumerar as existentes.
PRIORITY_FIRST = 0        # segurança, cancelamento: precisam vir antes de tudo
PRIORITY_CONNECTIVITY = 20  # celular / TV antes de volume e mídia genéricos
PRIORITY_SPECIFIC = 40    # alvos nomeados (planilha "X", app "Y", playlist "Z")
PRIORITY_SYSTEM = 60      # ações de sistema (volume, brilho, janelas, teclas)
PRIORITY_APPS = 80        # abrir/fechar programas
PRIORITY_SEARCH = 90      # pesquisa web
PRIORITY_FALLBACK = 100   # reconhecedores genéricos, por último


@dataclass
class Rule:
    """Uma capacidade reconhecível na fala.

    name:     identificador estável (usado em testes e diagnóstico).
    resolver: recebe o contexto e devolve True quando a regra se aplica.
    handler:  recebe o contexto e devolve a resposta (executa a capacidade).
    priority: ordem de avaliação; menor primeiro.
    domain:   agrupamento legível (sistema, midia, apps, conectividade...).
    """

    name: str
    resolver: Callable[["IntentContext"], bool]
    handler: Callable[["IntentContext"], Any]
    priority: int = PRIORITY_SYSTEM
    domain: str = "geral"

    def matches(self, context: "IntentContext") -> bool:
        try:
            return bool(self.resolver(context))
        except Exception:
            # Uma regra defeituosa nunca deve derrubar o reconhecimento inteiro:
            # ela simplesmente não se aplica e as seguintes continuam.
            return False

    def run(self, context: "IntentContext") -> Any:
        return self.handler(context)


class IntentContext:
    """Contexto compartilhado pelas regras durante a resolução de uma fala.

    Reúne o que os reconhecedores precisam: a fala já normalizada, o texto
    original (para extrair entidades como nomes de arquivo e URLs) e o objeto
    de comandos que executa as ações.
    """

    __slots__ = ("text", "original", "commands", "parser", "extras")

    def __init__(self, text: str, original: str, commands, parser=None):
        self.text = text
        self.original = original
        self.commands = commands
        self.parser = parser
        self.extras: dict = {}

    # ------------------------------------------------------------------
    # Auxiliares usados pelos resolvedores
    # ------------------------------------------------------------------

    def contains(self, *words: str) -> bool:
        """True se QUALQUER uma das palavras/frases aparecer na fala.

        Delega ao parser real (`_contains_any`) para manter exatamente a mesma
        semântica de fronteira de palavra já testada.
        """
        if self.parser is not None:
            return bool(self.parser._contains_any(self.text, words))
        return any(word in self.text for word in words if word)

    def equals(self, *phrases: str) -> bool:
        """True se a fala for exatamente uma das frases."""
        return self.text in phrases

    def has(self, name: str) -> bool:
        """True se o objeto de comandos tiver a capacidade (evita AttributeError).

        O parser também roda em modo "dry-run" (interpret) e em testes com
        comandos falsos; uma capacidade ausente deve virar "não se aplica",
        nunca estourar.
        """
        return callable(getattr(self.commands, name, None))

    def call(self, name: str, *args, **kwargs) -> Any:
        """Invoca uma capacidade do objeto de comandos pelo nome."""
        function = getattr(self.commands, name, None)
        if not callable(function):
            return None
        return function(*args, **kwargs)


class IntentCatalog:
    """Conjunto ordenado de regras, avaliado por prioridade."""

    def __init__(self):
        self._rules: List[Rule] = []

    def add(self, rule: Rule) -> Rule:
        self._rules.append(rule)
        return rule

    def extend(self, rules) -> None:
        for rule in rules:
            self.add(rule)

    def rules(self) -> List[Rule]:
        return list(self._rules)

    def sorted_rules(self) -> List[Rule]:
        """Regras em ordem de avaliação (prioridade, depois ordem de registro)."""
        return sorted(self._rules, key=lambda rule: rule.priority)

    def resolve(self, context: IntentContext):
        """Devolve (regra, resultado) da primeira regra aplicável, ou (None, None).

        Quando a regra casa mas o handler devolve None, continua para a próxima:
        é assim que os antigos reconhecedores se comportavam quando a intenção
        dependia de um alvo que não pôde ser extraído.
        """
        for rule in self.sorted_rules():
            if not rule.matches(context):
                continue
            result = rule.run(context)
            if result is not None:
                return rule, result
        return None, None

    def describe(self) -> List[dict]:
        """Inventário legível do catálogo (diagnóstico e documentação)."""
        return [
            {
                "name": rule.name,
                "domain": rule.domain,
                "priority": rule.priority,
            }
            for rule in self.sorted_rules()
        ]