# -*- coding: utf-8 -*-
"""Testes do diagnóstico honesto de falhas dos provedores de IA.

O contrato central: a MESMA exceção genérica ("erro no provedor") precisa ser
classificada pela causa real, porque é isso que permite a VEGA dizer "a conta
da OpenAI está sem créditos" em vez de "tente novamente em alguns segundos".
"""

import pytest

from core.brain.provider_health import (
    FailureKind,
    ProviderFailure,
    classify_provider_error,
)


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakeHttpError(Exception):
    """Imita o erro do SDK da OpenAI, que carrega a resposta HTTP."""

    def __init__(self, message, status_code=None, body=None):
        super().__init__(message)
        self.body = body
        self.response = FakeResponse(status_code) if status_code else None


class TestClassificacaoPorTexto:
    def test_openai_sem_credito_e_cota_esgotada(self):
        # Esta é exatamente a resposta que a conta do Endriw devolveu no teste
        # real: 429 com credit_balance_exhausted.
        error = FakeHttpError(
            "Error code: 429 - {'error': {'message': 'You have no credits "
            "remaining.', 'code': 'credit_balance_exhausted'}}",
            status_code=429,
        )
        failure = classify_provider_error(error, "openai")
        assert failure.kind == FailureKind.QUOTA_EXHAUSTED
        assert failure.is_terminal is True

    def test_gemini_quota_excedida(self):
        error = FakeHttpError(
            "429 You exceeded your current quota, please check your plan",
            status_code=429,
        )
        failure = classify_provider_error(error, "gemini")
        assert failure.kind == FailureKind.QUOTA_EXHAUSTED

    def test_rate_limit_temporario_nao_e_confundido_com_cota(self):
        error = FakeHttpError("429 rate limit exceeded, try again later", status_code=429)
        failure = classify_provider_error(error, "openrouter")
        assert failure.kind == FailureKind.RATE_LIMITED
        assert failure.is_terminal is False

    def test_chave_invalida(self):
        error = FakeHttpError("401 Incorrect API key provided", status_code=401)
        failure = classify_provider_error(error, "openai")
        assert failure.kind == FailureKind.AUTH_INVALID
        assert failure.is_terminal is True

    def test_modelo_inexistente(self):
        error = FakeHttpError("404 The model `gpt-9` does not exist", status_code=404)
        failure = classify_provider_error(error, "openai")
        assert failure.kind == FailureKind.MODEL_UNAVAILABLE

    def test_rede(self):
        failure = classify_provider_error(
            ConnectionError("HTTPSConnectionPool: connection timed out"), "gemini"
        )
        assert failure.kind == FailureKind.NETWORK


class TestClassificacaoPorStatus:
    """Sem texto legível, o status HTTP ainda precisa classificar."""

    def test_402_e_cota(self):
        error = FakeHttpError("payment required", status_code=402)
        assert classify_provider_error(error, "openai").kind == FailureKind.QUOTA_EXHAUSTED

    def test_403_e_autenticacao(self):
        error = FakeHttpError("forbidden", status_code=403)
        assert classify_provider_error(error, "gemini").kind == FailureKind.AUTH_INVALID

    def test_404_e_modelo(self):
        error = FakeHttpError("not found", status_code=404)
        assert classify_provider_error(error, "openai").kind == FailureKind.MODEL_UNAVAILABLE

    def test_500_e_rede(self):
        error = FakeHttpError("internal error", status_code=500)
        assert classify_provider_error(error, "openai").kind == FailureKind.NETWORK


class TestMensagemHonesta:
    def test_frase_de_cota_nao_fala_em_tentar_de_novo(self):
        error = FakeHttpError(
            "'code': 'credit_balance_exhausted'", status_code=429
        )
        message = classify_provider_error(error, "openai").describe()
        assert "OpenAI" in message
        assert "créditos" in message.lower()
        # O ponto do módulo: não culpar a rede por falta de pagamento.
        assert "tente novamente" not in message.lower()

    def test_frase_de_rede_manda_verificar_conexao(self):
        message = classify_provider_error(TimeoutError("timed out"), "gemini").describe()
        assert "Gemini" in message
        assert "rede" in message.lower() or "conexão" in message.lower()

    def test_cancelamento_nao_sugere_acao(self):
        failure = ProviderFailure(kind=FailureKind.CANCELLED, provider="openai")
        assert failure.describe() == "Resposta cancelada."


class TestRobustezEIdempotencia:
    def test_reclassificar_uma_falha_ja_pronta_nao_perde_dados(self):
        original = ProviderFailure(
            kind=FailureKind.QUOTA_EXHAUSTED, provider="openai", status_code=429
        )
        again = classify_provider_error(original, "gemini")
        # O provedor original é preservado: a falha já sabia de onde veio.
        assert again is original
        assert again.provider == "openai"

    def test_erro_desconhecido_nao_estoura(self):
        failure = classify_provider_error(ValueError("algo estranho"), "openai")
        assert failure.kind == FailureKind.UNKNOWN
        assert isinstance(failure.describe(), str)

    def test_erro_sem_nada_pesquisavel(self):
        failure = classify_provider_error(Exception(), "gemini")
        assert failure.kind == FailureKind.UNKNOWN

    def test_to_dict_serializa_para_a_ia(self):
        error = FakeHttpError("'code': 'credit_balance_exhausted'", status_code=429)
        payload = classify_provider_error(error, "openai").to_dict()
        assert payload["provider"] == "openai"
        assert payload["kind"] == "quota_exhausted"
        assert "message" in payload

    def test_detail_e_truncado_para_nao_inflar_o_prompt(self):
        failure = classify_provider_error(Exception("x" * 5000), "openai")
        assert len(failure.to_dict()["detail"]) <= 300
