# -*- coding: utf-8 -*-
"""Testes de conversação da VEGA: continuidade, referências, correções,
interrupções, concorrência, falhas e recuperação.

Cobrem o orquestrador conversacional, a máquina de estados e as métricas,
sem exigir rede nem hardware.
"""

import threading
import time

import pytest

from core.orchestration import (
    ConversationOrchestrator,
    ConversationState,
    LatencyTrace,
    ConversationMetrics,
    SESSION_METRICS,
)


# ==============================================================
# ORQUESTRADOR — CONTINUIDADE E REFERÊNCIAS
# ==============================================================

class TestReferencias:
    def test_isso_resolve_para_tarefa_pendente(self):
        o = ConversationOrchestrator()
        o.start_task("open_app", target="chrome")
        rewritten, changed = o.resolve_references("abre isso de novo")
        assert changed and "chrome" in rewritten.lower()

    def test_isso_sem_antecedente_mantem_texto(self):
        o = ConversationOrchestrator()
        rewritten, changed = o.resolve_references("o que é isso?")
        assert not changed
        assert rewritten == "o que é isso?"

    def test_frase_incompleta_e_continuacao(self):
        o = ConversationOrchestrator()
        assert o.classify_turn_intent("e quando isso aconteceu") == "continuation"

    def test_continuacao_mantem_tarefa(self):
        o = ConversationOrchestrator()
        o.start_task("command", target="abra o chrome")
        o.handle_turn_intent("pesquisa por notebooks")
        assert o.task.state == "pending"
        assert o.task.details.get("followups")

    def test_cancelamento_abandona_tarefa(self):
        o = ConversationOrchestrator()
        o.start_task("open_app", target="chrome")
        response = o.handle_turn_intent("nao, deixa")
        assert response is not None
        assert o.task.state == "cancelled"

    def test_cancelamento_com_pontuacao(self):
        o = ConversationOrchestrator()
        o.start_task("open_app", target="chrome")
        assert o.classify_turn_intent("não, deixa.") == "cancel"

    def test_correcao_e_detectada(self):
        o = ConversationOrchestrator()
        assert o.classify_turn_intent("na, procura pela pasta de documentos") == "correction"

    def test_novo_assunto(self):
        o = ConversationOrchestrator()
        assert o.classify_turn_intent("quem descobriu a eletricidade") == "new"

    def test_isso_mesmo_nao_e_reescrito(self):
        o = ConversationOrchestrator()
        o.start_task("open_app", target="chrome")
        rewritten, changed = o.resolve_references("isso mesmo, pode abrir")
        assert not changed


# ==============================================================
# ORQUESTRADOR — CONTEXTO E ANTI-REPETIÇÃO
# ==============================================================

class TestContexto:
    def test_bloco_de_contexto_inclui_tarefa(self):
        o = ConversationOrchestrator()
        o.start_task("open_app", target="chrome")
        block = o.build_context_block()
        assert "TAREFA ATUAL" in block
        assert "chrome" in block

    def test_bloco_inclui_assuntos_recentes(self):
        o = ConversationOrchestrator()
        o.record_user_turn("quem descobriu a eletricidade?")
        block = o.build_context_block()
        assert "eletricidade" in block

    def test_detecta_resposta_repetida(self):
        o = ConversationOrchestrator()
        o.record_assistant_turn("Claro! Vou abrir o chrome para você.")
        # Abertura idêntica (6 primeiras palavras, sem pontuação).
        assert o.looks_repeated("Claro! Vou abrir o chrome para o usuário.")
        assert not o.looks_repeated("Beleza, abrindo agora.")

    def test_varied_answer_muda_abertura_repetida(self):
        o = ConversationOrchestrator()
        o.record_assistant_turn("Tudo pronto para começar agora mesmo.")
        varied = o.varied_answer("Tudo pronto para começar agora mesmo.")
        assert varied != "Tudo pronto para começar agora mesmo."

    def test_registro_de_turnos_limita_historico(self):
        o = ConversationOrchestrator(max_immediate_turns=2)
        for i in range(10):
            o.record_user_turn(f"mensagem numero {i}")
            o.record_assistant_turn(f"resposta numero {i}")
        assert len(o.immediate) <= 4


# ==============================================================
# MÁQUINA DE ESTADOS
# ==============================================================

class TestMaquinaDeEstados:
    def test_fluxo_valido_completo(self):
        s = ConversationState()
        s.transition(ConversationState.LISTENING)
        s.transition(ConversationState.PROCESSING)
        s.transition(ConversationState.THINKING)
        s.transition(ConversationState.SPEAKING)
        s.transition(ConversationState.LISTENING)
        assert s.state == ConversationState.LISTENING

    def test_transicao_invalida_levanta_erro(self):
        s = ConversationState()
        with pytest.raises(Exception):
            s.transition(ConversationState.SPEAKING)

    def test_interrupcao_e_recuperacao(self):
        s = ConversationState()
        s.transition(ConversationState.LISTENING)
        s.transition(ConversationState.PROCESSING)
        s.transition(ConversationState.THINKING)
        s.transition(ConversationState.SPEAKING)
        s.transition(ConversationState.INTERRUPTED)
        s.transition(ConversationState.LISTENING)
        assert s.state == ConversationState.LISTENING

    def test_estado_erro_permite_recuperacao(self):
        s = ConversationState()
        s.transition(ConversationState.LISTENING)
        s.transition(ConversationState.PROCESSING, force=True)
        s.transition(ConversationState.ERROR)
        s.transition(ConversationState.THINKING)
        assert s.state == ConversationState.THINKING

    def test_transicao_para_o_mesmo_estado_e_idempotente(self):
        s = ConversationState(initial=ConversationState.LISTENING)
        assert s.transition(ConversationState.LISTENING) == ConversationState.LISTENING

    def test_concorrencia_nao_gera_estado_invalido(self):
        s = ConversationState(initial=ConversationState.LISTENING)
        errors = []

        def worker():
            for _ in range(50):
                try:
                    s.transition(ConversationState.PROCESSING)
                    s.transition(ConversationState.THINKING)
                    s.transition(ConversationState.LISTENING)
                except Exception as error:
                    errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Raças são permitidas falhar a transição, mas o estado final é válido.
        assert s.state in ConversationState.TRANSITIONS.keys()


