# -*- coding: utf-8 -*-
"""Catálogo de intenções: cada capacidade é um dado, não um `if`.

A migração converteu os ~40 blocos de `if` do parser em regras declaradas em
 três módulos (sistema, mídia/janelas/teclado, apps/busca), avaliados por
 prioridade. Estes testes garantem:
  1. o catálogo resolve as mesmas falas que os `if` antigos resolviam;
  2. a ordem de prioridade preserva as exigências dos comentários originais
     (cancelamento antes de desligar; app nomeado antes de fechar janela);
  3. regra defeituosa não derruba o reconhecimento;
  4. o parser continua devolvendo None quando nada casa.
"""

import pytest

from core.actions.commands_parser import VegaCommandsParser
from core.actions.intent_catalog import (
    IntentCatalog,
    IntentContext,
    PRIORITY_FIRST,
    PRIORITY_SYSTEM,
    Rule,
)


class FakeCommands:
    """Registra as capacidades chamadas, sem executar nada."""

    def __init__(self, **overrides):
        self.calls = []
        self._overrides = overrides

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return name

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._overrides:
            return self._overrides[name]
        # Consultas usadas pelo fluxo do parser devem responder "não" por
        # padrão; senão o diálogo de dever de casa engoliria toda a fala.
        if name == "has_pending_homework":
            return lambda: False

        def generic(*args, **kwargs):
            return self._record(name, *args, **kwargs)

        return generic


@pytest.fixture
def commands():
    return FakeCommands()


@pytest.fixture
def parser(commands):
    return VegaCommandsParser(commands)


# ------------------------------------------------------------------
# 1. Mesmas falas, mesmos executáveis
# ------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_call", [
    ("aumenta o volume", "volume_up"),
    ("diminui o volume", "volume_down"),
    ("volume no maximo", "volume_max"),
    ("muta o volume", "volume_mute"),
    ("aumenta o brilho", "brightness_up"),
    ("abaixa o brilho", "brightness_down"),
    ("brilho maximo", "brightness_max"),
    ("desligar o pc", "shutdown_pc"),
    ("reiniciar o pc", "restart_pc"),
    ("desligar a tela", "turn_off_screen"),
    ("bloquear o pc", "lock_pc"),
    ("liga o bluetooth", None),  # set_bluetooth(True)
    ("desliga o bluetooth", None),
    ("pausa a musica", "media_play_pause"),
    ("proxima musica", "media_next"),
    ("fecha a janela", "close_window"),
    ("minimiza a janela", "minimize_window"),
    ("maximiza a janela", "maximize_window"),
    ("restaura a janela", "restore_window"),
    ("copiar", "copy"),
    ("colar", "paste"),
    ("recortar", "cut"),
    ("desfazer", "undo"),
    ("salvar", "save"),
    ("abre o google", "open_google"),
    ("abre o youtube", "open_youtube"),
    ("abre o spotify", "open_spotify"),
    ("abre o whatsapp", "open_whatsapp"),
    ("abre o canva", "open_canva"),
    ("abre documentos", "open_documents"),
    ("abre downloads", "open_downloads"),
    ("abre o desktop", "open_desktop"),
])
def test_same_speech_resolves_to_same_executor(commands, parser, text, expected_call):
    # `parse` executa de verdade (registra os calls); `interpret` é dry-run.
    response = parser.parse(text)
    assert response is not None, f"fala não reconhecida: {text!r}"

    called = [name for name, _, _ in commands.calls]
    if expected_call is None:
        # bluetooth/notificações chamam set_bluetooth/set_notifications com flag.
        assert any(
            name in ("set_bluetooth", "set_notifications")
            for name in called
        )
    else:
        assert expected_call in called


# ------------------------------------------------------------------
# 2. Ordem de prioridade preservada
# ------------------------------------------------------------------

def test_cancel_before_shutdown(commands, parser):
    # "cancela o desligamento" NÃO pode desligar o PC.
    response = parser.parse("cancela o desligamento do pc")
    assert response is not None
    called = [name for name, _, _ in commands.calls]
    assert "cancel_shutdown" in called
    assert "shutdown_pc" not in called


