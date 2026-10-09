# -*- coding: utf-8 -*-
"""Roteador semântico: a IA entende, o código valida e executa.

Garante:
  1. o gate de custo não deixa conversa pura gastar token;
  2. paráfrases nunca previstas ("deixa o pc mais claro") viram ação;
  3. intenção inventada, bloqueada ou malformada NUNCA executa;
  4. falha da IA cai no fluxo normal (nada quebra, nada é executado);
  5. a execução usa o mesmo catálogo do parser (mesma segurança).
"""

import json

import pytest

from core.brain.semantic_router import SemanticRouter


class FakeCommands:
    def __init__(self, **overrides):
        self.calls = []
        self._overrides = overrides

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._overrides:
            return self._overrides[name]
        if name == "has_pending_homework":
            return lambda: False

        def generic(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return name

        return generic


class FakeAI:
    """IA falsa que devolve uma resposta pré-programada."""

    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error
        self.prompts = []

    def chat_stream(self, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        if self.error:
            raise RuntimeError(self.error)
        yield self.answer


@pytest.fixture
def commands():
    return FakeCommands()


@pytest.fixture
def capabilities():
    from core.actions.commands_parser import VegaCommandsParser

    parser = VegaCommandsParser(FakeCommands())
    return parser._intent_catalog.describe()


@pytest.fixture
def router_factory(capabilities):
    def factory(ai):
        return SemanticRouter(ai, capabilities)

    return factory


# ------------------------------------------------------------------
# 1. Gate de custo: conversa não roteia
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "me explica o que é um buraco negro",
    "qual a cotação do dólar",
    "oi, tudo bem?",
    "hoje o dia está bonito",
    "quando é o aniversário de você",
])
def test_conversation_never_reaches_the_model(router_factory, capabilities, text):
    ai = FakeAI(answer=json.dumps({"intent": "volume_up", "say": "x"}))
    router = router_factory(ai)
    assert router.route(text) is None
    assert ai.prompts == []  # nunca chamou o modelo


@pytest.mark.parametrize("text", [
    "deixa o pc mais claro",
    "abre aquele programa de música",
    "toca um som diferente",
    "deixa mais escuro aqui",
])
def test_action_phrasing_is_routed(router_factory, text):
    ai = FakeAI(answer=json.dumps({"intent": "brightness_up", "params": {}, "say": "Deixo mais claro."}))
    router = router_factory(ai)
    result = router.route(text)
    assert result is not None
    assert result["intent"] == "brightness_up"
    assert ai.prompts != []


# ------------------------------------------------------------------
# 2. Validação: só intenção real executa
# ------------------------------------------------------------------

def test_unknown_intent_is_rejected(router_factory):
    ai = FakeAI(answer=json.dumps({"intent": "formatar_disco", "say": "x"}))
    assert router_factory(ai).route("deixa o pc mais claro") is None


def test_blocked_intent_is_rejected(router_factory):
    ai = FakeAI(answer=json.dumps({"intent": "shell", "params": {"cmd": "rm -rf /"}, "say": "x"}))
    assert router_factory(ai).route("deixa o pc mais claro") is None


def test_malformed_json_is_rejected(router_factory):
    for bad in [
        "não é json",
        '{"intent": "volume_up", "say": "x"',  # JSON truncado
        '{"intent": volume_up}',                # aspas faltando
        "",
    ]:
        ai = FakeAI(answer=bad)
        assert router_factory(ai).route("deixa o pc mais claro") is None


def test_none_intent_keeps_normal_flow(router_factory):
    ai = FakeAI(answer=json.dumps({"intent": "none", "say": ""}))
    assert router_factory(ai).route("deixa o pc mais claro") is None


def test_params_are_coerced(router_factory):
    answer = json.dumps({
        "intent": "volume_up",
        "params": {"ok": "texto", "n": 5, "f": 1.5, "b": True, "bad": {"nested": 1}, "more": "x"},
    })
    result = router_factory(FakeAI(answer=answer)).route("deixa o pc mais claro")
    assert result is not None
    assert result["params"] == {"ok": "texto", "n": 5, "f": 1.5, "b": True}


# ------------------------------------------------------------------
# 3. Falha da IA cai no fluxo normal
# ------------------------------------------------------------------

def test_ai_failure_returns_none(router_factory):
    ai = FakeAI(error="sem rede")
    assert router_factory(ai).route("deixa o pc mais claro") is None


# ------------------------------------------------------------------
# 4. Integração com o cérebro: ação pelo mesmo executor
# ------------------------------------------------------------------

def test_brain_executes_routed_action(monkeypatch):
    from core.brain.brain import VegaBrain

    brain = VegaBrain.__new__(VegaBrain)
    brain.commands = FakeCommands()
    brain.ai = None

    from core.actions.commands_parser import VegaCommandsParser

    brain.commands_parser = VegaCommandsParser(brain.commands)

    class FakeRouter:
        def route(self, text):
            return {"intent": "volume_up", "params": {}, "say": "Mais alto."}

    monkeypatch.setattr(brain, "_semantic_router", lambda: FakeRouter())

    response = brain._route_via_semantic_router("deixa o pc mais claro")

    assert response == "Mais alto."
    assert ("volume_up", (), {}) in brain.commands.calls


def test_brain_falls_back_to_executor_say_when_model_silent(monkeypatch):
    from core.brain.brain import VegaBrain

    brain = VegaBrain.__new__(VegaBrain)
    brain.commands = FakeCommands()
    brain.ai = None

    from core.actions.commands_parser import VegaCommandsParser

    brain.commands_parser = VegaCommandsParser(brain.commands)

    class FakeRouter:
        def route(self, text):
            return {"intent": "volume_up", "params": {}, "say": ""}

    monkeypatch.setattr(brain, "_semantic_router", lambda: FakeRouter())

    response = brain._route_via_semantic_router("deixa o pc mais claro")
    assert response == "volume_up"  # resultado factual do executor


def test_brain_returns_none_when_intent_unknown(monkeypatch):
    from core.brain.brain import VegaBrain

    brain = VegaBrain.__new__(VegaBrain)
    brain.commands = FakeCommands()
    brain.ai = None

    from core.actions.commands_parser import VegaCommandsParser

    brain.commands_parser = VegaCommandsParser(brain.commands)

    class FakeRouter:
        def route(self, text):
            return {"intent": "intent_que_nao_existe", "params": {}, "say": "x"}

    monkeypatch.setattr(brain, "_semantic_router", lambda: FakeRouter())

    assert brain._route_via_semantic_router("deixa o pc mais claro") is None
    assert brain.commands.calls == []


def test_brain_router_creation_failure_is_safe(monkeypatch):
    from core.brain.brain import VegaBrain

    brain = VegaBrain.__new__(VegaBrain)
    brain.commands = FakeCommands()
    brain.ai = None

    from core.actions.commands_parser import VegaCommandsParser

    brain.commands_parser = VegaCommandsParser(brain.commands)

    def exploding():
        raise RuntimeError("sem catálogo")

    monkeypatch.setattr(brain, "_semantic_router", exploding)

    assert brain._route_via_semantic_router("deixa o pc mais claro") is None
    assert brain.commands.calls == []