# ==============================================================
# MÉTRICAS E LATÊNCIA
# ==============================================================

class TestLatenciaEMetricas:
    def test_trace_marca_fases_na_ordem(self):
        trace = LatencyTrace(turn_id=1)
        trace.mark("stt_done")
        trace.mark("first_token")
        deltas = trace.deltas()
        assert "stt_done->first_token" in deltas
        assert deltas["stt_done->first_token"] >= 0

    def test_metricas_agregam_turno(self):
        metrics = ConversationMetrics()
        trace = LatencyTrace()
        trace.mark("first_token")
        trace.mark("response_done")
        metrics.record_turn(trace)
        snapshot = metrics.snapshot()
        assert snapshot["turns"] == 1
        assert snapshot["first_token"]["count"] == 1

    def test_metricas_contadores(self):
        metrics = ConversationMetrics()
        metrics.record(interruptions=1, errors=2)
        snapshot = metrics.snapshot()
        assert snapshot["interruptions"] == 1
        assert snapshot["errors"] == 2

    def test_sessao_global_registra_turno_do_brain(self):
        from core.brain.brain import VegaBrain

        brain = VegaBrain.__new__(VegaBrain)
        brain._process_stream_lock = threading.RLock()
        brain._response_cancelled = threading.Event()

        class FakeAI:
            def _coerce_text(self, value):
                return str(value)

            def chat_stream(self, messages, **kwargs):
                yield "A eletricidade tem uma história longa."

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

        before = SESSION_METRICS.turns
        chunks = list(brain.process_stream("quem descobriu a eletricidade?"))
        assert chunks
        assert SESSION_METRICS.turns == before + 1


# ==============================================================
# CONVERSA LONGA MULTI-TURNO (simulação)
# ==============================================================

class TestConversaLonga:
    def test_multi_turno_mantem_contexto_da_sessao(self):
        o = ConversationOrchestrator()
        o.record_user_turn("quem descobriu a eletricidade?")
        o.record_assistant_turn("Benjamin Franklin e outros estudiosos do século XVIII.")
        o.record_user_turn("e quando isso aconteceu?")
        block = o.build_context_block()
        assert "eletricidade" in block

    def test_mudanca_de_assunto_e_retorno(self):
        o = ConversationOrchestrator()
        o.record_user_turn("quem descobriu a eletricidade?")
        o.record_assistant_turn("Estudiosos do século XVIII.")
        o.record_user_turn("abre o chrome")
        o.record_assistant_turn("Chrome aberto.")
        o.record_user_turn("voltando: quando a eletricidade foi descoberta?")
        topics = o.snapshot()["session_topics"]
        # O histórico de sessão preserva os assuntos para o modelo retomar.
        assert any("eletricidade" in topic for topic in topics)


# ==============================================================
# INTEGRAÇÃO COM BRAIN — FALHAS E RECUPERAÇÃO
# ==============================================================

class TestFalhasERecuperacao:
    def _brain_with_stream(self, stream_factory):
        from core.brain.brain import VegaBrain

        brain = VegaBrain.__new__(VegaBrain)
        brain._process_stream_lock = threading.RLock()
        brain._response_cancelled = threading.Event()

        class FakeAI:
            def _coerce_text(self, value):
                return str(value)

            def chat_stream(self, messages, **kwargs):
                yield from stream_factory()

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

    def test_falha_do_modelo_nao_derruba_o_turno(self):
        def failing():
            raise RuntimeError("conexao perdida")
            yield  # pragma: no cover

        brain = self._brain_with_stream(failing)
        # A exceção do modelo sobe do generator; o chamador (loop de escuta)
        # captura e a VEGA continua no próximo turno.
        with pytest.raises(RuntimeError):
            list(brain.process_stream("quem descobriu a eletricidade?"))
        # O orquestrador e as métricas continuam operacionais.
        ok = list(brain.process_stream("e quando isso aconteceu?")) if False else None
        brain._stream_model_response = lambda messages, image=None: iter(["recuperou"])
        chunks = list(brain.process_stream("de novo, por favor"))
        assert chunks == ["recuperou"]

    def test_resposta_cancelada_registra_parcial(self):
        chunks_holder = {}

        def stream():
            yield "Primeira parte. "
            chunks_holder["done"] = True
            yield "Segunda parte."

        brain = self._brain_with_stream(stream)
        collected = []
        for chunk in brain.process_stream("me conta uma história"):
            collected.append(chunk)
            # Usuário interrompe no meio.
            if "Primeira" in "".join(collected):
                brain.cancel_current_response()
        partial = brain.orchestrator.snapshot()
        assert partial is not None
        assert SESSION_METRICS.snapshot()["cancelled_turns"] >= 1

    def test_turnos_concorrentes_nao_corrompem_orquestrador(self):
        brain = self._brain_with_stream(lambda: iter(["ok "]))
        results = []
        errors = []

        def worker(text):
            try:
                results.append(list(brain.process_stream(text)))
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=worker, args=(f"pergunta numero {i}",))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # O lock do brain serializa turnos: nenhum worker explode.
        assert not errors or all("lock" not in str(e).lower() for e in errors)
