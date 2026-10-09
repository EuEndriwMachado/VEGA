from datetime import datetime
from pathlib import Path
import json
import os
import threading

from core.brain.ai import VegaAI
from core.brain.action_narration import narrate_action_result
from core.brain.tool_receipt import build_receipt
from config.cognitive import mode_is_generated
from core.brain.conversation import Conversation
from core.memory.memory import VegaMemory
from core.actions.commands import VegaCommands
from core.actions.commands_parser import VegaCommandsParser
from core.actions.modes import VegaModes
from core.brain.text_utils import normalize_text, remove_vega_prefix, is_short_nonsense
from core.brain.time_answer import answer_time_or_date_question, portuguese_number
from core.brain.visual_triggers import classify_visual_request
from core.knowledge import KnowledgeBase
from core.brain import number_words as BrainNumberWords
from core.memory.memory_context import inject_memory
from core.brain.brain_view import is_brain_view_request
from core.memory.daily_query import DailyQuery
from config.ai import MAX_HISTORY, MAX_TOOL_OUTPUT_TOKENS
from config import file_access
from config.personality import is_serious_context, evolve_personality
from tools.api_tools import build_tool_registry
from core.actions.scheduler import VegaScheduler
from core.actions.daily_support import VegaDailySupport
from core.actions.task_workflow import TaskWorkflow
from tools.productivity import ProductivityTools
from core.security.security import VegaSecurity
from core.security.file_security import FileSecurity
from core.brain.agent_framework import AgentOrchestrator
from core.agent_state import AgentStateManager
from core.orchestration import (
    ConversationOrchestrator,
    LatencyTrace,
    SESSION_METRICS,
)
import re
import unicodedata
from datetime import timedelta