def test_named_app_close_beats_generic_window_close(commands, parser):
    # "fecha o programa spotify" tem alvo nomeado: encerra o app, não a janela.
    response = parser.parse("fecha o programa spotify")
    assert response is not None
    called = [name for name, _, _ in commands.calls]
    assert "close_app" in called
    assert "close_window" not in called


def test_named_music_beats_generic_play(commands, parser):
    parser.parse("toca a musica ewproximo episodio")
    # Mesmo quando a extração não encontra faixa, a regra de música nomeada
    # vence o play/pause genérico.
    called = [name for name, _, _ in commands.calls]
    assert "media_play_pause" not in called


def test_shutdown_request_goes_to_shutdown(commands, parser):
    parser.parse("desligar o pc")
    called = [name for name, _, _ in commands.calls]
    assert called == ["shutdown_pc"]


# ------------------------------------------------------------------
# 3. Regra defeituosa não derruba o reconhecimento
# ------------------------------------------------------------------

def test_broken_rule_does_not_break_resolution(commands, parser):
    def exploding_resolver(context):
        raise RuntimeError("regra quebrada de propósito")

    broken = Rule(
        name="quebrada",
        resolver=exploding_resolver,
        handler=lambda ctx: "nunca",
        priority=PRIORITY_FIRST,
    )
    working = Rule(
        name="funciona",
        resolver=lambda ctx: ctx.contains("abre"),
        handler=lambda ctx: ctx.call("open_google"),
        priority=PRIORITY_FIRST + 1,
    )

    catalog = IntentCatalog()
    catalog.extend([broken, working])
    context = IntentContext("abre o google", "abre o google", commands, parser)

    rule, response = catalog.resolve(context)
    assert rule is not None and rule.name == "funciona"
    assert response == "open_google"


# ------------------------------------------------------------------
# 4. Fala desconhecida devolve None (não executa nada)
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "quero comprar um carro novo",
    "me explica o que é buraco negro",
    "hoje o dia está bonito",
    "",
    "   ",
])
def test_unmatched_speech_returns_none(parser, commands, text):
    result = parser.parse(text)
    assert result is None
    assert commands.calls == []


# ------------------------------------------------------------------
# 5. Inventário do catálogo
# ------------------------------------------------------------------

def test_catalog_inventory_is_listed_by_priority(parser):
    inventory = parser._intent_catalog.describe()
    priorities = [entry["priority"] for entry in inventory]

    assert priorities == sorted(priorities)
    # Cancelamento deve ser a primeira regra do catálogo.
    assert inventory[0]["name"] == "cancel_shutdown"
    # Domínios presentes.
    domains = {entry["domain"] for entry in inventory}
    assert {"sistema", "midia", "janelas", "teclado", "apps", "busca"} <= domains


# ------------------------------------------------------------------
# 6. Correções de transcrição ampliadas
# ------------------------------------------------------------------

@pytest.mark.parametrize("spoken,expected", [
    ("abre o chorme", "chrome"),
    ("abre o crome", "chrome"),
    ("abre o discorde", "discord"),
    ("abre o vs code", "vscode"),
])
def test_transcription_corrections_are_normalized(commands, parser, spoken, expected):
    response = parser.parse(spoken)
    assert response is not None
    # O app correto é aberto (direto ou por open_app com o nome corrigido).
    assert any(
        expected in str(args[0])
        for name, args, _ in commands.calls
        if args and name in ("open_app", "play_music", "open_chrome")
    ) or any(name in [n for n, _, _ in commands.calls] for name in ("open_google", "open_youtube", "open_spotify"))


def test_music_platform_words_are_not_treated_as_song_title(commands, parser):
    # "toca no youtube" pede música NA plataforma, não uma faixa chamada
    # "no youtube". Antes, a faixa extraída era "no you tube" (errado).
    parser.parse("toca no you tube")
    calls = dict((name, args) for name, args, _ in commands.calls)
    args = calls.get("play_music", (None,))
    query = args[0] if args else ""
    assert query is not None
    assert "you tube" not in str(query).lower()