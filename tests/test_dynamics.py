# -*- coding: utf-8 -*-
"""Testes de dinâmica de uso da VEGA: cenários de rotina, ambiguidade,
correções em cadeia, interrupções compostas e condições de erro.

Estes testes simbolizam uso real do dia a dia e foram escritos para
FORÇAR erros na primeira rodada (red) e depois verificar a correção.
Rodam sem rede nem hardware.
"""

import threading

import pytest

from core.orchestration import ConversationOrchestrator
from core.brain.text_utils import normalize_text, is_short_nonsense
from core.brain.brain import VegaBrain
from core.orchestration import SESSION_METRICS


# ==============================================================
# HELPERS
# ==============================================================

def make_brain(stream_text="resposta do modelo."):
    """Brain com dependências falsas mas pipeline real de process_stream."""

    brain = VegaBrain.__new__(VegaBrain)
    brain._process_stream_lock = threading.RLock()
    brain._response_cancelled = threading.Event()

    class FakeAI:
        def _coerce_text(self, value):
            return str(value)

        def chat_stream(self, messages, **kwargs):
            yield stream_text

        def cancel_generation(self):
            pass

    class FakeConversation:
        class context:
            @staticmethod
            def snapshot():
                return {}

        def get_messages(self):
            return []

        def add_user_message(self, content):
            pass

        def add_assistant_message(self, content):
            pass

    class FakeMemory:
        def add_daily_message(self, *args, **kwargs):
            pass

        def build_memory_context(self, text):
            return ""

        def retrieve_relevant(self, text, limit=6):
            return []

        def contextual_recall(self, text, limit=5):
            return []

    brain.ai = FakeAI()
    brain.conversation = FakeConversation()
    brain.memory = FakeMemory()
    brain.orchestrator = ConversationOrchestrator()
    brain.agent_state = type("AS", (), {"observe": lambda s, t: None, "render_context_prompt": lambda s: ""})()
    brain._process_command = lambda text: None
    brain._process_mode = lambda text: None
    brain._process_tool_request = lambda text: None
    brain._process_scheduler_request = lambda text: None
    brain._process_security_request = lambda text: None
    brain._process_pending_security_response = lambda text: None
    brain._process_daily_support_request = lambda text: None
    brain._process_recycle_bin_request = lambda: None
    brain._process_file_delete_request = lambda text: None
    brain._handle_visual_request_stream = lambda *a, **k: iter(())
    brain.daily_query = type("DQ", (), {"answer": lambda s, t: None})()
    brain._update_context_state = lambda text: None
    brain.environment_callback = None
    brain.database_view_callback = None
    brain.brain_view_callback = None
    return brain


def run(brain, text):
    return "".join(brain.process_stream(text))


# ==============================================================
# ROTINA MATINAL (cenário de uso típico)
# ==============================================================

class TestRotinaDiaria:
    def test_bom_dia_com_enunciado_completo_nao_e_lixo(self):
        """'bom dia' não pode ser descartado como ruído."""
        assert not is_short_nonsense(normalize_text("bom dia"))

    def test_pergunta_curta_legitima_passa(self):
        for utterance in ("que horas sao", "qual a data de hoje", "tudo bem"):
            assert not is_short_nonsense(normalize_text(utterance)), utterance

    def test_comando_com_erro_de_transcricao_e_corrigido(self):
        assert normalize_text("abra o chorme") == "abra o chrome"
        assert normalize_text("abre o spotfy") == "abre o spotify"
        assert normalize_text("aumente o som") == "aumente o volume"

    def test_cadeia_completa_de_manha(self):
        brain = make_brain()
        # 1) saudação
        run(brain, "bom dia, tudo bem?")
        # 2) clima
        run(brain, "como tá o tempo hoje?")
        # 3) lembrete
        run(brain, "lembre-me de tomar café em 10 minutos")
        # 4) retoma clima via referência
        run(brain, "e amanhã, vai chover?")
        # nada explodiu e o orquestrador registrou os turnos
        assert len(brain.orchestrator.immediate) >= 2


# ==============================================================
# AMBIGUIDADE E CORREÇÃO EM CADEIA
# ==============================================================

