# -*- coding: utf-8 -*-
"""Ligação dos orquestradores no VegaBrain.

Regressão real: `VegaBrain.__init__` criava o `ConversationOrchestrator` e,
logo depois, sobrescrevia `self.orchestrator` com o `AgentOrchestrator`.
Resultado: QUALQUER fala morria com

    'AgentOrchestrator' object has no attribute 'classify_turn_intent'

...inclusive um simples "não" respondendo a uma pergunta da VEGA.

Estes testes garantem que os dois orquestradores coexistem, cada um no seu
lugar, sem que um apague o outro.
"""

import pytest


@pytest.fixture
def brain():
    from core.brain.brain import VegaBrain
    return VegaBrain()


class TestLigacaoDosOrquestradores:
    def test_orchestrator_e_o_conversacional(self, brain):
        from core.orchestration import ConversationOrchestrator
        assert isinstance(brain.orchestrator, ConversationOrchestrator)

    def test_orchestrator_tem_o_metodo_que_o_stream_usa(self, brain):
        # É exatamente o método que faltava e derrubava toda fala.
        assert callable(getattr(brain.orchestrator, "classify_turn_intent", None))

    def test_agent_orchestrator_continua_disponivel(self, brain):
        from core.brain.agent_framework import AgentOrchestrator
        assert isinstance(brain.agent_orchestrator, AgentOrchestrator)

    def test_orquestradores_sao_objetos_diferentes(self, brain):
        assert brain.orchestrator is not brain.agent_orchestrator


class TestFalasSimplesNaoQuebram:
    """O caminho que o usuário percorre no dia a dia."""

    @pytest.mark.parametrize("fala", [
        "não",
        "nao",
        "sim",
        "ok",
        "talvez",
        "deixa pra lá",
        "oi",
    ])
    def test_fala_curta_responde_sem_excecao(self, brain, fala):
        response = "".join(str(chunk) for chunk in brain.process_stream(fala) if chunk)
        assert response.strip()
        # Nenhum erro interno pode vazar para o usuário.
        assert "object has no attribute" not in response
        assert "Erro processando" not in response

    def test_negativa_apos_desafio_nao_explode(self, brain):
        """"não" logo depois de uma pergunta de segurança."""
        brain.phone_guard.set_pending("responde o miguel", device_serial="ABC")
        response = "".join(
            str(chunk) for chunk in brain.process_stream("não") if chunk
        )
        assert response.strip()
        assert "object has no attribute" not in response

    def test_fluxo_de_comando_usa_o_agent_orchestrator(self, brain, monkeypatch):
        """Tarefa complexa continua sendo tratada pelo orquestrador de agentes."""
        monkeypatch.setattr(
            brain.agent_orchestrator, "is_complex_task", lambda text: True
        )
        monkeypatch.setattr(
            brain.agent_orchestrator, "handle_task", lambda text: "plano do agente"
        )
        assert brain.process("implemente um sistema de login no projeto") == "plano do agente"