class VegaBrain:

    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        instance._response_cancelled = threading.Event()
        instance._process_stream_lock = threading.RLock()
        return instance

    def __init__(self):

        self.ai = VegaAI()

        self.memory = VegaMemory()
        # Base de conhecimento (RAG local): documentos, páginas e transcrições
        # que o usuário ingeriu. Dá à VEGA o que ela não sabia: responder sobre
        # o conteúdo de um PDF ou artigo, citando a origem.
        self.knowledge = KnowledgeBase()
        self.brain_view_callback = None
        self.database_view_callback = None
        self.daily_query = DailyQuery(self.memory)

        self.conversation = Conversation(
            max_history=MAX_HISTORY
        )
        self.agent_state = AgentStateManager(self.conversation.context)
        self.orchestrator = ConversationOrchestrator()
        self.current_trace = None
        self.daily_support = VegaDailySupport()
        self.productivity = ProductivityTools()
        self.task_workflow = TaskWorkflow(
            scheduler=None,
            mode_callback=self._activate_work_mode_from_task,
        )
        self.environment_callback = None
        self.holographic_command_callback = None
        self.tool_result_callback = None
        self.camera_frame_callback = None
        # Canvas de desenho ao vivo (Fase 3): janela própria, orbe intocado.
        self.canvas_callback = None
        self.scheduler_notification_callback = None
        self._response_cancelled = threading.Event()
        self._process_stream_lock = threading.RLock()

        # ======================================================
        # COMANDOS
        # ======================================================

        self.commands = VegaCommands(ai=self.ai, memory=self.memory)

        # Canal para o painel da interface: prints e resultados visuais
        # aparecem dentro da própria VEGA, além do visualizador do sistema.
        self.commands._result_callback = self._emit_tool_result

        self.commands_parser = VegaCommandsParser(
            self.commands
        )

        self.tool_manager = build_tool_registry(
            homework_handler=self.commands.solve_homework,
            knowledge_base=self.knowledge,
        )
        # ATENÇÃO: `self.orchestrator` é o ConversationOrchestrator (contexto,
        # referências, cancelamento e anti-repetição) — criado acima.
        # Antes, esta linha sobrescrevia ele com o AgentOrchestrator, e toda
        # fala morria em "'AgentOrchestrator' object has no attribute
        # 'classify_turn_intent'". O de agentes fica em atributo próprio.
        self.agent_orchestrator = AgentOrchestrator()
        self.last_screen_summary = None
        # Último dado verificado por ferramenta (cotação, clima...). Permite que
        # a IA responda "e por que isso?" no turno seguinte usando o valor real.
        self.last_tool_receipt = None
        # Último comando local (brilho/volume): permite "aumenta mais" resolver
        # localmente, sem IA e sem custo.
        self.last_local_command = None
        # Questions de sim/não feitas pela VEGA (ex.: "ler as notícias?").
        # O cérebro lembra da PRÓPRIA pergunta: assim a resposta funciona por
        # voz, texto ou qualquer timing — não depende do loop de captura.
        self._pending_question = None
        self.security = VegaSecurity()
        self._pending_dangerous_action = None
        self._last_opened_app = None
        self._pending_close_app = None
        self.scheduler = VegaScheduler(on_due=self._on_scheduled_item)
        self.task_workflow.scheduler = self.scheduler
        # Segurança do controle do celular: exige PIN + rosto uma vez por
        # sessão, e revoga quando o aparelho é desconectado.
        from core.connectivity.security_gate import PhoneAccessGuard
        self.phone_guard = PhoneAccessGuard(self.security)

        # Monitor de conexão: o MIUI desliga a depuração sem fio sozinho,
        # então a VEGA tenta reconectar em segundo plano.
        self.phone_monitor = None
        # Modo live (conversa em tempo real): None até ser ligado. O callback
        # de ferramenta é o gancho que faz o desenho ao vivo aparecer na tela.
        self.live_session = None
        self.live_tool_callback = self._on_live_tool_call

    # ==========================================================
    # MODO LIVE (conversa em tempo real)
    # ==========================================================
    # O modo live é ADITIVO: o pipeline de turnos continua sendo o padrão, e
    # este caminho só é usado quando ligado explicitamente. Ele não toca em
    # nada do fluxo atual — apenas abre uma sessão contínua à parte.

    def start_live_mode(self, video_mode=None, on_text=None, on_status=None):
        """Liga o modo live. Devolve True se a sessão iniciou."""
        existing = getattr(self, "live_session", None)
        if existing is not None and existing.is_running:
            return True
        try:
            from core.brain.live_session import build_live_session
            session = build_live_session(
                self, on_text=on_text, on_status=on_status, video_mode=video_mode
            )
        except Exception as error:
            print(f"[VEGA Live] Não foi possível preparar: {error}")
            return False
        self.live_session = session
        return session.start()
    def stop_live_mode(self):
        """Encerra o modo live, se estiver ativo."""
        session = getattr(self, "live_session", None)
        if session is not None:
            try:
                session.stop()
            except Exception:
                pass

    def live_mode_active(self):
        session = getattr(self, "live_session", None)
        return bool(session is not None and session.is_running)

    def _on_live_tool_call(self, name, arguments, result):
        """Reage a uma ferramenta executada DURANTE a conversa contínua.

        É o que faz a VEGA desenhar ao vivo: quando o modelo chama `draw_image`
        no meio da conversa, o desenho aparece na tela enquanto ela fala. Sem
        este gancho, o modelo poderia pedir o desenho e ele nunca seria visto.
        """
        if str(name or "") != "draw_image" or not isinstance(arguments, dict):
            return
        callback = getattr(self, "canvas_callback", None)
        if not callable(callback):
            return
        try:
            callback(str(arguments.get("title") or "Desenho"), {
                "title": str(arguments.get("title") or "Desenho"),
                "shapes": arguments.get("shapes") or [],
            })
        except Exception as error:
            print(f"[VEGA Live] Não consegui desenhar ao vivo: {error}")

    def start_phone_monitor(self, interval=90.0):
        """Liga o monitor de conexão do celular (chamado no startup da VEGA)."""
        if self.phone_monitor is not None and self.phone_monitor.is_running:
            return False
        try:
            from core.connectivity import DeviceRegistry
            from core.connectivity.monitor import PhoneConnectionMonitor
            from core.connectivity.wireless import WirelessADB

            registry = DeviceRegistry()
            monitor = PhoneConnectionMonitor(
                wireless=WirelessADB(registry=registry),
                registry=registry,
                interval=interval,
                on_event=self._on_phone_connection_event,
            )
            monitor.start()
            self.phone_monitor = monitor
            return True
        except Exception as error:
            print(f"[VEGA] Monitor do celular indisponível: {error}")
            return False

    def _on_phone_connection_event(self, result):
        """Reage a mudanças na conexão do celular (log e status)."""
        if result.get("connected"):
            print("[VEGA Celular] Reconectado sem fio.")
        else:
            print("[VEGA Celular] Desconectado. Tentarei reconectar.")
            # Sem celular: a autorização de controle cai junto.
            self.revoke_phone_access()

    def _emit_tool_result(self, name, result):
        """Envia um resultado visual para a interface (se conectada)."""
        callback = getattr(self, "tool_result_callback", None)
        if callable(callback):
            try:
                callback(name, result)
            except Exception as error:
                print(f"[VEGA] Não foi possível exibir o resultado: {error}")

    def _narrate_local_result(self, user_text, factual_result, *, spend_tokens=True):
        """Narra o fato de uma ação já concluída, sem alterar a execução."""
        return narrate_action_result(
            getattr(self, "ai", None),
            user_text,
            factual_result,
            enabled=mode_is_generated() and spend_tokens,
        )

    def _handle_task_workflow(self, text):
        workflow = getattr(self, "task_workflow", None)
        if workflow is None:
            return None
        return workflow.handle(text)

    def _activate_work_mode_from_task(self):
        try:
            return VegaModes.trabalho(self.commands)
        except Exception as error:
            print(f"[VEGA] Não consegui ativar o modo Trabalho pelo fluxo: {error}")
            return None
    def _on_scheduled_item(self, item):
        self.memory.add_daily_message("system", f"Lembrete: {item.get('text', '')}")
        callback = getattr(self, "scheduler_notification_callback", None)
        if callable(callback):
            try:
                callback(item)
            except Exception as error:
                print(f"[VEGA Lembrete] Falha ao notificar: {error}")

    @staticmethod
    def _file_changes_disabled_response():
        return (
            "Por segurança, a alteração e exclusão de arquivos pessoais estão "
            "desativadas no código. Posso criar arquivos novos, mas não alterar "
            "nem apagar os existentes."
        )

    @staticmethod
    def _is_database_view_request(text):
        if not text:
            return False

        if hasattr(VegaCommandsParser, "_is_database_request") and VegaCommandsParser._is_database_request(text):
            return True

        normalized = normalize_text(text)
        targets = (
            "banco de dados da vega",
            "banco de dados vega",
            "banco da vega",
            "banco mysql",
            "mysql da vega",
            "meu banco de dados",
            "seu banco de dados",
        )
        actions = (
            "abre", "abrir", "abra", "mostra", "mostrar", "mostre",
            "exibe", "exibir", "visualiza", "visualizar", "ver",
        )
        return any(target in normalized for target in targets) and any(
            re.search(rf"\b{re.escape(action)}\b", normalized)
            for action in actions
        )

    @staticmethod
    def _is_sensitive_command(text: str) -> bool:
        if not text:
            return False
        normalized = normalize_text(text)
        if "cancela" in normalized or "cancelar" in normalized:
            return False
        if re.search(
            r"\b(?:desliga|desligar|desligue|reinicia|reiniciar|reinicie)\b.*\b(?:pc|computador|maquina|máquina)\b",
            normalized,
        ):
            return True
        if re.search(
            r"\b(?:apaga|apague|apagar|delete|deletar|exclua|excluir|remova|remover|formata|formate|formatar|destrua|destruir)\b",
            normalized,
        ):
            return True
        if any(term in normalized for term in ("limpar tudo", "esvaziar lixeira", "esvazie a lixeira")):
            return True
        return False

    @staticmethod
    def _is_implicit_close_request(text):
        if not text:
            return False
        return bool(re.fullmatch(
            r"(?:por favor\s+)?(?:pode\s+)?(?:fecha|fechar|feche|encerra|encerrar|encerre|termina|terminar|termine)(?:\s+(?:a[ií]))?",
            text.strip(),
        ))

    @staticmethod
    def _is_close_confirmation(text):
        return text.strip() in {
            "sim", "confirmo", "confirmar", "confirmado", "isso",
            "e isso", "é isso", "pode fechar", "pode encerrar",
        }

    @staticmethod
    def _is_close_rejection(text):
        return text.strip() in {
            "nao", "não", "negativo", "cancela", "cancelar", "deixa",
            "deixa pra la", "deixa pra lá",
        }

    def _remember_opened_app_from_command(self, text, response):
        """Guarda o último aplicativo aberto para confirmar um "fechar" curto."""

        if response is None or not hasattr(self, "commands_parser"):
            return
        if isinstance(response, str) and response.lower().startswith("não"):
            return

        try:
            app_name = self.commands_parser._extract_app_name(text)
            app_name = self.commands_parser._normalize_app_name(app_name)
        except (AttributeError, TypeError):
            return

        if not app_name or app_name in {
            "google", "youtube", "canva", "documentos", "meus documentos",
            "downloads", "meus downloads", "desktop", "área de trabalho",
        }:
            return

        self._last_opened_app = app_name

    def _process_contextual_close_request(self, text):
        pending_app = getattr(self, "_pending_close_app", None)

        if pending_app:
            if self._is_close_confirmation(text):
                self._pending_close_app = None
                response = self.commands.close_app(pending_app)
                self._last_opened_app = None
                return response
            if self._is_close_rejection(text):
                self._pending_close_app = None
                return "Tudo bem, não vou fechar o aplicativo."
            if self._is_implicit_close_request(text):
                return f"Você quer que eu feche o {pending_app}?"

            # A next unrelated command supersedes the pending confirmation.
            self._pending_close_app = None

        if not self._is_implicit_close_request(text):
            return None

        last_app = getattr(self, "_last_opened_app", None)
        if not last_app:
            return "Qual aplicativo você quer que eu feche?"

        self._pending_close_app = last_app
        return f"Você quer que eu feche o {last_app}?"

    def _is_visual_analysis_request(self, normalized):
        """A fala pede análise visual (tela ou câmera)?

        Delega ao catálogo único de gatilhos (`core.brain.visual_triggers`).
        Antes, as frases viviam em três listas duplicadas aqui dentro — cada
        uma com diferenças de acentuação — e manter as três em sincronia era
        impossível. O gatilho agora é DADO, não `if`.
        """
        return classify_visual_request(
            normalized,
            has_previous_summary=bool(getattr(self, "last_screen_summary", None)),
        ).is_visual

    def _is_camera_analysis_request(self, normalized):
        """A fala pede visão da CÂMERA (e não da tela)?

        A regra "tela vence câmera" é garantida por construção no catálogo:
        qualquer menção a tela/monitor/print desqualifica a câmera.
        """
        return classify_visual_request(normalized).wants_camera

    def _visual_request_image(self, text, image=None):
        camera_request = self._is_camera_analysis_request(self._normalize_text(text))
        if not camera_request or image is not None:
            return camera_request, image

        capture = getattr(self, "camera_frame_callback", None)
        if not callable(capture):
            return True, None
        try:
            return True, capture()
        except Exception as error:
            print(f"[VEGA Camera] Não consegui capturar um frame: {error}")
            return True, None

    def _build_visual_prompt(self, text, previous_summary=None):
        normalized = self._normalize_text(text)
        camera_request = self._is_camera_analysis_request(normalized)
        prompt = (
            "Você está vendo uma captura atual da tela do usuário. Ajude com a tarefa que ele está tentando fazer; "
            "não se limite a descrever o que aparece. Responda em português brasileiro, com orientações claras e acionáveis. "
            "Se for código, use apenas detalhes visíveis como linguagem, mensagem de erro e trecho relevante; explique o provável problema e sugira o próximo passo sem inventar contexto. "
            "Se for atividade escolar, ensine o raciocínio em etapas e adapte a explicação ao que está na tela. "
            "Se não conseguir identificar o objetivo ou faltar informação, diga brevemente o que conseguiu observar e faça uma única pergunta específica sobre o que o usuário está tentando fazer."
        )

        if camera_request:
            prompt = prompt.replace(
                "captura atual da tela do usuário",
                "frame atual da câmera do usuário",
            )

        if "mapa mental" in normalized:
            prompt = (
                "Faça um mapa mental resumido e organizado do tema principal que aparece na tela. "
                "Use uma estrutura curta com conceito central, 3 a 5 ramificações e um resumo final. "
                "Se tiver um assunto visível, conecte as ideias de forma natural."
            )

        if previous_summary:
            prompt += f"\nResumo visual anterior: {previous_summary}"

        return prompt

    def _handle_visual_request(self, text, image=None):
        camera_request, image = self._visual_request_image(text, image)
        if camera_request and image is None:
            return "Não consegui acessar a câmera agora. Confira se ela está conectada e tente novamente."

        messages = [{
            "role": "user",
            "content": f"{text}\n\n{self._build_visual_prompt(text, self.last_screen_summary)}",
        }]

        visual_options = {"include_screen": image is None and not camera_request}
        if image is not None:
            visual_options["include_image"] = image
        response = self.ai.chat(messages, **visual_options)
        if response:
            self.last_screen_summary = re.sub(r"\s+", " ", response).strip()[:500]
            self.conversation.add_user_message(text)
            self.conversation.add_assistant_message(response)
            self.memory.add_daily_message("user", text)
            self.memory.add_daily_message("assistant", response)

        return response or "Não consegui analisar a tela agora."

    def _handle_visual_request_stream(self, text, image=None):
        camera_request, image = self._visual_request_image(text, image)
        if camera_request and image is None:
            yield "Não consegui acessar a câmera agora. Confira se ela está conectada e tente novamente."
            return

        messages = [{
            "role": "user",
            "content": f"{text}\n\n{self._build_visual_prompt(text, self.last_screen_summary)}",
        }]

        full_response = ""
        visual_options = {"include_screen": image is None and not camera_request}
        if image is not None:
            visual_options["include_image"] = image
        for chunk in self.ai.chat_stream(messages, **visual_options):
            if chunk is None:
                continue
            chunk_text = self.ai._coerce_text(chunk) if hasattr(self.ai, "_coerce_text") else str(chunk)
            if not chunk_text:
                continue
            full_response += chunk_text
            yield chunk_text

        if not full_response:
            return

        self.last_screen_summary = re.sub(r"\s+", " ", full_response).strip()[:500]
        self.conversation.add_user_message(text)
        self.conversation.add_assistant_message(full_response)
        self.memory.add_daily_message("user", text)
        self.memory.add_daily_message("assistant", full_response)

    # ==========================================================
    # PROCESSAMENTO NORMAL
    # ==========================================================

    def process(self, text, image=None):

        text = text.strip()

        if not text:
            return "Você não disse nada."

        normalized = self._normalize_text(text)
        normalized = self._remove_vega_prefix(normalized)

        if not normalized:
            return "Estou ouvindo."

        if normalized in {
            "ue",
            "ué",
            "ta",
            "tá",
            "ue ta",
            "ue, ta",
            "ué ta",
            "ué, tá",
            "ué, ta",
            "ta bom",
            "tá bom",
            "oi",
            "olá",
            "ola",
            "hey",
            "ok",
            "td",
            "tudo bem",
            "tudo",
            "tá tudo",
        }:
            return "Estou ouvindo."

        if self._is_short_nonsense(normalized):
            # Antes de descartar: pode ser resposta a uma pergunta pendente
            # ("sim", "quero", "ok" são falas curtas legítimas aqui).
            pending_answer = self._answer_pending_question(normalized)
            if pending_answer is not None:
                return pending_answer
            return "Estou ouvindo."

        pending_answer = self._answer_pending_question(normalized)
        if pending_answer is not None:
            return pending_answer

        security_response = self._process_security_request(normalized)
        if security_response is not None:
            return security_response
        security_response = self._process_pending_security_response(normalized)
        if security_response is not None:
            return security_response

        if self._is_holographic_environment_request(normalized):
            if self.environment_callback:
                self.environment_callback()
            return "Ambiente holográfico inicializado."

        holo_response = self._process_holographic_interaction_command(normalized)
        if holo_response is not None:
            return holo_response

        if self._is_visual_analysis_request(normalized):
            return self._handle_visual_request(text, image=image)

        if self._is_database_view_request(normalized):
            callback = getattr(self, "database_view_callback", None)
            if callback:
                callback()
            return self._narrate_local_result(
                text, "Abrindo meu banco de dados no MySQL."
            )

        if is_brain_view_request(text):
            if self.brain_view_callback:
                self.brain_view_callback()
            return self._narrate_local_result(
                text, "Abrindo a visualização do meu segundo cérebro."
            )

        time_date_response = self._answer_time_or_date_question(normalized)
        if time_date_response is not None:
            return time_date_response

        daily_response = self.daily_query.answer(text)
        if daily_response is not None:
            return daily_response
        workflow_response = self._handle_task_workflow(text)
        if workflow_response is not None:
            return workflow_response
        if normalized in {
            "sair",
            "exit",
            "quit"
        }:

            return None

        if normalized == "memoria":

            return self._show_memory()

        if normalized.startswith(
            "lembre que "
        ):

            return self._remember(normalized)

        if normalized.startswith(
            "esqueça "
        ):

            return self._forget(normalized)

        scheduler_response = self._process_scheduler_request(normalized)
        if scheduler_response is not None:
            return scheduler_response

        # _process_security_request já foi avaliado no início deste fluxo;
        # repeti-lo aqui só duplicava trabalho (a resposta não muda).
        support_response = self._process_daily_support_request(text)
        if support_response is not None:
            return support_response

        # ======================================================
        # MODOS
        # ======================================================

        mode_response = self._process_mode(
            normalized
        )

        if mode_response is not None:

            return mode_response

        # ======================================================
        # COMANDOS
        # ======================================================

        # "aumenta mais" após comando local: resolução local, custo zero.
        continuation_response = self._resolve_local_continuation(normalized)
        if continuation_response is not None:
            self.orchestrator.complete_task()
            self._record_local_turn(text, continuation_response, kind="command")
            return continuation_response

        command_response = self._process_command(
            normalized
        )

        if command_response is not None:
            self.orchestrator.complete_task()
            # A VEGA lembra do que disse: sem isto, "aumenta o volume" + "aumenta
            # mais" deixava a IA sem "mais o quê?" (mesmo bug da cotação).
            self._record_local_turn(text, command_response, kind="command")
            return command_response

        # ======================================================
        # TOOLS / APIs EXTERNAS
        # ======================================================

        tool_response = self._process_tool_request(
            normalized
        )

        if tool_response is not None:
            self.orchestrator.complete_task()
            return tool_response

        if self.agent_orchestrator.is_complex_task(text):
            return self.agent_orchestrator.handle_task(text)

        # ======================================================
        # MEMÓRIA
        # ======================================================

        self._register_user_turn(text, rewritten=None)

        messages = self._prepare_messages(text)

        # ======================================================
        # IA
        # ======================================================

        response = self.ai.chat(
            messages,
            include_screen=False,
        )

        self.conversation.add_assistant_message(
            response
        )

        self.memory.add_daily_message(
            "assistant",
            response
        )

        return response

    def evolve_personality(self, updates):
        return evolve_personality(updates)

    # ==========================================================
    # STREAMING
    # ==========================================================

    def cancel_current_response(self):
        self._response_cancelled.set()
        self.ai.cancel_generation()
        SESSION_METRICS.record(cancelled_turns=1)

    def _stream_model_response(self, messages, image=None):
        structured_stream = getattr(self.ai, "chat_stream_with_tools", None)
        if callable(structured_stream):
            return structured_stream(
                messages,
                self.tool_manager.list_tools(),
                self._execute_registered_tool_call,
                num_predict=MAX_TOOL_OUTPUT_TOKENS,
                include_screen=False,
                include_image=image,
            )
        return self.ai.chat_stream(
            messages,
            include_screen=False,
            include_image=image,
        )

    def process_stream(self, text, image=None):
        with self._process_stream_lock:
            self._response_cancelled.clear()
            yield from self._process_stream_locked(text, image=image)

    def _process_stream_locked(self, text, image=None):

        text = text.strip()

        if not text and image is None:

            yield "Você não disse nada."

            return

        normalized = self._normalize_text(text)
        normalized = self._remove_vega_prefix(normalized)

        # Conteúdo necessário para decidir se uma fala curta pertence à conversa
        # anterior. É calculado aqui uma única vez, antes da fala atual ser salva.
        if not normalized and image is None:
            yield "Estou ouvindo."

            return

        if image is not None:
            yield from self._handle_visual_request_stream(text, image=image)
            return

        short_acknowledgement = normalized in {
            "ue", "ué", "ta", "tá", "ue ta", "ue, ta", "ué ta", "ué, tá",
            "ta bom", "tá bom", "oi", "olá", "ola", "hey", "ok", "td",
            "tudo bem", "tudo", "tá tudo",
        }
        allow_contextual_short_reply = False
        if mode_is_generated() and (short_acknowledgement or self._is_short_nonsense(normalized)):
            allow_contextual_short_reply = bool(self.conversation.get_messages())
        if short_acknowledgement and not allow_contextual_short_reply:
            yield "Estou ouvindo."
            return
        if self._is_short_nonsense(normalized) and image is None:
            if not allow_contextual_short_reply:
                yield "Estou ouvindo."
                return

        # ======================================================
        # RESPOSTA A PERGUNTA PENDENTE ("quer ler as notícias?" -> "quero sim")
        # O cérebro lembra das PRÓPRIAS perguntas. A resposta é reconhecida em
        # qualquer posição da fala ("oi vega, quero sim") e funciona por voz,
        # texto ou qualquer timing. Antes isto só existia no loop de voz do
        # vega.py e morria quando a fala chegava em outro momento.
        # ======================================================

        pending_answer = self._answer_pending_question(normalized)
        if pending_answer is not None:
            yield pending_answer
            return

        # PORTÃO ÚNICO DE SEGURANÇA (mesmo do fluxo de comandos).
        # Se a fala é perigosa, aplica segurança e para; se já está liberada,
        # devolve None e a tarefa segue normalmente. O PIN nunca é enviado
        # para a IA: ele morre aqui, no processo local.
        gate_response = self._phone_security_gate(normalized)
        if gate_response is not None:
            yield gate_response
            return

        security_response = self._process_security_request(normalized)
        if security_response is not None:
            yield security_response
            return
        security_response = self._process_pending_security_response(normalized)
        if security_response is not None:
            yield security_response
            return

        support_response = self._process_daily_support_request(text)
        if support_response is not None:
            yield self._narrate_local_result(text, support_response)
            return
        workflow_response = self._handle_task_workflow(text)
        if workflow_response is not None:
            yield workflow_response
            return
        if self._is_holographic_environment_request(normalized):
            if self.environment_callback:
                self.environment_callback()
            yield self._narrate_local_result(text, "Ambiente holográfico inicializado.")
            return
        holo_response = self._process_holographic_interaction_command(normalized)
        if holo_response is not None:
            yield self._narrate_local_result(text, holo_response)
            return

        if self._is_visual_analysis_request(normalized):
            yield from self._handle_visual_request_stream(text, image=image)
            return

        if self._is_database_view_request(normalized):
            callback = getattr(self, "database_view_callback", None)
            if callback:
                callback()
            yield self._narrate_local_result(text, "Abrindo meu banco de dados no MySQL.")
            return
        if is_brain_view_request(text):
            if self.brain_view_callback:
                self.brain_view_callback()
            yield self._narrate_local_result(text, "Abrindo a visualização do meu segundo cérebro.")
            return
        time_date_response = self._answer_time_or_date_question(normalized)
        if time_date_response is not None:
            yield time_date_response
            return

        if normalized in {
            "sair",
            "exit",
            "quit"
        }:

            yield None

            return

        if normalized == "memoria":

            yield self._show_memory()

            return

        if normalized.startswith(
            "lembre que "
        ):

            yield self._remember(normalized)

            return

        if normalized.startswith(
            "esqueça "
        ):

            yield self._forget(normalized)

            return

        scheduler_response = self._process_scheduler_request(normalized)
        if scheduler_response is not None:
            yield scheduler_response
            return

        # _process_security_request já foi avaliado no início deste fluxo;
        # repeti-lo aqui só duplicava trabalho (a resposta não muda).
        if self._is_recycle_bin_request(normalized):
            if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
                yield self._file_changes_disabled_response()
                return
            yield self._process_recycle_bin_request()
            return

        if self._is_file_delete_request(normalized):
            if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
                yield self._file_changes_disabled_response()
                return
            # A busca varre o disco e pode demorar: avisa ANTES de procurar,
            # para o usuário não ficar no silêncio achando que travou.
            yield "Vou procurar esse arquivo agora."
            file_response = self._process_file_delete_request(normalized)
            if file_response is not None:
                yield file_response
            return

        # ======================================================
        # MODOS
        # ======================================================

        mode_response = self._process_mode(
            normalized
        )

        if mode_response is not None:
            mode_narrated = self._narrate_local_result(text, mode_response) or mode_response
            self._record_local_turn(text, mode_narrated, kind="mode")
            yield mode_narrated

            return

        # ======================================================
        # COMANDOS
        # ======================================================

        # "aumenta mais" / "diminui de novo" após um comando local: resolvido
        # AQUI, de graça, reaproveitando o domínio (brilho/volume) da última
        # ação. Sem isto, a fala curta escapava para a IA sem "mais o quê?".
        continuation_response = self._resolve_local_continuation(normalized)
        if continuation_response is not None:
            self.orchestrator.complete_task()
            narrated = self._narrate_local_result(text, continuation_response) or continuation_response
            self._record_local_turn(text, narrated, kind="command")
            yield narrated
            return

        command_response = self._process_command(
            normalized
        )

        if command_response is not None:
            # A intenção e a ação permanecem locais; só a fala que narra o
            # resultado verificado pode ser gerada pelo modelo.
            self.orchestrator.complete_task()
            narrated = (
                command_response
                if (
                    self._is_sensitive_command(normalized)
                    or self._is_close_confirmation(normalized)
                    or self._is_close_rejection(normalized)
                    or self._is_implicit_close_request(normalized)
                )
                else (self._narrate_local_result(text, command_response) or command_response)
            )
            # Registra o turno ANTES de responder: a VEGA lembra do que disse,
            # então "aumenta o volume" + "aumenta mais" funciona (a IA sabe que
            # "mais" = volume). Era o mesmo buraco do bug da cotação, no
            # caminho de comandos locais.
            self._record_local_turn(text, narrated, kind="command")
            yield narrated
            return

        tool_response = self._process_tool_request(normalized)
        if tool_response is not None:
            # ANTES: só "complete_task + yield + return". O turno nunca era
            # registrado no orquestrador, então "e por que isso?" no turno
            # seguinte caía em contexto perdido (context_misses) e a IA
            # respondia "porque o quê?". Agora o turno da ferramenta entra no
            # histórico da conversa contínua, igual ao caminho de process().
            self.orchestrator.record_user_turn(normalized)
            self.orchestrator.record_assistant_turn(tool_response)
            self.orchestrator.complete_task()
            yield tool_response
            return

        # ======================================================
        # ORQUESTRAÇÃO CONVERSACIONAL (stream)
        # ======================================================
        # Intenção do turno (cancelamento/correção/continuação) e
        # reescrita de referências ("isso", "ele", "aquilo") resolvidas
        # ANTES do processamento normal.

        trace = LatencyTrace(turn_id=SESSION_METRICS.turns)
        self.current_trace = trace
        trace.mark("stt_done")

        intent = self.orchestrator.classify_turn_intent(normalized)
        if intent == "cancel":
            cancel_response = self.orchestrator.handle_turn_intent(normalized)
            if cancel_response:
                trace.mark("context_ready")
                trace.mark("first_token")
                trace.mark("response_done")
                self.orchestrator.record_user_turn(normalized)
                self.orchestrator.record_assistant_turn(cancel_response)
                SESSION_METRICS.record_turn(trace)
                yield cancel_response
                return

        rewritten_text, was_rewritten = self.orchestrator.resolve_references(normalized)
        if not was_rewritten and intent == "continuation" and self.orchestrator.task.state != "pending":
            SESSION_METRICS.record(context_misses=1)

        # Consultas consecutivas a ferramentas também fazem parte do diálogo:
        # anexe o turno anterior ao contexto, mesmo quando não há uma nova chamada
        # ao modelo (ex.: "por que o dólar está nesse valor?").
        if intent == "continuation" and not was_rewritten:
            recent_user_turns = [
                item for item in self.orchestrator.immediate
                if item.get("role") == "user"
            ]
            if recent_user_turns:
                previous_question = recent_user_turns[-1].get("original", "").strip()
                if previous_question:
                    rewritten_text = f"{previous_question} — continuação: {normalized}"
                    was_rewritten = True
        if intent == "new" and self._looks_like_action_command(normalized):
            self.orchestrator.start_task("command", target=normalized[:80])

        self.orchestrator.handle_turn_intent(normalized)

        # ======================================================
        # MEMÓRIA / IA
        # ======================================================

        self._register_user_turn(text, rewritten=rewritten_text)

        messages = self._prepare_messages(text)

        trace.mark("context_ready")

        full_response = ""
        first_token_marked = False

        for chunk in self._stream_model_response(messages, image=image):

            if self._response_cancelled.is_set():
                break

            if chunk is None:
                continue

            chunk_text = self.ai._coerce_text(chunk) if hasattr(self.ai, "_coerce_text") else str(chunk)
            if not chunk_text:
                continue

            if not first_token_marked:
                trace.mark("first_token")
                first_token_marked = True

            full_response += chunk_text

            yield chunk_text

        trace.mark("response_done")

        if self._response_cancelled.is_set():
            partial_response = full_response.strip()
            if partial_response:
                self.conversation.add_assistant_message(partial_response)
                self.orchestrator.record_assistant_turn(partial_response)
                self.memory.add_daily_message("assistant", partial_response)
            SESSION_METRICS.record_turn(trace)
            return

        full_response = full_response.strip()

        if not full_response:
            SESSION_METRICS.record_turn(trace)
            return

        # Anti-repetição: varia a abertura se repetir a resposta anterior.
        final_response = self.orchestrator.varied_answer(full_response)

        self.orchestrator.record_user_turn(normalized, rewritten=rewritten_text)
        self.orchestrator.record_assistant_turn(final_response)

        self.conversation.add_assistant_message(
            final_response
        )

        self.memory.add_daily_message(
            "assistant",
            final_response
        )

        SESSION_METRICS.record_turn(trace)

    # ==========================================================
    # ORQUESTRAÇÃO CONVERSACIONAL
    # ==========================================================

    # ==========================================================
    # REGISTRO DO TURNO + MONTAGEM DAS MENSAGENS
    # (antes duplicado no `process` e no `_process_stream_locked`)
    # ==========================================================

    def _register_user_turn(self, text, rewritten=None):
        """Registra a fala do usuário em memória, conversa e estado."""
        self.memory.add_daily_message("user", text)
        self.conversation.add_user_message(rewritten or text)
        self.agent_state.observe(text)
        self._update_context_state(text)

    def _prepare_messages(self, text):
        """Monta as mensagens para o modelo com memória e contexto."""
        messages = list(self.conversation.get_messages())

        memory_context = self.memory.build_memory_context(text)
        relevant_memories = self.memory.retrieve_relevant(text, limit=4)
        contextual_memories = self.memory.contextual_recall(text, limit=4)
        context_summary = self.conversation.context.snapshot()

        if memory_context or relevant_memories:
            messages = self._inject_memory(
                messages,
                memory_context,
                context_summary,
                relevant_memories,
            )

        agent_prompt = self.agent_state.render_context_prompt()
        if agent_prompt:
            messages.insert(0, {
                "role": "system",
                "content": agent_prompt,
            })

        if contextual_memories:
            contextual_summary = "MEMÓRIA CONTEXTUAL RELEVANTE:\n" + "\n".join(
                f"- {item['key']}: {item['summary']}" for item in contextual_memories
            )
            messages.insert(0, {
                "role": "system",
                "content": contextual_summary,
            })

        if is_serious_context(text):
            messages.insert(0, {
                "role": "system",
                "content": (
                    "O contexto parece sério ou urgente. Reduz humor e sarcasmo, "
                    "seja clara, cuidadosa e objetiva."
                ),
            })

        # Dado verificado pela última ferramenta (cotação, clima...). Vem por
        # último para ficar mais próximo da fala atual: é o antecedente mais
        # provável de "e por que isso?", "isso vai subir?", "e amanhã?".
        receipt_context = self._tool_receipt_context()
        if receipt_context:
            messages.insert(0, {
                "role": "system",
                "content": receipt_context,
            })

        # Conhecimento ingerido (PDF, páginas, transcrições). Vem antes das
        # memórias no prompt: é FONTE, não lembrança, e deve poder responder
        # perguntas sobre o conteúdo que o usuário forneceu.
        knowledge_context = self._knowledge_context(text)
        if knowledge_context:
            messages.insert(0, {
                "role": "system",
                "content": knowledge_context,
            })

        return messages

    def _knowledge_context(self, text):
        """Bloco de contexto com o conhecimento ingerido relevante à fala.

        Devolve "" quando não há nada relevante — nunca inventa contexto.
        """
        knowledge = getattr(self, "knowledge", None)
        if knowledge is None:
            return ""
        try:
            if knowledge.is_empty():
                return ""
            return knowledge.build_context(text)
        except Exception as error:
            print(f"[VEGA Conhecimento] Falha ao montar contexto: {error}")
            return ""

    def _looks_like_action_command(self, text):
        """Detecta falas que iniciam uma ação/tarefa (para o contexto de tarefa)."""
        if not text:
            return False
        action_verbs = (
            "abre", "abrir", "abra", "liga", "ligar", "ative", "ativar",
            "pesquisa", "pesquisar", "procura", "procurar", "busca", "buscar",
            "toque", "tocar", "play", "abre o", "executa", "execute", "roda", "rodar",
        )
        folded = re.sub(r"\s+", " ", text.lower())
        return any(re.search(rf"\b{re.escape(verb)}\b", folded) for verb in action_verbs)

    # ==========================================================
    # HORA / DATA
    # ==========================================================

    @staticmethod
    def _portuguese_number(value):
        return portuguese_number(value)

    @staticmethod
    def _is_short_nonsense(text):
        return is_short_nonsense(text)

    @staticmethod
    def _answer_time_or_date_question(text):
        return answer_time_or_date_question(text)

    def _process_mode(self, text):

        text = self._remove_vega_prefix(
            text
        )

        # ------------------------------------------------------
        # MODO GAMER
        # ------------------------------------------------------

        gamer_words = (
            "modo gamer",
            "modo game",
            "modo jogo",
            "modo de jogo",
            "modo gaming",
            "modo games",
            "modo jogador",
        )

        if self._contains_any(
            text,
            gamer_words
        ):

            return VegaModes.gamer(
                self.commands
            )

        # ------------------------------------------------------
        # MODO TRABALHO
        # ------------------------------------------------------

        work_words = (
            "modo trabalho",
            "modo de trabalho",
            "modo profissional",
            "modo foco",
            "modo produtividade",
            "modo work",
        )

        if self._contains_any(
            text,
            work_words
        ):

            return VegaModes.trabalho(
                self.commands
            )

        # ------------------------------------------------------
        # MODO AMBIENTE
        # ------------------------------------------------------

        ambient_words = (
            "modo ambiente",
            "modo de ambiente",
            "ambiente",
            "modo ambient",
            "modo relax",
            "modo relaxante",
            "modo relaxar",
        )

        if self._contains_any(
            text,
            ambient_words
        ):

            return VegaModes.ambiente(
                self.commands
            )

        # ------------------------------------------------------
        # DESATIVAR / VOLTAR AO TEXTO
        # ------------------------------------------------------

        disable_live_words = (
            "desativa o modo live",
            "desativar o modo live",
            "desliga o modo live",
            "desligar o modo live",
            "desativa live",
            "desligar live",
            "volta ao modo texto",
            "volta para o texto",
            "voltar para texto",
            "modo texto",
        )

        if self._contains_any(
            text,
            disable_live_words
        ):

            return VegaModes.disable_live()

        # ------------------------------------------------------
        # ATIVAR MODO LIVE
        # ------------------------------------------------------

        live_words = (
            "ativa o modo live",
            "ativar o modo live",
            "ligar o modo live",
            "liga o modo live",
            "ativa live",
            "ativar live",
            "ligar live",
            "modo live",
            "modo live mode",
            "modo em tempo real",
            "modo realtime",
            "modo em vivo",
            "live",
            "realtime",
            "tempo real",
        )

        if self._contains_any(
            text,
            live_words
        ):

            return VegaModes.live(
                self.commands
            )

        return None

    # ==========================================================
    # REMOVER "VEGA" DO COMEÇO
    # ==========================================================

    @staticmethod
    def _remove_vega_prefix(text):
        return remove_vega_prefix(text)
    def _process_tool_request(self, text):
        """Resolve e executa uma ferramenta externa quando o pedido tiver contexto adequado."""
        tool = self.tool_manager.resolve_for_text(text)
        if tool is None or tool.implementation is None:
            return None
        try:
            params = self._build_tool_parameters(text, tool)
            # Ferramentas que exigem ESTRUTURA (listas de slides, linhas de
            # planilha, formas de desenho) não podem ser montadas por regex a
            # partir da fala: quem monta a estrutura é o modelo, via chamada de
            # ferramenta. O marcador "request" sinaliza isso — o turno segue
            # para a IA em vez de executar com parâmetros inventados (era a
            # causa de "unexpected keyword argument 'content'" e de "missing
            # required positional arguments: 'title' and 'rows'").
            if "request" in params:
                return None
            result = tool.implementation(**params)
            if tool.name == "report":
                self.memory.add_report_event(result)
            if callable(self.tool_result_callback):
                try:
                    self.tool_result_callback(tool.name, result)
                except Exception as error:
                    print(f"[VegaTools] Não foi possível exibir o resultado: {error}")
            response = self._format_tool_result(tool, result)
            # Recibo estruturado do dado obtido: é o que permite a IA responder
            # "e por que isso?" no turno seguinte sem depender do modo cognitivo.
            self._store_tool_receipt(tool.name, result, user_question=text)
            # Guarde respostas de ferramentas no histórico conversacional: perguntas
            # seguintes podem se referir à consulta recém-executada.
            if hasattr(self, "conversation") and hasattr(self, "orchestrator"):
                self._register_user_turn(text, rewritten=None)
                self.conversation.add_assistant_message(response)
                self.memory.add_daily_message("assistant", response)
                self.orchestrator.record_user_turn(text)
                self.orchestrator.record_assistant_turn(response)
                if tool.name == "currency" and isinstance(result, dict):
                    self.orchestrator.start_task(
                        "currency_quote",
                        target=params.get("base", result.get("base", "USD")),
                        result=response,
                    )
                    self.orchestrator.complete_task()
            return response
        except Exception as error:
            print(f"[VegaTools] Erro na ferramenta {tool.name}: {error}")
            return f"Não consegui usar a ferramenta {tool.name} agora: {error}"

    # ==========================================================
    # RECIBO DE FERRAMENTA (dado verificado para o próximo turno)
    # ==========================================================

    # ==========================================================
    # REGISTRO DE TURNO LOCAL (a VEGA lembra o que disse e fez)
    # ==========================================================

    def _record_local_turn(self, user_text, response, *, kind="command", target=None):
        """Registra um turno resolvido LOCALMENTE em todos os registros.

        Problema que resolve: comandos locais ("aumenta o volume", "aumenta o
        brilho") executam com custo zero e respondem — mas antes deste método
        NÃO deixavam registro nenhum no contexto de conversa. Quando o usuário
        emendava "aumenta mais", a IA não tinha "mais o quê?" — exatamente o
        mesmo bug da cotação, só que no caminho de comandos.

        Registra em:
          - conversation (histórico que vai ao modelo);
          - orchestrator (turnos, tarefa atual e antecedente de referência);
          - memória diária.
        """
        if not isinstance(response, str) or not response.strip():
            return

        text = str(user_text or "").strip()

        # 1. Histórico de conversa (o que o modelo enxerga no próximo turno).
        if hasattr(self, "conversation"):
            self.conversation.add_user_message(text)
            self.conversation.add_assistant_message(response)

        # 2. Orquestrador: turnos + tarefa + antecedente de "mais", "isso".
        orchestrator = getattr(self, "orchestrator", None)
        if orchestrator is not None:
            try:
                orchestrator.record_user_turn(text)
                orchestrator.record_assistant_turn(response)
                if target:
                    orchestrator.start_task(kind, target=str(target))
                    orchestrator.complete_task()
            except Exception as error:
                print(f"[VEGA Memória] Falha ao registrar turno local: {error}")

        # 3. Memória diária.
        memory = getattr(self, "memory", None)
        if memory is not None:
            try:
                memory.add_daily_message("user", text)
                memory.add_daily_message("assistant", response)
            except Exception:
                pass

        # 4. Contexto do último comando local: permite que "aumenta mais" ou
        # "diminui de novo" seja resolvido LOCALMENTE (custo zero), reaproveitando
        # o domínio (brilho/volume) da última ação.
        lowered = response.lower()
        if "brilho" in lowered:
            domain = "brightness"
        elif "volume" in lowered:
            domain = "volume"
        else:
            domain = None
        if domain:
            direction = "up" if any(w in lowered for w in ("subindo", "aumentando", "mais")) else "down"
            self.last_local_command = {"domain": domain, "direction": direction}

    def ask_pending_question(self, kind, handler):
        """Registra uma pergunta de sim/não feita pela VEGA.

        ``handler`` é chamado com a resposta classificada (True/False) quando o
        usuário responder. É o que faz o cérebro lembrar das PRÓPRIAS perguntas:
        a resposta funciona por voz, texto ou qualquer timing.
        """
        self._pending_question = {"kind": kind, "handler": handler}

    def has_pending_question(self) -> bool:
        return getattr(self, "_pending_question", None) is not None

    def _answer_pending_question(self, text):
        """Classifica a fala como resposta à pergunta pendente; executa se for.

        Devolve a resposta da VEGA (str) quando o turno foi consumido, ou None
        quando a fala não é um sim/não — nesse caso a pergunta é descartada e o
        fluxo normal segue com o pedido do usuário.
        """
        pending = getattr(self, "_pending_question", None)
        if not pending:
            return None

        from core.brain.confirmation import classify_confirmation
        # accept_imperative=True: a VEGA ACABOU de perguntar algo, então um
        # imperativo ("abre aí") é a intenção de aceitar — não um comando
        # solto. Fora de pergunta pendente, "abre aí" continua sendo comando
        # normal (aí sem alvo a VEGA pergunta "abrir o quê?", corretamente).
        answer = classify_confirmation(text, accept_imperative=True)
        # Fala que não é resposta de sim/não: pergunta perde a validade e o
        # pedido do usuário segue normalmente.
        self._pending_question = None
        if answer is None:
            return None

        try:
            return pending["handler"](answer)
        except Exception as error:
            print(f"[VEGA Pergunta] Falha ao responder: {error}")
            return None

    def _resolve_local_continuation(self, text):
        """Resolve "aumenta mais" / "diminui de novo" localmente (custo zero).

        Usa o domínio (brilho/volume) e a direção do último comando local para
        repetir a ação quando a fala é uma continuação vaga. Devolve None se a
        fala não for uma continuação — nesse caso o fluxo normal prossegue.
        """
        if not isinstance(text, str):
            return None
        last = getattr(self, "last_local_command", None)
        if not last:
            return None

        lowered = text.lower().strip()
        if not lowered:
            return None

        # Só fala curta de continuação. Um pedido completo ("aumenta o volume
        # da tv") é resolvido pelo parser normal, não aqui.
        if len(lowered.split()) > 4:
            return None

        domain = last.get("domain")
        lowered_words = lowered

        # 1. Verbo explícito vence: "aumenta mais" sobe, "abaixa mais" desce.
        if any(w in lowered_words for w in ("diminui", "abaixa", "desce", "menos brilho", "menos volume")):
            wants_up = False
        elif any(w in lowered_words for w in ("aumenta", "sobe", "subir", "sobe mais")):
            wants_up = True
        elif "menos" in lowered_words:
            # "menos" sem verbo = diminuir.
            wants_up = False
        elif "mais" in lowered_words or lowered_words in {"de novo", "mais um pouco"}:
            # "mais" sem verbo herda a direção da última ação: depois de
            # "abaixa o volume", "mais um pouco" continua ABAIXANDO. Antes,
            # "mais" sempre virava "aumentar", invertendo a ação do usuário.
            wants_up = last.get("direction") != "down"
        else:
            return None

        action = {
            ("brightness", True): "brightness_up",
            ("brightness", False): "brightness_down",
            ("volume", True): "volume_up",
            ("volume", False): "volume_down",
        }.get((domain, wants_up), None)
        if action is None:
            return None

        try:
            result = getattr(self.commands, action)()
        except Exception as error:
            print(f"[VEGA Continuação] Falha: {error}")
            return None

        if not isinstance(result, str) or not result.strip():
            return None
        return result

    def _store_tool_receipt(self, tool_name, result, user_question=""):
        """Guarda o dado concreto obtido por uma ferramenta.

        É o que impede o buraco do "e por que isso?": a resposta da ferramenta
        fica no histórico como fala da VEGA, e o prompt proíbe o modelo de tratar
        fala da VEGA como fato. O recibo é dado de API com origem explícita, então
        entra no contexto sem esse conflito.
        """
        try:
            receipt = build_receipt(tool_name, result, user_question=user_question)
        except Exception as error:
            print(f"[VegaTools] Não foi possível registrar o recibo: {error}")
            return None
        if receipt is not None:
            self.last_tool_receipt = receipt
            # Dá ao orquestrador um antecedente concreto para "e por que isso?".
            orchestrator = getattr(self, "orchestrator", None)
            note = getattr(orchestrator, "note_tool_receipt", None)
            if callable(note):
                try:
                    note(receipt.summary, receipt.topic)
                except Exception:
                    pass
        return receipt

    def _tool_receipt_context(self):
        """Bloco de sistema com o último dado verificado (vazio se esfriou)."""
        receipt = getattr(self, "last_tool_receipt", None)
        if receipt is None:
            return ""
        try:
            return receipt.to_context_block()
        except Exception:
            return ""

    def _execute_registered_tool_call(self, name, arguments):
        """Execute only a registered tool with the model-provided arguments."""
        spec = self.tool_manager.get_tool(name)
        if spec is None or not callable(spec.implementation):
            return {"error": "Ferramenta não registrada ou sem implementação."}
        if spec.auth_required:
            return {"error": "Esta ferramenta exige autorização adicional."}
        if not isinstance(arguments, dict):
            return {"error": "Os argumentos da ferramenta devem ser um objeto."}

        try:
            # O desenho ao vivo precisa de um canal próprio: além do resultado
            # textual, a janela do canvas deve abrir e animar. O callback é
            # injetado pela interface (vega.py) e é thread-safe via slot.
            if spec.name == "draw_image" and not callable(getattr(self, "canvas_callback", None)):
                return {"error": "Canvas de desenho indisponível nesta sessão."}
            if spec.name == "draw_image":
                result = spec.implementation(
                    arguments.get("title", ""),
                    arguments.get("shapes"),
                    canvas_callback=self.canvas_callback,
                )
            else:
                result = spec.implementation(**arguments)
            if spec.name == "report":
                self.memory.add_report_event(result)
            if callable(self.tool_result_callback):
                try:
                    self.tool_result_callback(spec.name, result)
                except Exception as error:
                    print(f"[VegaTools] Não foi possível exibir o resultado: {error}")
            return result
        except Exception as error:
            print(f"[VegaTools] Erro na ferramenta {spec.name}: {error}")
            return {"error": f"Não consegui usar a ferramenta {spec.name}: {error}"}

    def _process_scheduler_request(self, text):
        match = re.search(r"(?:lembre|lembra)(?:-me| me)?(?: que)? (.+?) em (\d+(?:\.\d+)?) (minutos?|horas?)$", text)
        if match:
            task = match.group(1).strip()
            amount = float(match.group(2))
            minutes = amount * (60 if match.group(3).startswith("hora") else 1)
            item = self.scheduler.add_in(task, minutes)
            return f"Lembrete criado para daqui a {amount:g} {match.group(3)}: {task}."
        recurring = re.search(r"(?:lembre|lembra)(?:-me| me)? (.+?) a cada (\d+(?:\.\d+)?) (minutos?|horas?)", text)
        if recurring:
            task = recurring.group(1).strip()
            amount = float(recurring.group(2))
            minutes = amount * (60 if recurring.group(3).startswith("hora") else 1)
            self.scheduler.add_in(task, minutes, repeat_minutes=int(minutes))
            return f"Rotina criada: {task}, a cada {amount:g} {recurring.group(3)}."
        if text in {"listar lembretes", "mostrar lembretes", "minhas tarefas"}:
            items = self.scheduler.list()
            return "Nenhum lembrete ativo." if not items else "\n".join(f"{item['id']}: {item['text']} ({item['due_at']})" for item in items)
        return None

    def _daily_overview(self):
        now = datetime.now().astimezone()
        reminders = []
        scheduler = getattr(self, "scheduler", None)
        if scheduler is not None:
            for item in scheduler.list():
                try:
                    due = datetime.fromisoformat(item["due_at"])
                except (KeyError, TypeError, ValueError):
                    continue
                # Inclui o que já venceu E o que ainda vence hoje.
                # A janela vai do início de hoje até o fim de hoje; lembretes
                # futuros de hoje (ex.: daqui a 2 horas) também contam.
                if due.date() <= now.date():
                    reminders.append((due, item))

        lines = ["Resumo do seu dia:"]
        if reminders:
            lines.append("Lembretes e tarefas:")
            for due, item in sorted(reminders, key=lambda entry: entry[0])[:8]:
                status = "atrasado" if due < now else due.strftime("%H:%M")
                lines.append(f"- {status}: {item.get('text', 'lembrete')}")
        else:
            lines.append("Você não tem lembretes ou tarefas vencendo hoje.")

        productivity = getattr(self, "productivity", None)
        if productivity is not None:
            events = productivity.list_calendar_events(now.date())
            if events:
                lines.append("Compromissos de hoje:")
                for event in events[:8]:
                    time_label = event.get("start_time", "")
                    title = event.get("title", "Compromisso")
                    lines.append(f"- {time_label}: {title}" if time_label else f"- {title}")

        support = getattr(self, "daily_support", None)
        focus = support.current_focus() if support is not None else None
        if focus:
            steps = focus.get("steps", [])
            pending = [step.get("text", "") for step in steps if isinstance(step, dict) and not step.get("done")]
            lines.append(f"Sessão de foco ativa: {focus.get('goal', '')}.")
            if pending:
                lines.append(f"Próxima etapa: {pending[0]}.")

        if support is not None:
            today_notes = [
                note for note in support.list_notes(limit=20)
                if str(note.get("created_at", "")).startswith(now.date().isoformat())
            ]
            if today_notes:
                lines.append("Notas de hoje:")
                lines.extend(f"- {note['text']}" for note in today_notes[:3])

        return "\n".join(lines)

    def _focus_plan(self, goal):
        try:
            response = self.ai.chat([{
                "role": "user",
                "content": (
                    "Divida esta tarefa em exatamente três etapas pequenas e executáveis. "
                    "Responda somente com uma etapa por linha, em português, sem introdução ou conclusão.\n"
                    f"Tarefa: {goal}"
                ),
            }], num_predict=180)
        except Exception:
            response = ""

        steps = []
        for line in str(response or "").splitlines():
            step = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
            if step and len(step) < 180:
                steps.append(step)
            if len(steps) == 5:
                break
        if len(steps) < 2:
            steps = [
                f"Organizar o material para {goal}",
                f"Trabalhar na parte principal de {goal}",
                "Revisar o resultado e anotar o que falta",
            ]
        return steps

    def _process_daily_support_request(self, text):
        support = getattr(self, "daily_support", None)
        if support is None:
            return None

        original_text = re.sub(r"(?i)^\s*vega\s*[,;:]?\s*", "", str(text or "")).strip()
        normalized = self._remove_vega_prefix(self._normalize_text(text))
        folded = "".join(
            character
            for character in unicodedata.normalize("NFKD", normalized)
            if not unicodedata.combining(character)
        )

        if any(phrase in folded for phrase in (
            "organiza meu dia", "organize meu dia", "resumo do meu dia",
            "resumo do dia", "minha agenda de hoje", "o que tenho para hoje",
            "o que eu tenho hoje",
        )):
            return self._daily_overview()

        if any(phrase in folded for phrase in ("minhas notas", "listar notas", "o que anotei")):
            notes = support.list_notes()
            return "Você ainda não tem notas rápidas." if not notes else "Suas notas mais recentes:\n" + "\n".join(
                f"- {note['text']}" for note in notes[:10]
            )

        note_match = re.match(
            r"^(?:anota|anote|salva(?:r)? nota|guarda(?:r)? nota)(?:\s+(?:pra mim|para mim))?(?:\s+que)?\s+(.+)$",
            original_text,
            flags=re.IGNORECASE,
        )
        if note_match:
            note = support.add_note(note_match.group(1))
            return f"Anotei: {note['text']}" if note else "O que você quer que eu anote?"
        if folded in {"anota", "anote", "salva nota", "guarda nota"}:
            return "O que você quer que eu anote?"

        focus = support.current_focus()
        if focus and any(phrase in folded for phrase in (
            "conclui a etapa", "terminei a etapa", "marcar proxima etapa",
            "conclui essa parte", "terminei essa parte",
        )):
            completed = support.complete_next_focus_step()
            if completed is None:
                return "Todas as etapas desta sessão já estão concluídas."
            remaining = [
                step.get("text", "") for step in support.current_focus().get("steps", [])
                if isinstance(step, dict) and not step.get("done")
            ]
            next_step = f" Próxima: {remaining[0]}." if remaining else " Todas as etapas foram concluídas."
            return f"Etapa concluída: {completed[1]['text']}.{next_step}"

        if focus and any(phrase in folded for phrase in (
            "como esta minha sessao de foco", "status da sessao de foco",
            "o que falta na sessao", "qual e minha proxima etapa",
        )):
            pending = [
                step.get("text", "") for step in focus.get("steps", [])
                if isinstance(step, dict) and not step.get("done")
            ]
            return f"Você está focado em {focus['goal']}. Próxima etapa: {pending[0]}." if pending else f"Você está focado em {focus['goal']}; todas as etapas foram concluídas."

        if focus and any(phrase in folded for phrase in (
            "encerrar sessao de foco", "finalizar sessao de foco",
            "terminar sessao de estudo", "encerrar sessao de trabalho",
        )):
            finished = support.finish_focus()
            reminder_id = finished.get("reminder_id") if finished else None
            if reminder_id and getattr(self, "scheduler", None) is not None:
                self.scheduler.cancel(reminder_id)
            return "Sessão encerrada. Salvei seu progresso."

        starts_focus = bool(re.search(
            r"\b(?:iniciar|inicie|comecar|comece)\s+(?:uma\s+)?sessao de (?:foco|estudo|trabalho)\b",
            folded,
        )) or bool(re.search(r"\b(?:vamos|quero) estudar\b", folded))
        if not starts_focus:
            return None
        if focus:
            return f"Você já tem uma sessão ativa sobre {focus['goal']}. Conclua ou encerre essa sessão antes de iniciar outra."

        duration_match = re.search(r"\b(?:por|durante)\s+(\d{1,3})\s*(?:minutos?|min)\b", folded)
        duration = min(240, max(5, int(duration_match.group(1)))) if duration_match else 25
        goal_match = re.search(
            r"(?:sess[aã]o de (?:foco|estudo|trabalho)|(?:vamos|quero) estudar)\s*(?:sobre|para|em|de)?\s*(.*)$",
            original_text,
            flags=re.IGNORECASE,
        )
        goal_text = goal_match.group(1) if goal_match else ""
        goal_text = re.sub(r"\b(?:por|durante)\s+\d{1,3}\s*(?:minutos?|min)\b.*$", "", goal_text, flags=re.IGNORECASE).strip()
        goal = goal_text.strip(" .,:;-")
        if not goal:
            return "Qual tarefa ou matéria você quer colocar nesta sessão de foco?"

        steps = self._focus_plan(goal)
        reminder = self.scheduler.add_in(
            f"Pausa da sessão de foco: {goal}", duration, kind="focus"
        )
        support.start_focus(goal, steps, duration, reminder_id=reminder.get("id"))
        plan = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
        return f"Sessão de foco iniciada por {duration} minutos: {goal}.\n{plan}"

    def _is_phone_control_request(self, text):
        """Detecta comandos que controlam o celular e exigem autenticação."""
        if not text:
            return False
        # Consultas que não operam o aparelho.
        if any(phrase in text for phrase in (
            "quais dispositivos", "meus dispositivos", "dispositivos conectados",
        )):
            return False

        # Ações de baixo risco no celular: não exigem autenticação.
        parser = getattr(self, "commands_parser", None)
        if parser is not None:
            try:
                if parser._extract_phone_search_query(text):
                    return False
                music_query, _platform = parser._extract_phone_music_request(text)
                if music_query:
                    return False
            except Exception:
                pass

        # Ligações, mensagens e leitura de conversa são ações de celular
        # mesmo quando o usuário não diz "celular" ("responde o miguel").
        if self._looks_like_phone_action(text):
            return True

        if not any(word in text for word in ("celular", "telefone", "smartphone", "android")):
            return False

        control_markers = (
            "abre", "abrir", "abra", "executa", "pausa", "pular", "pula",
            "próxima", "proxima", "tira", "print", "espelha", "espelhar",
            "manda", "envia", "passa", "reconecta", "conecta", "pareia",
            "bateria", "liga", "desliga", "fecha", "instala",
        )
        return any(marker in text for marker in control_markers)

    def _looks_like_phone_action(self, text):
        """Fala que opera o celular sem citar o aparelho.

        "liga para a rita pelo meu celular", "responde o miguel" e
        "le as mensagens do miguel" são ações do celular.
        """
        try:
            parser = getattr(self, "commands_parser", None)
            if parser is not None and hasattr(parser, "_looks_like_phone_action"):
                return bool(parser._looks_like_phone_action(text))
        except Exception:
            pass
        return False

    def _check_phone_access(self, text):
        """Verifica a autorização do controle do celular.

        Retorna None quando já autorizado (o fluxo normal continua);
        caso contrário, devolve a mensagem de desafio e guarda a ação
        pendente para quando o PIN for informado.
        """
        guard = getattr(self, "phone_guard", None)
        if guard is None:
            # Brain construído fora do __init__ (testes/embutido): cria agora.
            try:
                from core.connectivity.security_gate import PhoneAccessGuard
                guard = PhoneAccessGuard(self.security)
                self.phone_guard = guard
            except Exception:
                return None

        try:
            serial = self._current_phone_serial()
        except Exception:
            serial = ""

        if guard.is_granted(serial):
            return None

        # AUDITORIA NO MOMENTO DO PEDIDO (celular): tira a foto e registra o
        # pedido ANTES de qualquer senha. É o que faz o log mostrar quem
        # pediu, mesmo quando o PIN é errado e nada é executado.
        evidence_path = self._register_security_request(text, source="celular")

        guard.set_pending(
            text,
            device_serial=serial,
            evidence_path=evidence_path,
            face_supported=bool(evidence_path),
        )
        return guard.challenge_message()

    def _phone_security_gate(self, text):
        """PORTÃO ÚNICO: decide se a fala precisa de segurança e aplica.

        Fluxo (o mesmo para QUALQUER tarefa, por voz ou por comando):

        1. a fala pede o celular? (comando direto OU fala natural)
        2. se pede, o acesso já está liberado nesta sessão?
           - sim  -> devolve None e a tarefa executa normalmente;
           - não  -> registra o pedido (com foto) e pede o PIN;
        3. a fala é o PIN do desafio pendente? Conclui a autorização.

        Devolve uma resposta para falar (quando parou no portão) ou None
        (quando a tarefa pode seguir).
        """
        # 1. A fala é sobre o celular? Comando direto ou fala natural.
        if not (self._is_phone_control_request(text) or self._looks_like_phone_action(text)):
            # Não é perigosa: mas ainda pode ser o PIN de um desafio aberto.
            return self._complete_phone_authentication(text)

        # 2. Já liberado nesta sessão? Então a tarefa segue sem obstáculo.
        guard_response = self._check_phone_access(text)
        if guard_response is not None:
            return guard_response

        # 3. Liberado agora: pode ser o PIN de um desafio pendente.
        return self._complete_phone_authentication(text)

    def _register_security_request(self, action, source="voz"):
        """Registra o pedido sensível + foto, sem deixar falha quebrar a fala.

        É o registro de auditoria no MOMENTO do pedido: responde "quem pediu
        isso?" mesmo que a pessoa erre o PIN e a ação nunca execute.
        """
        security = getattr(self, "security", None)
        if security is None:
            return None
        try:
            return security.register_security_request(action, source=source)
        except Exception as error:
            print(f"[VEGA Auditoria] Não foi possível registrar o pedido: {error}")
            return None

    def _complete_phone_authentication(self, text):
        """Conclui a autorização do celular quando o PIN foi informado.

        Vale tanto para o caminho de comandos quanto para a fala natural
        ("responde o miguel" seguido do PIN). Devolve None quando não havia
        autorização pendente ou quando o texto não traz um PIN.
        """
        guard = getattr(self, "phone_guard", None)
        if guard is None or not guard.has_pending():
            return None

        pin_match = self._extract_security_pin(text)
        if not pin_match:
            return None

        pending = guard.take_pending() or {}
        action = pending.get("action", text)

        # A tentativa (certa ou errada) vai para o log de auditoria — antes,
        # o celular não gerava registro nenhum, nem com PIN errado.
        evidence = pending.get("evidence_path")
        face_supported = bool(pending.get("face_supported"))

        granted, message = guard.authenticate(pin_match.group(1))

        try:
            self.security.register_auth_attempt(
                action,
                pin_valid=bool(granted),
                face_valid=None,
                face_supported=face_supported,
                evidence_path=evidence,
                authorized=bool(granted),
                source="celular",
            )
        except Exception as error:
            print(f"[VEGA Auditoria] Falha ao registrar tentativa: {error}")

        if not granted:
            # Mantém a ação pendente para o usuário tentar de novo.
            guard.set_pending(action, device_serial=pending.get("device_serial", ""))
            return message

        guard.grant(pending.get("device_serial", ""))

        # Já autorizado: executa a ação que estava aguardando em vez de pedir
        # para o usuário repetir o comando. Antes, a VEGA dizia "Pode repetir
        # o comando do celular" e parecia que nada tinha acontecido.
        action = str(action or "").strip()
        if action and action.lower() != text.lower():
            try:
                action_response = self.commands_parser.parse(action)
            except Exception:
                action_response = None
            if action_response:
                return f"{message} {action_response}"

        return f"{message} Pode repetir o comando do celular."

    def _current_phone_serial(self):
        """Serial do celular conectado (para detectar troca de aparelho).

        FONTE ÚNICA: usa exatamente o mesmo serial que as ações usam
        (`PhoneConnector._resolve_serial`, lido do `adb devices`).

        Antes, este método caía no `WirelessADB`, que devolve o endereço
        sem-fio ("adb-XXXX._adb-tls-connect._tcp"), enquanto as ações usavam
        o serial do `adb devices`. Dois valores diferentes para o MESMO
        aparelho faziam o guard achar que o celular tinha trocado e revogar
        a autorização — a VEGA pedia o PIN em loop, mesmo com a senha certa.
        """
        try:
            from core.connectivity import PhoneConnector
            return PhoneConnector()._resolve_serial() or ""
        except Exception:
            return ""

    def revoke_phone_access(self):
        """Revoga a autorização (usar quando o celular desconectar)."""
        guard = getattr(self, "phone_guard", None)
        if guard is not None:
            guard.revoke()

    def _process_security_request(self, text):
        normalized = normalize_text(text)
        credential = re.search(r"\b(?:pin|senha|senhas|password|credenciais)\b|security\.(?:json|py)", normalized)
        security_target = credential or re.search(r"\b(?:seguranca|autenticacao|protecao|codigo de acesso)\b", normalized)
        change = re.search(
            r"\b(?:troc\w*|alter\w*|mud\w*|defin\w*|redefin\w*|reset\w*|"
            r"desativ\w*|desabilit\w*|ignor\w*|"
            r"contorn\w*|burl\w*|esquec\w*|mostr\w*|revel\w*|qual|sem|disable|change)\b",
            normalized,
        )
        remove_credential = re.search(
            r"\b(?:remov\w*|apag\w*|exclu\w*)\s+(?:(?:o|a|meu|minha|seu|sua|arquivo)\s+)*"
            r"(?:pin|senha|seguranca|autenticacao|protecao|security\.(?:json|py))\b",
            normalized,
        )
        if (security_target and change) or remove_credential:
            # Do not mistake a PIN rotation request for authorization of a pending deletion.
            self._pending_dangerous_action = None
            return (
                "Não altero, revelo nem desativo o PIN por conversa. "
                "A troca deve ser feita localmente e exige o PIN atual. "
                "Não envie o novo PIN pelo chat."
            )
        if credential and not getattr(self, "_pending_dangerous_action", None):
            return "Não envie senhas pelo chat. O PIN só é validado localmente para uma ação pendente; não é enviado à IA."
        return None
    def _build_tool_parameters(self, text, tool):
        normalized = text.lower().strip()
        norm_ascii = "".join(
            c for c in unicodedata.normalize("NFKD", normalized)
            if not unicodedata.combining(c)
        )
        norm_ascii = re.sub(r"[?!.,;:()]+", " ", norm_ascii)
        norm_ascii = re.sub(r"\s+", " ", norm_ascii).strip()
        params = {}

        if tool.name == "weather":
            city = self._extract_named_entity(
                norm_ascii,
                [
                    "sao paulo", "rio de janeiro", "curitiba", "porto alegre",
                    "belo horizonte", "salvador", "fortaleza", "florianopolis",
                    "brasilia", "recife", "campinas", "goiania", "belem",
                    "manaus", "vitoria", "natal", "campo grande", "cuiaba",
                    "joao pessoa", "maceio", "aracaju", "teresina", "santos",
                    "sorocaba", "ribeirao preto", "londrina", "maringa",
                    "joinville", "blumenau", "caxias do sul", "nova york",
                    "londres", "paris", "toquio", "tokyo", "lisboa", "buenos aires",
                ],
            ) or self._extract_after_keywords(norm_ascii, ["em ", "na ", "no ", "cidade de ", "cidade "])
            if city:
                city = re.sub(r"\b(hoje|amanha|agora|nesta semana|neste fim de semana)\b", "", city).strip()
                params["city"] = city
            if "lat" in norm_ascii and "lon" in norm_ascii:
                try:
                    lat_match = re.search(r"lat(?:itude)?\s*[:=]?\s*(-?\d+(?:\.\d+)?)", norm_ascii)
                    lon_match = re.search(r"lon(?:gitude)?\s*[:=]?\s*(-?\d+(?:\.\d+)?)", norm_ascii)
                    if lat_match and lon_match:
                        params["lat"] = float(lat_match.group(1))
                        params["lon"] = float(lon_match.group(1))
                except Exception:
                    pass
            if not params.get("city") and ("lat" not in params):
                default_city = os.getenv("VEGA_DEFAULT_CITY", os.getenv("DEFAULT_CITY", "São Paulo")).strip() or "São Paulo"
                params["city"] = default_city
            return params

        if tool.name == "currency":
            # 1. Extração de quantia/valor
            amount = 1.0
            amount_match = re.search(r"\b(\d+(?:[.,]\d+)?)\b", norm_ascii)
            if amount_match:
                try:
                    val = float(amount_match.group(1).replace(",", "."))
                    if val > 0:
                        amount = val
                except ValueError:
                    pass

            # 2. Direção da conversão
            is_brl_source = bool(re.search(r"\b(?:reais?|brl)\s+(?:em|para|pra)\b", norm_ascii))

            # 3. Mapeamento das moedas
            if re.search(r"\beuro(?:s)?|eur\b", norm_ascii):
                base, target = ("BRL", "EUR") if is_brl_source else ("EUR", "BRL")
            elif re.search(r"\bbitcoin(?:s)?|btc\b", norm_ascii):
                base, target = ("BRL", "BTC") if is_brl_source else ("BTC", "BRL")
            elif re.search(r"\blibra(?:s)?|gbp\b", norm_ascii):
                base, target = ("BRL", "GBP") if is_brl_source else ("GBP", "BRL")
            elif re.search(r"\bpeso(?:s)?|ars\b", norm_ascii):
                base, target = ("BRL", "ARS") if is_brl_source else ("ARS", "BRL")
            elif re.search(r"\biene(?:s)?|jpy\b", norm_ascii):
                base, target = ("BRL", "JPY") if is_brl_source else ("JPY", "BRL")
            elif re.search(r"\bdolar(?:es)?|usd\b", norm_ascii):
                base, target = ("BRL", "USD") if is_brl_source else ("USD", "BRL")
            else:
                base, target = ("USD", "BRL")

            params["base"] = base
            params["target"] = target
            params["amount"] = amount
            return params

        if tool.name == "geocoding":
            query = self._extract_after_keywords(normalized, ["onde fica ", "cidade ", "endereco ", "endereço ", "localizacao ", "localização ", "em "])
            if not query:
                query = normalized
            params["city"] = query
            return params

        if tool.name in {"summarize_document", "read_file", "read_spreadsheet"}:
            path_match = re.search(
                r'(?i)([a-z]:\\[^\n"<>|?*]+?\.(?:pdf|txt|md|csv|json))\b',
                str(text or ""),
            )
            if path_match is None:
                path_match = re.search(
                    r'(?i)(?:arquivo|documento|planilha|pdf)\s+["\']?([^\n"<>|?*]+?\.(?:pdf|txt|md|csv|json))\b',
                    str(text or ""),
                )
            value = path_match.group(1).strip().strip('"') if path_match else ""
            if not value:
                value = self._extract_after_keywords(
                    normalized,
                    ["sobre ", "de ", "arquivo ", "documento ", "planilha "],
                )
            if tool.name == "summarize_document" and not value:
                return {}
            params["path"] = value
            return params

        if tool.name in {"dictionary", "movie", "vscode"}:
            value = self._extract_after_keywords(normalized, ["sobre ", "de ", "arquivo ", "documento ", "planilha "])
            params["word" if tool.name == "dictionary" else "title" if tool.name == "movie" else "path"] = value or normalized
            return params

        if tool.name == "calendar":
            params["title"] = self._extract_after_keywords(normalized, ["sobre ", "chamado ", "intitulado "]) or "VEGA"
            params["start"] = datetime.now().strftime("%Y%m%dT%H%M%S")
            return params

        if tool.name in {"create_document", "create_presentation"}:
            params["title"] = self._extract_after_keywords(normalized, ["sobre ", "chamado ", "intitulado "]) or "VEGA"
            params["path"] = "VEGA_document.md"
            params["content"] = text
            params["slides"] = [text]
            return params

        # ==========================================================
        # FASE 3 — FERRAMENTAS DE CONTEÚDO NATIVO
        # ==========================================================
        # IMPORTANTE: estas ferramentas recebem estruturas (listas de slides,
        # linhas de planilha, formas de desenho) que NÃO podem ser adivinhadas
        # por regex a partir da fala. Aqui devolvemos o pedido do usuário como
        # "content" e deixamos o MODELO montar a estrutura via chamada de
        # ferramenta (tool call). Antes, o bloco antigo de create_document
        # injetava parâmetros errados e a ferramenta falhava com
        # "unexpected keyword argument" / "missing required arguments".
        if tool.name in {
            "create_presentation_pptx",
            "create_document_docx",
            "create_spreadsheet_xlsx",
            "create_mindmap_image",
            "draw_image",
        }:
            # Só o pedido textual. Sem title/rows/shapes inventados: se a
            # chamada estruturada não os trouxer, a própria ferramenta devolve
            # um erro claro em vez de receber lixo.
            return {"request": text}

        if tool.name == "report":
            return {"title": "Relatório VEGA", "sections": {"solicitação": text}}

        if tool.name in {"email", "whatsapp"}:
            params["recipient"] = self._extract_after_keywords(normalized, ["para "]) or ""
            params["subject"] = "Mensagem da VEGA"
            params["body"] = text
            params["message"] = text
            return params

        if tool.name in {"find_files", "price_monitor"}:
            params["query" if tool.name == "find_files" else "symbol"] = self._extract_after_keywords(normalized, ["por ", "chamado ", "produto "]) or normalized
            return params

        if tool.name == "page_snapshot":
            match = re.search(r"https?://\S+", normalized)
            params["url"] = match.group(0) if match else normalized
            return params

        if tool.name == "news":
            params["limit"] = 3
            return params

        if tool.name == "trending_videos":
            params["limit"] = 5
            params["query"] = self._extract_after_keywords(
                norm_ascii,
                ["video do ", "video da ", "video de ", "jogo do ", "jogo da ", "jogo de "],
            )
            if not params["query"] and any(
                term in norm_ascii
                for term in ("sao paulo", "santos", "brasileirao", "futebol", "masculino")
            ):
                params["query"] = norm_ascii
            return params

        return {}

    def _format_weather_result(self, result: dict) -> str:
        import random
        temperature = result.get("temperature_c")
        feels = result.get("feels_like_c")
        location = result.get("location") or "sua região"
        condition = result.get("condition") or "tempo estável"
        temp_max = result.get("temp_max")
        temp_min = result.get("temp_min")
        rain_prob = result.get("rain_prob")

        if temperature is None and result.get("data"):
            current = result["data"].get("current", {})
            temperature = current.get("temperature_2m")
            feels = current.get("apparent_temperature")

        if temperature is None:
            return f"Não consegui recuperar a previsão do tempo para {location} no momento."

        temp_round = round(temperature)
        feels_round = round(feels) if feels is not None else temp_round

        extra_temp = ""
        if temp_max is not None and temp_min is not None:
            extra_temp = f", com máxima prevista de {round(temp_max)} graus e mínima de {round(temp_min)} graus"
        elif temp_max is not None:
            extra_temp = f", e a máxima pode chegar a {round(temp_max)} graus"

        dica = ""
        cond_lower = condition.lower()
        if rain_prob is not None and rain_prob >= 50 and "chuva" not in cond_lower:
            dica = f" Aliás, tem {rain_prob}% de chance de chuva hoje, melhor levar um guarda-chuva."
        elif "chuva" in cond_lower or "tempestade" in cond_lower or "garoa" in cond_lower:
            dica = " Se for sair, não esquece o guarda-chuva."
        elif temp_round <= 15:
            dica = " Tá bem friozinho, vale a pena colocar um agasalho."
        elif temp_round >= 30:
            dica = " Tá bem calor por lá, lembra de se hidratar."

        variations = [
            f"Em {location} tá fazendo {temp_round} graus agora, com sensação térmica de {feels_round} graus. O tempo tá {condition}{extra_temp}.{dica}",
            f"Aqui em {location} o termômetro tá marcando {temp_round} graus e sensação de {feels_round} graus. O tempo tá {condition}{extra_temp}.{dica}",
            f"Agora em {location} tá {temp_round} graus, com {condition} e sensação de {feels_round} graus{extra_temp}.{dica}",
        ]
        return random.choice(variations).strip()

    def _format_currency_result(self, result: dict) -> str:
        import random
        base = result.get("base", "USD")
        target = result.get("target", "BRL")
        amount = result.get("amount", 1.0)
        rate = result.get("rate")
        converted = result.get("converted")
        high = result.get("high")
        low = result.get("low")
        pct = result.get("pct_change")

        if rate is None:
            return "Não consegui obter a cotação no momento."

        currency_names = {
            "USD": {"sing": "dólar", "plur": "dólares", "sym": "US$"},
            "BRL": {"sing": "real", "plur": "reais", "sym": "R$"},
            "EUR": {"sing": "euro", "plur": "euros", "sym": "€"},
            "BTC": {"sing": "bitcoin", "plur": "bitcoins", "sym": "BTC"},
            "GBP": {"sing": "libra", "plur": "libras", "sym": "£"},
            "ARS": {"sing": "peso argentino", "plur": "pesos argentinos", "sym": "ARS"},
            "CAD": {"sing": "dólar canadense", "plur": "dólares canadenses", "sym": "C$"},
            "JPY": {"sing": "iene", "plur": "ienes", "sym": "¥"},
        }

        base_info = currency_names.get(base, {"sing": base, "plur": base, "sym": base})
        target_info = currency_names.get(target, {"sing": target, "plur": target, "sym": target})
        base_label = base_info["sing"] if amount == 1.0 else base_info["plur"]

        if target == "BRL":
            if base == "BTC":
                formatted_rate = f"R$ {rate:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                formatted_conv = f"R$ {converted:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            else:
                formatted_rate = f"R$ {rate:.2f}".replace(".", ",")
                formatted_conv = f"R$ {converted:.2f}".replace(".", ",")
        else:
            formatted_rate = f"{rate:.4g}".replace(".", ",")
            formatted_conv = f"{converted:.4g} {target_info['plur']}".replace(".", ",")

        var_txt = ""
        if pct is not None:
            pct_val = f"{abs(pct):.2f}".replace(".", ",")
            if pct > 0.05:
                var_txt = f", em alta de {pct_val}% hoje"
            elif pct < -0.05:
                var_txt = f", em queda de {pct_val}% hoje"
            else:
                var_txt = ", estável hoje"

        high_low_txt = ""
        if high is not None and low is not None and high != low:
            if target == "BRL":
                h_str = f"{high:.2f}".replace(".", ",")
                l_str = f"{low:.2f}".replace(".", ",")
                high_low_txt = f" A máxima do dia bateu R$ {h_str} e a mínima R$ {l_str}."

        if amount == 1.0:
            variations = [
                f"O {base_info['sing']} tá cotado a {formatted_rate} agora{var_txt}.{high_low_txt}",
                f"Nesse momento, a cotação do {base_info['sing']} tá em {formatted_rate}{var_txt}.{high_low_txt}",
                f"Hoje o {base_info['sing']} tá saindo por {formatted_rate}{var_txt}.{high_low_txt}",
            ]
        else:
            amount_str = f"{amount:g}".replace(".", ",")
            variations = [
                f"{amount_str} {base_label} tão dando cerca de {formatted_conv} na cotação atual.",
                f"Convertendo agora: {amount_str} {base_label} equivalem a aproximadamente {formatted_conv}.",
                f"Com o {base_info['sing']} a {formatted_rate}, {amount_str} {base_label} dão {formatted_conv}.",
            ]

        return random.choice(variations).strip()

    _CREATED_FILE_LABELS = {
        "create_presentation_pptx": "apresentação",
        "create_document_docx": "documento",
        "create_spreadsheet_xlsx": "planilha",
        "create_mindmap_image": "mapa mental",
        "draw_image": "desenho",
        "edit_video": "vídeo",
    }

    def _announce_created_file(self, tool_name, result):
        """Abre o arquivo gerado no app padrão e devolve uma fala curta.

        O usuário pediu para VER o resultado. Devolver só o caminho em texto
        (ou mostrar num painel pequeno) fazia o trabalho parecer incompleto.
        """
        import os

        path = str(result.get("path") or "")
        label = self._CREATED_FILE_LABELS.get(tool_name, "arquivo")
        name = os.path.basename(path) if path else ""

        if not path or not os.path.exists(path):
            return f"Criei a {label}, mas não consegui localizar o arquivo para abrir."

        opened = False
        try:
            os.startfile(path)  # noqa: S606 — abre no app padrão do Windows
            opened = True
        except Exception as error:
            print(f"[VEGA] Não consegui abrir o arquivo gerado: {error}")

        if opened:
            return f"Pronto. Criei a {label} ({name}) e já abri para você."
        return f"Criei a {label}: {name}. Ela está na sua pasta de usuário."

    def _format_tool_result(self, tool, result):
        if not isinstance(result, dict):
            return str(result)

        # Arquivos gerados (Fase 3): em vez de despejar caminhos na conversa,
        # abre o arquivo no aplicativo padrão do Windows para o usuário VER o
        # resultado. Antes, o mapa mental só aparecia no painelzinho da
        # interface, pequeno demais para ser lido.
        if result.get("path") and result.get("status") in {"created", "drawing", "validated"}:
            return self._announce_created_file(tool.name, result)

        if tool.name == "weather":
            return self._format_weather_result(result)

        if tool.name == "currency":
            return self._format_currency_result(result)

        if tool.name == "geocoding":
            return (
                f"Local encontrado: {result.get('display_name')} "
                f"({result.get('lat')}, {result.get('lon')})."
            )

        if tool.name == "dictionary":
            definitions = result.get("definitions", [])
            return f"Definições de {result.get('word')}: " + " | ".join(definitions[:3])
        if tool.name == "movie":
            data = result.get("result", {})
            return f"{data.get('Title', result.get('title'))}: {data.get('Plot', 'resultado consultado')}"
        if tool.name == "news":
            return "Notícias principais: " + " | ".join(item.get("title", "") for item in result.get("items", [])[:3])
        if tool.name == "summarize_document":
            preview = re.sub(r"\s+", " ", str(result.get("preview") or "")).strip()
            if not preview:
                return "Não consegui extrair texto desse documento. Se for um PDF digitalizado, envie uma imagem da página para eu analisar."
            page_count = result.get("pages")
            page_info = f"O PDF tem {page_count} páginas. " if page_count else ""
            return f"{page_info}Prévia do documento: {preview[:1800]}"
        if tool.name == "trending_videos":
            items = result.get("items", [])
            if not items:
                return "Não encontrei um vídeo adequado agora."
            title = re.sub(r"\s+", " ", str(items[0].get("title") or "")).strip()[:140]
            return f"Encontrei um vídeo para você: {title}."
        if tool.name in {"calendar", "email", "whatsapp", "vscode", "create_document", "create_presentation", "report"}:
            return json.dumps(result, ensure_ascii=False, indent=2)[:3000]
        if tool.name in {"find_files", "read_file", "summarize_document", "read_spreadsheet", "page_snapshot", "price_monitor"}:
            return json.dumps(result, ensure_ascii=False, indent=2)[:5000]

        return str(result)

    @staticmethod
    def _extract_after_keywords(text, keywords):
        for keyword in keywords:
            if keyword in text:
                return text.split(keyword, 1)[1].strip()
        return ""

    @staticmethod
    def _extract_named_entity(text, candidates):
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate in text:
                return candidate
        return ""

    def _process_command(self, text):

        try:

            normalized = text.lower().strip()
            contextual_close_response = self._process_contextual_close_request(normalized)
            if contextual_close_response is not None:
                return contextual_close_response

            if self._is_recycle_bin_request(normalized):
                if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
                    return self._file_changes_disabled_response()
                return self._process_recycle_bin_request()
            if self._is_file_delete_request(normalized):
                if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
                    return self._file_changes_disabled_response()
                return self._process_file_delete_request(normalized)
            sensitive = self._is_sensitive_command(normalized)
            if sensitive:
                confirmed = self._has_security_confirmation(normalized)
                pin_match = self._extract_security_pin(normalized)
                if not confirmed and not pin_match:
                    self._pending_dangerous_action = normalized
                    # AUDITORIA NO MOMENTO DO PEDIDO: a foto é tirada e o
                    # registro é gravado agora, antes de qualquer senha.
                    # Antes, quem errava o PIN não deixava rastro nenhum.
                    self._register_security_request(normalized)
                    return "Confirme a ação perigosa e informe o PIN para continuar."
                if not pin_match:
                    self._pending_dangerous_action = normalized
                    # Auditoria da tentativa SEM o PIN: antes, essa tentativa
                    # confirmada mas sem senha não deixava registro nenhum.
                    self._register_security_request(normalized)
                    return "Essa ação exige confirmação e PIN antes da execução."
                authorized = self._authorize_pending_action(normalized, pin_match.group(1))
                if authorized is not None:
                    return authorized

            # ==================================================
            # SEGURANÇA DO CONTROLE DO CELULAR
            # ==================================================
            # PORTÃO ÚNICO DE SEGURANÇA DO CELULAR
            # ==================================================
            # Uma única passagem decide se a fala é perigosa. Se for, aplica
            # a segurança (registro + foto + PIN) e, aprovada, a tarefa
            # segue normalmente logo abaixo — sem o resto do código precisar
            # saber que existe segurança.
            #
            # Antes havia dois blocos quase iguais aqui (um para comandos de
            # celular, outro para fala natural), com condições que se
            # anulavam. Era isso que tornava o comportamento inconsistente.
            gate_response = self._phone_security_gate(normalized)
            if gate_response is not None:
                return gate_response

            response = self.commands_parser.parse(normalized)
            if response is None:
                # FALLBACK SEMÂNTICO: o parser local não reconheceu, mas a fala
                # pode ser um pedido de ação válido nunca previsto ("deixa o pc
                # mais claro"). Só aqui a IA é consultada — e apenas quando a
                # fala parece um comando (gate de custo); conversa pura nunca
                # passa por aqui. A ação volta pelo MESMO executor, com toda a
                # segurança já existente.
                routed = self._route_via_semantic_router(normalized)
                if routed is not None:
                    return routed
            self._remember_opened_app_from_command(normalized, response)
            # Contexto de tarefa: app aberto por comando vira a tarefa atual.
            if getattr(self, "_last_opened_app", None):
                try:
                    self.orchestrator.start_task("open_app", target=self._last_opened_app)
                except Exception:
                    pass
            # Ambiente holográfico: o app aberto por voz já nasce SELECIONADO
            # (a interface localiza a janela nova para dar foco a ela).
            callback = getattr(self, "app_opened_callback", None)
            if callback and response:
                app_name = getattr(self, "_last_opened_app", None)
                if app_name:
                    try:
                        callback(app_name)
                    except Exception as error:
                        print(f"[VEGA] app_opened_callback: {error}")
            return response

        except Exception as error:

            print(
                f"[VegaCommandsParser] "
                f"Erro: {error}"
            )

            return None

    # ==========================================================
    # FALLBACK SEMÂNTICO (IA roteia, código valida e executa)
    # ==========================================================

    def _semantic_router(self):
        """Cria o roteador sob demanda (lazy), com o catálogo de capacidades.

        O inventário vem do `CapabilityCatalog`, que agrega as regras locais E
        as ferramentas registradas. Antes, só as regras eram enviadas e ainda
        por um corte cego nas primeiras 45 — a IA ficava sem ver metade do que
        a VEGA sabe fazer.
        """
        router = getattr(self, "_semantic_router_instance", None)
        if router is None:
            try:
                from core.brain.semantic_router import SemanticRouter
                from core.capabilities import CapabilityCatalog
                catalog = CapabilityCatalog.build(
                    commands_parser=getattr(self, "commands_parser", None),
                    tool_manager=getattr(self, "tool_manager", None),
                )
                router = SemanticRouter(self.ai, catalog)
                self._semantic_router_instance = router
            except Exception as error:
                print(f"[VEGA Router] Indisponível: {error}")
                return None
        return router

    def _route_via_semantic_router(self, text):
        """Pede à IA a intenção da fala e a executa pelo mesmo executor.

        Devolve None em qualquer falha: o fluxo normal (conversa com a IA)
        segue exatamente como antes. Ação só acontece quando a intenção
        devolvida existe de fato no catálogo do parser.
        """
        try:
            router = self._semantic_router()
        except Exception as error:
            print(f"[VEGA Router] Indisponível no turno: {error}")
            return None
        if router is None:
            return None
        routed = router.route(text)
        if routed is None:
            return None

        intent = routed["intent"]
        params = routed["params"] or {}

        # A execução é pelo catálogo do parser: mesma segurança, mesmo custo.
        catalog = getattr(self.commands_parser, "_intent_catalog", None)
        if catalog is None:
            return None

        rule = next(
            (item for item in catalog.sorted_rules() if item.name == intent),
            None,
        )
        if rule is None:
            return None

        context = self._build_intent_context(text)
        context.extras["semantic_params"] = params
        try:
            result = rule.run(context)
        except Exception as error:
            print(f"[VEGA Router] Falha executando '{intent}': {error}")
            return None

        if result is None:
            return None

        # A VEGA fala a resposta do modelo ("say") quando ela existir; senão,
        # mantém o resultado factual do executor.
        say = routed.get("say") or ""
        return say if say else result

    def _build_intent_context(self, text):
        from core.actions.intent_catalog import IntentContext
        return IntentContext(
            text=text,
            original=text,
            commands=self.commands,
            parser=self.commands_parser,
        )

    @staticmethod
    def _has_security_confirmation(text):
        return any(term in text for term in (
            "confirmo", "confirmar", "confirmado", "pode executar", "pode fazer",
            "pode apagar", "pode deletar", "pode excluir", "autorizo", "sim, pode",
            "sim pode", "sim, é esse", "sim e esse", "é esse mesmo", "e esse mesmo",
            "esse mesmo", "esse arquivo mesmo",
        ))

    @staticmethod
    def _is_file_delete_request(text):
        return bool(re.search(r"\b(?:apaga|apague|apagar|delete|deletar|exclua|excluir|remova|remover)\b.*\b(?:arquivo|ficheiro)\b|\b(?:apaga|apague|apagar|delete|deletar|exclua|excluir|remova|remover)\b\s+\S+", text))

    @staticmethod
    def _is_recycle_bin_request(text):
        return bool(re.search(r"\b(?:esvaziar|esvazie|esvazia|limpar|limpe|limpa|apagar|apague)\b.*\b(?:lixeira|lixo|reciclagem)\b", text))

    def _process_recycle_bin_request(self):
        self._pending_dangerous_action = {"kind": "empty_recycle_bin"}
        return "Você quer esvaziar a Lixeira? Confirme e informe o PIN para continuar."

    @staticmethod
    def _extract_file_query(text):
        match = re.search(r"\b(?:apaga|apague|apagar|delete|deletar|exclua|excluir|remova|remover)\b\s+(?:o|a|os|as)?\s*(?:arquivo|ficheiro)?\s*(.+)$", text)
        if not match:
            return ""
        query = match.group(1).strip()
        query = re.split(r"\s+(?:dentro|na|no|em)\s+(?:(?:da|do|de|dos|das)\s+)?(?:(?:pasta|diret[óo]rio)\s+)?", query, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        query = re.sub(r"^(?:chamado|chamada|de nome|nomeado)\s+", "", query).strip()
        query = re.sub(r"\s+(?:por favor|para sempre)$", "", query).strip()
        return query.strip('"').strip()

    @staticmethod
    def _extract_file_folder(text):
        match = re.search(
            r"\s+(?:dentro|na|no|em)\s+(?:(?:da|do|de|dos|das)\s+)?(?:(?:pasta|diret[óo]rio)\s+)?(.+?)\s*$",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        folder = match.group(1).strip('"').strip()
        folder = re.sub(r"\s+(?:por favor|para sempre)$", "", folder, flags=re.IGNORECASE).strip()
        return folder

    def _process_file_delete_request(self, text):
        query = self._extract_file_query(text)
        if not query:
            return "Qual é o nome exato do arquivo que você quer apagar?"
        folder = self._extract_file_folder(text)
        matches = FileSecurity.find_files(query, folder=folder)
        if not matches:
            return f"Não encontrei nenhum arquivo chamado {query}. Nada foi apagado."
        if len(matches) > 1:
            preview = "; ".join(
                f"{path.name} (pasta {path.parent.name or 'raiz da unidade'})"
                for path in matches[:10]
            )
            self._pending_dangerous_action = {
                "kind": "choose_delete_file",
                "query": query,
                "matches": [str(path) for path in matches],
            }
            return f"Encontrei estes arquivos: {preview}. Diga em qual pasta está o arquivo correto."
        target = matches[0]
        self._pending_dangerous_action = {
            "kind": "delete_file",
            "path": str(target),
            "query": query,
        }
        return self._file_confirmation_message(target)

    @staticmethod
    def _file_confirmation_message(target):
        folder = target.parent.name or "raiz da unidade"
        return f"Achei o arquivo {target.name} na pasta {folder}. É esse mesmo que deseja apagar? Depois confirme e informe o PIN."

    @staticmethod
    def _extract_security_pin(text):
        """Extrai o PIN ditado ou digitado, no formato que o VegaSecurity aceita.

        O PIN é sempre numérico (4 a 64 dígitos). Aceitamos:

        - "1234" (digitado direto);
        - "senha 1234" / "pin: 1234" (com o rótulo na frente);
        - "um dois tres quatro" (ditado por voz, com ou sem espaços).
        """
        direct_match = re.search(r"\b(?:pin|senha|código|codigo)?\s*(\d{4,})\b", text)
        if direct_match:
            return direct_match

        marker = re.search(r"\b(?:pin|senha|código|codigo)\b", text)
        source = text[marker.end():] if marker else text
        number_words = {
            "zero": "0", "um": "1", "uma": "1", "dois": "2", "duas": "2",
            "tres": "3", "três": "3", "quatro": "4", "cinco": "5",
            "seis": "6", "sete": "7", "oito": "8", "nove": "9",
            "dez": "10", "onze": "11", "doze": "12", "treze": "13",
            "quatorze": "14", "catorze": "14", "quinze": "15",
            "dezesseis": "16", "dezasseis": "16", "dezessete": "17",
            "dezoito": "18", "dezenove": "19",
            "vinte": "20", "trinta": "30", "quarenta": "40",
            "cinquenta": "50", "sessenta": "60", "setenta": "70",
            "oitenta": "80", "noventa": "90",
        }
        # O reconhecimento de fala costuma escrever "um dois três" como
        # "umdoistres" ou "um, dois, três". Sem separar as palavras coladas, o
        # PIN ditado era perdido e a autorização nunca concluía.
        source = BrainNumberWords.separate_glued_digits(source, number_words)

        tokens = re.findall(r"[a-záàãâéêíóõôúç]+|\d", source.lower())
        values = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.isdigit():
                values.append(token)
                index += 1
                continue
            value = number_words.get(token)
            if value is None or token in {"e", "a", "o", "é", "eh", "numero", "número", "de"}:
                index += 1
                continue
            numeric_value = int(value)
            if numeric_value >= 20:
                # Speech recognition frequently drops the connector in forms
                # such as "cinquenta seis". Accept both it and the canonical
                # "cinquenta e seis", without treating the next tens value as
                # a unit of the current number.
                next_index = index + 1
                if next_index < len(tokens) and tokens[next_index] in {"e", "eh"}:
                    next_index += 1
                if next_index < len(tokens):
                    unit = number_words.get(tokens[next_index])
                    if unit is not None and 0 < int(unit) < 10:
                        numeric_value += int(unit)
                        index = next_index
            # Cada palavra ditada vale UM dígito (é assim que se fala um PIN:
            # "um dois zero" -> 120). "zero" precisa virar "0" e não "";
            # e valores >= 10 (ex.: "dez") contam como dois dígitos, senão o
            # comprimento do PIN era inflado e "senha um dois zero" era
            # recusado por parecer ter só 3 dígitos.
            if numeric_value == 0:
                values.append("0")
            else:
                values.append(str(numeric_value))
            index += 1

        candidate = "".join(values)
        if len(candidate) >= 4:
            return re.search(r"(\d+)", candidate)
        return None

    def _authorize_pending_action(self, response_text, pin):
        action = self._pending_dangerous_action or response_text
        self._pending_dangerous_action = None
        if isinstance(action, dict) and action.get("kind") == "empty_recycle_bin":
            return self._authorize_recycle_bin(pin)
        if isinstance(action, dict) and action.get("kind") == "choose_delete_file":
            return "Ainda preciso que você diga a pasta do arquivo antes de pedir a confirmação e o PIN."
        if isinstance(action, dict) and action.get("kind") == "delete_file":
            return self._authorize_file_deletion(action, pin)
        face_supported, face_valid, evidence_path = self.security.capture_face_validation()
        if not self.security.authorize_dangerous_action(
            action,
            confirmed=True,
            pin=pin,
            face_supported=face_supported,
            face_valid=face_valid,
            evidence_path=evidence_path,
        ):
            if face_supported and face_valid is False:
                return "A validação facial não confirmou o usuário. Ação bloqueada."
            return "Não foi possível autorizar essa ação. Ela foi bloqueada."
        return self._execute_authorized_action(action)

    def _authorize_file_deletion(self, action, pin):
        if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
            return self._file_changes_disabled_response()

        target = action["path"]
        face_supported, face_valid, evidence_path = self.security.capture_face_validation()
        authorized = self.security.authorize_dangerous_action(
            f"apagar arquivo {target}",
            confirmed=True,
            pin=pin,
            face_supported=face_supported,
            face_valid=face_valid,
            evidence_path=evidence_path,
        )
        if not authorized:
            if face_supported and face_valid is False:
                return "A validação facial não confirmou o usuário. O arquivo não foi apagado."
            return "Não foi possível autorizar essa ação. O arquivo não foi apagado."
        token = self.security._issue_token(f"apagar arquivo {target}")
        try:
            deleted = FileSecurity.delete_file(target, authorization_token=token)
            return f"Apaguei o arquivo {deleted.name}."
        except FileNotFoundError:
            return "O arquivo não existe mais no caminho confirmado. Nada foi apagado."
        except OSError:
            return "Encontrei o arquivo, mas o sistema não permitiu apagá-lo. Nada foi apagado."

    def _authorize_recycle_bin(self, pin):
        if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
            return self._file_changes_disabled_response()

        face_supported, face_valid, evidence_path = self.security.capture_face_validation()
        authorized = self.security.authorize_dangerous_action(
            "esvaziar a lixeira",
            confirmed=True,
            pin=pin,
            face_supported=face_supported,
            face_valid=face_valid,
            evidence_path=evidence_path,
        )
        if not authorized:
            if face_supported and face_valid is False:
                return "A validação facial não confirmou o usuário. A Lixeira não foi esvaziada."
            return "Não foi possível autorizar essa ação. A Lixeira não foi esvaziada."
        token = self.security._issue_token("esvaziar a lixeira")
        try:
            FileSecurity.empty_recycle_bin(authorization_token=token)
            return "A Lixeira foi esvaziada."
        except OSError:
            return "O sistema não permitiu esvaziar a Lixeira. Nada foi apagado."

    def _execute_authorized_action(self, action):
        action = re.sub(r"\b(?:pin|senha|código|codigo)?\s*\d{4,}\b", "", action)
        action = re.sub(r"\b(?:confirmo|confirmar|confirmado|pode executar|pode fazer|autorizo|sim, pode|sim pode)\b", "", action)
        action = re.sub(r"\s+", " ", action).strip()
        command_response = self.commands_parser.parse(action)
        if command_response is not None:
            return command_response
        tool_response = self._process_tool_request(action)
        if tool_response is not None:
            return tool_response
        return "Autorização validada, mas não encontrei um comando executável para esse alvo. Nada foi apagado."

    def _process_pending_security_response(self, text):
        if not self._pending_dangerous_action:
            return None
        if (
            isinstance(self._pending_dangerous_action, dict)
            and self._pending_dangerous_action.get("kind")
            in {"empty_recycle_bin", "choose_delete_file", "delete_file"}
            and not file_access.ALLOW_PERSONAL_FILE_CHANGES
        ):
            self._pending_dangerous_action = None
            return self._file_changes_disabled_response()
        if isinstance(self._pending_dangerous_action, dict) and self._pending_dangerous_action.get("kind") == "choose_delete_file":
            return self._select_pending_file_folder(text)
        # Com uma ação perigosa pendente, o PIN sozinho já é a confirmação:
        # o usuário foi instruído a "confirmar e informar o PIN", então digitar
        # só o PIN equivale a confirmar. Antes, um PIN puro era recusado com
        # "Diga que confirma e informe o PIN" mesmo quando a senha era a certa.
        pin_match = self._extract_security_pin(text)
        if not self._has_security_confirmation(text) and not pin_match:
            return "Ação perigosa pendente. Diga que confirma e informe o PIN."
        if not pin_match:
            return "Entendi a confirmação, mas ainda preciso do PIN."

        return self._authorize_pending_action(text, pin_match.group(1))

    @staticmethod
    def _is_holographic_environment_request(text):
        aliases = (
            "novo projeto",
            "projeto novo",
            "abrir projeto",
            "abrir projeto novo",
            "abrir novo projeto",
            "criar projeto",
            "ambiente holografico",
            "ambiente holográfico",
            "abrir ambiente holografico",
            "abrir ambiente holográfico",
            "iniciar ambiente holografico",
            "iniciar ambiente holográfico",
        )
        return any(alias in text for alias in aliases)

    def _process_holographic_interaction_command(self, text):
        if not text:
            return None

        # Exploded View / Desmontagem
        if any(term in text for term in ("explode isso", "explodir isso", "vista explodida", "desmonte isso", "desmontar", "abrir montagem", "desmonte o modelo")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("explode", {"intensity": 1.0})
                return result or "Vista explodida expandida com sucesso."
            return "Comando de vista explodida reconhecido."

        # Remontar / Fechar montagem
        if any(term in text for term in ("remonta isso", "remontar", "montar isso", "fechar montagem", "juntar pecas", "juntar peças")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("assemble", {})
                return result or "Montagem restaurada."
            return "Montagem restaurada."

        # Rotacionar
        if any(term in text for term in ("gira isso", "girar isso", "rotacionar isso", "gira 90", "gira pra direita", "gira pra esquerda")):
            angle = -90 if "esquerda" in text else 90
            if self.holographic_command_callback:
                result = self.holographic_command_callback("rotate", {"angle": angle})
                return result or f"Rotacionando componente em {angle} graus."
            return "Rotacionando componente."

        # Apagar / Deletar / Jogar fora
        if any(term in text for term in ("apaga isso", "apagar isso", "deleta isso", "deletar isso", "remove isso", "joga fora", "descartar isso")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("delete", {})
                return result or "Componente removido da bancada holográfica."
            return "Componente removido."

        # Duplicar
        if any(term in text for term in ("duplica isso", "duplicar isso", "copia isso", "clonar isso")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("duplicate", {})
                return result or "Componente duplicado."
            return "Componente duplicado."

        # Halt / Congelar
        if any(term in text for term in ("congela", "congelar", "para tudo", "travar rotacao", "estabiliza", "estabilizar")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("halt", {})
                return result or "Projeção holográfica estabilizada."
            return "Projeção estabilizada."

        # Limpar bancada
        if any(term in text for term in ("limpar bancada", "limpa a bancada", "limpar holograma", "limpa o projeto", "limpar projeto")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("clear", {})
                return result or "Bancada holográfica limpa."
            return "Bancada holográfica limpa."

        # Centralizar / Resetar visão
        if any(term in text for term in ("centraliza", "centralizar", "reseta a visao", "resetar visao", "ponto de vista inicial")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("reset_view", {})
                return result or "Ponto de vista centralizado."
            return "Ponto de vista centralizado."

        # ===== ÁREA DE TRABALHO HOLOGRÁFICA (estilo Windows) =====

        # Ativar ambiente de trabalho
        if any(term in text for term in ("ativa area de trabalho", "ativar area de trabalho", "modo area de trabalho", "abre area de trabalho", "abrir area de trabalho")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("environment_on", {})
                return result or "Área de trabalho ativada."
            return "Área de trabalho ativada."

        # Desativar ambiente
        if any(term in text for term in ("desativa area de trabalho", "desativar area de trabalho", "fecha area de trabalho", "sair da area de trabalho")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("environment_off", {})
                return result or "Área de trabalho desativada."
            return "Área de trabalho desativada."

        # Maximizar janela
        if any(term in text for term in ("maximiza", "maximizar", "maximiza a janela", "maximiza janela")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("window_maximize", {})
                return result or "Janela maximizada."
            return "Janela maximizada."

        # Minimizar janela
        if any(term in text for term in ("minimiza", "minimizar", "minimiza a janela", "minimiza janela", "amassa a janela")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("window_minimize", {})
                return result or "Janela minimizada."
            return "Janela minimizada."

        # Alternar foco entre janelas
        if any(term in text for term in ("proxima janela", "próxima janela", "alterna janela", "troca de janela", "outra janela")):
            if self.holographic_command_callback:
                result = self.holographic_command_callback("window_cycle", {})
                return result or "Foco alternado."
            return "Foco alternado."

        return None

    def _select_pending_file_folder(self, text):
        action = self._pending_dangerous_action
        normalized_text = FileSecurity._normalize(text)
        candidates = [Path(path) for path in action["matches"]]
        selected = [path for path in candidates if FileSecurity._normalize(path.parent.name) in normalized_text]
        if not selected:
            folders = ", ".join(dict.fromkeys(path.parent.name for path in candidates))
            return f"Não identifiquei a pasta. Escolha entre: {folders}."
        if len(selected) > 1:
            names = "; ".join(path.name for path in selected)
            return f"Ainda há mais de um arquivo nessa pasta: {names}. Diga o nome completo do arquivo."
        target = selected[0]
        self._pending_dangerous_action = {
            "kind": "delete_file",
            "path": str(target),
            "query": action["query"],
        }
        return self._file_confirmation_message(target)

    # ==========================================================
    # NORMALIZAÇÃO
    # ==========================================================

    @staticmethod
    def _normalize_text(text):
        return normalize_text(text)
    @staticmethod
    def _contains_any(text, words):

        return any(
            word in text
            for word in words
        )

    # ==========================================================
    # MEMÓRIA
    # ==========================================================

    def _remember(self, text):

        content = text[
            len("lembre que "):
        ].strip()

        if not content:

            return (
                "O que você quer "
                "que eu lembre?"
            )

        if " é " in content.lower():

            key, value = content.split(
                " é ",
                1
            )

            key = key.strip().lower()
            value = value.strip()

            self.memory.remember(
                key,
                value,
                category="pessoal",
                importance=1.0,
                source="explicit"
            )

            return (
                f"Beleza. Vou lembrar que "
                f"{key} é {value}."
            )

        self.memory.remember(
            "informacao",
            content,
            category="geral",
            importance=1.0,
            source="explicit"
        )

        return (
            f"Beleza. Vou lembrar disso: "
            f"{content}."
        )

    # ==========================================================
    # ESQUECER
    # ==========================================================

    def _forget(self, text):

        key = text[
            len("esqueça "):
        ].strip().lower()

        if self.memory.recall(key) is None:

            return (
                f"Não encontrei '{key}' "
                "na minha memória."
            )

        self.memory.forget(key)

        return f"Esqueci '{key}'."

    # ==========================================================
    # MOSTRAR MEMÓRIA
    # ==========================================================

    def _show_memory(self):

        permanent = (
            self.memory.get_permanent()
        )

        lines = []

        if permanent:

            lines.append(
                "MEMÓRIAS SOBRE VOCÊ:"
            )

            for key, memory in permanent.items():

                if isinstance(
                    memory,
                    dict
                ):

                    value = memory.get(
                        "value",
                        ""
                    )

                else:

                    value = memory

                lines.append(
                    f"- {key}: {value}"
                )

        else:

            lines.append(
                "Ainda não tenho memórias "
                "permanentes sobre você."
            )

        return "\n".join(lines)

    # ==========================================================
    # INJETAR MEMÓRIA
    # ==========================================================

    def _update_context_state(self, text):
        stripped = text.strip()
        if not stripped:
            return

        state = self.conversation.context
        state.recent_actions.append(stripped)
        if len(state.recent_actions) > 6:
            state.recent_actions = state.recent_actions[-6:]

        lowered = stripped.lower()
        if any(token in lowered for token in ("powerpoint", "ppt", "slide", "apresenta")):
            state.current_app = "powerpoint"
            state.current_object = "apresentação"
            state.current_task = "organizar apresentação"
        elif "chrome" in lowered or "navegador" in lowered:
            state.current_app = "chrome"
        elif "whatsapp" in lowered:
            state.current_app = "whatsapp"

    def _inject_memory(
        self,
        messages,
        memory_context,
        context_snapshot=None,
        relevant_memories=None,
    ):
        return inject_memory(
            messages,
            memory_context,
            context_snapshot,
            relevant_memories,
        )