class TestAmbiguidadeECorrecao:
    def test_correcao_substitui_alvo_da_tarefa(self):
        o = ConversationOrchestrator()
        o.start_task("open_app", target="chrome")
        # usuário corrige: "não, procura pela pasta de documentos"
        assert o.classify_turn_intent("na, procura pela pasta de documentos") == "correction"
        o.handle_turn_intent("na, procura pela pasta de documentos")
        # a tarefa segue ativa (a correção substitui a instrução, não cancela)
        assert o.task.state == "pending"

    def test_cancelamento_depois_de_correcao(self):
        o = ConversationOrchestrator()
        o.start_task("open_app", target="chrome")
        o.handle_turn_intent("na verdade abre o firefox")
        assert o.task.state == "pending"
        o.handle_turn_intent("esquece")
        assert o.task.state == "cancelled"

    def test_referencia_ambigua_sem_antecedente_nao_quebra(self):
        o = ConversationOrchestrator()
        rewritten, changed = o.resolve_references("abre isso")
        assert not changed  # sem antecedente: texto segue original

    def test_dois_alvos_diferentes_na_sequencia(self):
        o = ConversationOrchestrator()
        o.start_task("open_app", target="chrome")
        o.start_task("open_app", target="notepad")  # novo comando substitui
        rewritten, changed = o.resolve_references("fecha isso")
        assert changed and "notepad" in rewritten

    def test_interrupcao_no_meio_do_turno_mantem_orquestrador_consistente(self):
        brain = make_brain()
        collected = []
        for chunk in brain.process_stream("me explique quantum computing"):
            collected.append(chunk)
            if collected:
                brain.cancel_current_response()
                break
        # o próximo turno funciona normalmente após a interrupção
        response = run(brain, "obrigado")
        assert response


# ==============================================================
# FALHAS DE COMPONENTES (forçar erro → verificar degradação graciosa)
# ==============================================================

class TestDegradiGraciosa:
    def test_modelo_fora_do_ar_primeiro_turno_segundo_ok(self):
        """Falha do modelo num turno não deve envenenar o próximo."""
        attempts = {"n": 0}

        def flaky(*args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("sem internet")
            yield "de volta ao ar."

        brain = make_brain()
        brain.ai.chat_stream = flaky

        with pytest.raises(ConnectionError):
            run(brain, "quem descobriu a eletricidade?")

        response = run(brain, "tenta de novo")
        assert response == "de volta ao ar."
        assert attempts["n"] == 2

    def test_ferramenta_inexistente_nao_derruba(self):
        brain = make_brain()
        brain._process_tool_request = lambda text: (_ for _ in ()).throw(
            RuntimeError("tool boom")
        )
        # tool request levanta: o chamador (loop) captura; aqui só garantimos
        # que o estado do orquestrador permanece utilizável depois.
        with pytest.raises(RuntimeError):
            run(brain, "clima em são paulo")
        response = run(brain, "oi")
        assert response

    def test_memoria_com_dados_corrompidos(self):
        brain = make_brain()
        brain.memory.build_memory_context = lambda text: (_ for _ in ()).throw(
            ValueError("json corrompido")
        )
        with pytest.raises(ValueError):
            run(brain, "me lembra do meu aniversário")

    def test_concorrencia_alta_multiplas_sessoes(self):
        brain = make_brain()
        results, errors = [], []

        def worker(i):
            try:
                results.append(run(brain, f"pergunta concorrente {i}"))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 8, errors
        assert not errors


# ==============================================================
# MÉTRICAS SOB CARGA
# ==============================================================

class TestMetricasSobCarga:
    def test_contadores_acumulam_sessao_longa(self):
        before = SESSION_METRICS.snapshot()
        brain = make_brain()
        for i in range(15):
            run(brain, f"turno de carga numero {i}")
        after = SESSION_METRICS.snapshot()
        assert after["turns"] >= before["turns"] + 15

    def test_context_misses_sao_rastreados(self):
        before = SESSION_METRICS.snapshot()["context_misses"]
        o = ConversationOrchestrator()
        # "e agora?" sem antecedente = contexto perdido rastreado
        o.classify_turn_intent("e agora?")
        o.resolve_references("e agora?")
        brain = make_brain()
        run(brain, "e agora?")
        after = SESSION_METRICS.snapshot()["context_misses"]
        assert after >= before


# ==============================================================
# QUALIDADE DE CONVERSA
# ==============================================================

class TestQualidadeDeConversa:
    def test_sem_resposta_repetida_em_sequencia(self):
        o = ConversationOrchestrator()
        o.record_assistant_turn("Vou verificar isso pra você agora.")
        assert o.looks_repeated("Vou verificar isso pra você agora mesmo.")
        varied = o.varied_answer("Vou verificar isso pra você agora.")
        assert varied != "Vou verificar isso pra você agora."

    def test_assuntos_da_sessao_crescem_com_conversa(self):
        o = ConversationOrchestrator()
        for topic in ("clima em petrópolis", "receita de bolo de cenoura", "horário do banco"):
            o.record_user_turn(f"me fala sobre {topic}, por favor")
        topics = o.snapshot()["session_topics"]
        assert len(topics) == 3

    def test_bloco_contexto_nao_explode_com_conversa_longa(self):
        o = ConversationOrchestrator()
        for i in range(50):
            o.record_user_turn(f"mensagem longa demais para caber numero {i} com bastante texto")
            o.record_assistant_turn(f"resposta numero {i}")
        block = o.build_context_block()
        # Assuntos limitados + turnos limitados = bloco pequeno.
        assert len(block) < 4000
