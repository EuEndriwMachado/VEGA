# -*- coding: utf-8 -*-
"""Testes do caminho opcional de narração factual de ações locais."""

from core.brain.action_narration import narrate_action_result
class FakeAI:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def _is_failure_response(self, text):
        return "sem créditos" in str(text).lower()

    backend = "gemini"

    def chat_stream(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return iter(self.response or [])


def test_disabled_mode_keeps_legacy_result_without_calling_ai():
    ai = FakeAI(response=["ignored"])
    result = "Volume aumentado para 40%."
    assert narrate_action_result(ai, "aumenta o volume", result, enabled=False) == result
    assert ai.calls == []


def test_generated_mode_flag_enables_narration(monkeypatch):
    import config.cognitive as cognitive
    from core.brain import action_narration
    monkeypatch.setenv("VEGA_COGNITIVE_MODE", "generated")
    assert cognitive.mode_is_generated() is True
    ai = FakeAI(response=["O aplicativo escolhido já está aberto na tela."])
    assert action_narration.narrate_action_result(ai, "abre paint", "O Paint abriu.") == "O aplicativo escolhido já está aberto na tela."


def test_enabled_mode_allows_rewording_factual_result():
    ai = FakeAI(response=["O volume subiu para 40%."])
    result = narrate_action_result(
        ai, "aumenta o volume", "Volume aumentado para 40%.", enabled=True
    )
    assert result == "O volume subiu para 40%."


def test_enabled_mode_does_not_allow_unsupported_success_claim():
    ai = FakeAI(response=["Pronto, salvei o arquivo."])
    factual_result = "Nao foi possivel abrir o arquivo: acesso negado."
    result = narrate_action_result(
        ai, "organize o arquivo", factual_result, enabled=True
    )
    assert result == factual_result
    messages, kwargs = ai.calls[0]
    assert "organize o arquivo" in messages[0]["content"]
    assert "acesso negado" in messages[0]["content"]
    assert "não altere se a ação teve sucesso ou falhou" in kwargs["system_prompt"]
    assert kwargs["include_screen"] is False
    assert kwargs["num_predict"] <= 160

def test_empty_ai_output_keeps_factual_local_result():
    ai = FakeAI(response=[None, "", "   "])
    result = "O Paint abriu."
    assert narrate_action_result(ai, "abre paint", result, enabled=True) == result

def test_ai_exception_keeps_factual_local_result():
    ai = FakeAI(error=RuntimeError("offline"))
    result = "A TV foi pausada."
    assert narrate_action_result(ai, "pausa a TV", result, enabled=True) == result

def test_provider_error_text_does_not_erase_successful_local_result():
    ai = FakeAI(response=["Não consegui responder agora. Tente novamente em alguns segundos."])
    result = narrate_action_result(ai, "abre paint", "O Paint abriu.", enabled=True)
    assert result == "O Paint abriu."
