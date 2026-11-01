from datetime import datetime
from pathlib import Path
import json
import os
import threading

from core.brain.ai import VegaAI
from core.brain.action_narration import narrate_action_result, local_narration_enabled
from core.brain.tool_receipt import build_receipt
from config.cognitive import mode_is_generated
from core.brain.conversation import Conversation
from core.memory.memory import VegaMemory
from core.actions.commands import VegaCommands
from core.actions.commands_parser import VegaCommandsParser
from core.actions.modes import VegaModes
from core.brain.text_utils import (
    normalize_text, remove_vega_prefix, is_short_nonsense, varied_listening,
    is_small_talk, varied_small_talk,
)
from core.brain.time_answer import (
    answer_time_or_date_question,
    extract_trailing_time_date_question,
    portuguese_number,
)
from core.brain.visual_triggers import classify_visual_request
from core.brain.visual_markers import strip_marker
from core.vision.continuous import ContinuousVision
from core.brain.capability_answer import (
    is_capability_question, summarize_capabilities,
)
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
        # Busca visual em background: função chamada quando os downloads
        # terminam (encadeia com o sinal da UI — configurado em vega.py).
        self.images_done_callback = None
        self.camera_frame_callback = None
        # Canvas de desenho ao vivo (Fase 3): janela própria.
        self.canvas_callback = None
        self.scheduler_notification_callback = None
        self._response_cancelled = threading.Event()
        self._process_stream_lock = threading.RLock()

        # ======================================================
        # COMANDOS
        # ======================================================

        self.commands = VegaCommands(ai=self.ai, memory=self.memory)
        self.commands.brain = self

        # Canal para o painel da interface: prints e resultados visuais
        # aparecem dentro da própria VEGA, além do visualizador do sistema.
        self.commands._result_callback = self._emit_tool_result

        # Música de fundo da inicialização: quando a VEGA começa a tocar
        # qualquer mídia (player interno, Spotify...), a música ambiente
        # para — duas fontes de áudio ao mesmo tempo incomodam.
        try:
            from vega import stop_background_music
            VegaCommands._stop_background_hook = staticmethod(stop_background_music)
        except Exception:
            pass

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
        """Narra o fato de uma ação já concluída, sem alterar a execução.

        Economia de créditos: resultados mecânicos curtos ("O Paint abriu.")
        recebem narração local variada sem chamar o modelo, se o modo global
        de economia estiver ativo (VEGA_NARRATION_LOCAL, padrão ligado).
        """
        local_ok = spend_tokens and local_narration_enabled()
        return narrate_action_result(
            getattr(self, "ai", None),
            user_text,
            factual_result,
            enabled=mode_is_generated() and spend_tokens,
            local_ok=local_ok,
        )

    def _handle_task_workflow(self, text):
        workflow = getattr(self, "task_workflow", None)
        if workflow is None:
            return None
        return workflow.handle(text)

    def _answer_capability_question(self, text):
        """Resposta LOCAL à pergunta 'o que você pode fazer?'.

        A pergunta é recorrente (logs de 01/10 e 11/10/2026) e não tinha
        caminho: caía no None do parser e ia gastar tokens para a IA
        improvisar — às vezes inventando capacidade que não existe.
        Aqui a resposta é construída do catálogo real de capacidades, custo
        zero, no tom da VEGA.
        """
        if not is_capability_question(text):
            return None
        try:
            from core.capabilities import CapabilityCatalog
            catalog = CapabilityCatalog.build(
                commands_parser=getattr(self, "commands_parser", None),
                tool_manager=getattr(self, "tool_manager", None),
            )
            answer = summarize_capabilities(catalog.inventory())
        except Exception:
            answer = summarize_capabilities(None)
        self._record_local_turn(text, answer, kind="capabilities")
        return answer

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

    def _is_pure_time_or_date_question(self, normalized):
        """A fala é SÓ uma pergunta de hora ou data (sem outro assunto)?

        "que horas são" -> sim. "notícias de hoje" -> não (o assunto é
        notícias; "hoje" é detalhe). Usada para decidir se a resposta local
        de hora/data deve ter prioridade sobre as ferramentas.
        """
        answer = answer_time_or_date_question(normalized)
        return answer is not None

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

    # ==========================================================
    # OLHO NA TELA — visão contínua
    # ==========================================================

    _WATCH_STOP_RE = re.compile(
        r"\b(?:para(?:r)?(?:\s+de)?|pare|cancela(?:r)?(?:\s+o)?|desliga(?:r)?(?:\s+o)?)\s+(?:de\s+)?"
        r"(?:olhar|olho|observar|assistir|vigiar|monitorar|monitoramento|monitorando)\b",
        re.IGNORECASE,
    )

    _WATCH_START_RE = re.compile(
        r"\b(?:fica(?:r)?|fica|fique|continua(?:r)?|vem|vai)\s+(?:de\s+olho|olhando|monitorando|vigilante)\b"
        r"|\b(?:me\s+)?(?:avisa|avise|me\s+avise|me\s+avisa|alerta|notifica)\s+(?:quando|se|assim\s+que)\b"
        r"|\b(?:olho\s+na\s+tela|vigia\s+a\s+tela|monitora\s+a\s+tela)\b",
        re.IGNORECASE,
    )

    def _stop_continuous_vision(self, normalized):
        if not self._WATCH_STOP_RE.search(normalized):
            return None
        watcher = getattr(self, "continuous_vision", None)
        if watcher is not None and watcher.is_running:
            watcher.stop()
            return "Ok, parei de olhar a tela."
        return "Não estou de olho em nada agora."

    def _maybe_start_continuous_vision(self, normalized):
        if not self._WATCH_START_RE.search(normalized):
            return None
        # Só é pedido de visão contínua se citar tela/aviso — "fica de olho"
        # genérico é conversa e não deve ligar a captura.
        if not re.search(r"\btela\b|\bmonitor\b|\bpagina\b|\bsite\b|\bresultado\b|\bpreco\b|\bpreço\b", normalized):
            return None

        capture = getattr(self, "screen_capture_callback", None)
        if not callable(capture):
            capture = lambda: __import__("core.actions.screenshot", fromlist=["capture_image"]).capture_image()

        watcher = getattr(self, "continuous_vision", None)
        if watcher is None:
            watcher = ContinuousVision(
                capture_fn=capture,
                analyze_fn=self._analyze_vision_frame,
                speak_fn=getattr(self, "continuous_vision_speak", None),
                interval_seconds=15.0,
            )
            self.continuous_vision = watcher

        if watcher.is_running:
            return "Já estou de olho na tela. Continuo avisando se algo mudar."

        # A instrução é a própria fala, sem os verbos de comando.
        instruction = re.sub(
            r"\b(?:vega[,\s]+)?(?:fica(?:r)?|fique|continua|vem|vai)\s+(?:de\s+olho|olhando|monitorando|vigilante)\s*(?:na|no|em)?\s*(?:tela|monitor|pagina)?\s*(?:e|quando|se|me\s+avisa|me\s+avise|avisa|avise)?\s*",
            "",
            normalized,
            flags=re.IGNORECASE,
        ).strip(" ,.;:")
        watcher.start(instruction=instruction)
        return "Certo, vou ficar de olho na tela e te aviso se algo importante mudar."

    def _analyze_vision_frame(self, messages, include_image=None, num_predict=200):
        try:
            return self.ai.chat(
                messages,
                include_image=include_image,
                num_predict=num_predict,
            )
        except Exception as error:
            print(f"[VEGA Visão Contínua] Análise falhou: {error}")
            return ""

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
            # A câmera pode ter falhado por índice errado (câmera virtual
            # selecionada por padrão): tenta a próxima câmera disponível.
            try:
                camera = getattr(self, "camera", None)
                if camera is not None:
                    next_index = camera.next_camera()
                    if next_index is not None:
                        print(f"[VEGA Camera] Tentando a câmera {next_index}...")
                        return True, capture()
            except Exception:
                pass
            return True, None

    def _build_visual_prompt(self, text, previous_summary=None):
        normalized = self._normalize_text(text)
        camera_request = self._is_camera_analysis_request(normalized)
        fonte = "um frame atual da câmera do usuário" if camera_request else "uma captura atual da tela do usuário"
        prompt = (
            f"Você está vendo um {fonte}. A pessoa está falando com você por VOZ — a resposta vai ser LIDA em voz alta, então escreva como você FALA, não como um texto. "
            "Use frases curtas e naturais, do jeito que uma pessoa fala de verdade: 'Então... acho que o problema é X', 'Vi aqui que...', 'Olha, dá pra resolver assim:'. "
            "Nada de listas com marcadores, títulos, seções ou formatação de documento — o ouvinte não vê nada disso, só escuta. "
            "Ajude com a tarefa que a pessoa está tentando fazer, sem se limitar a descrever o que aparece. Seja direto e fale na primeira pessoa, como um assistente do lado dela. "
            "Se for código, comente apenas o que dá pra ver (linguagem, mensagem de erro, trecho visível) e sugira o próximo passo sem inventar contexto. "
            "Se for atividade escolar, ensine o raciocínio passo a passo, como um professor explicando em voz alta, adaptado ao que está na tela. "
            "Se não conseguir identificar o objetivo, diga em uma frase o que conseguiu ver e pergunte de forma natural o que a pessoa está tentando fazer — sem parecer um formulário."
        )

        if "mapa mental" in normalized:
            prompt = (
                "Faça um mapa mental FALADO do tema principal que aparece na tela — a pessoa vai OUVIR, não ler. "
                "Comece pelo conceito central, depois percorra de 3 a 5 ramificações em frases curtas e conectadas ('O tema central é X. A partir dele, temos A... depois B... e por fim C...'), "
                "e feche com um resumo rápido em uma ou duas frases. Sem marcadores, sem títulos, sem formatação — apenas fala fluida e organizada."
            )

        if previous_summary:
            prompt += f"\nResumo visual anterior (para dar continuidade à conversa, sem repetir o que já foi dito): {previous_summary}"

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
        # Entrada nunca pode derrubar o cérebro: None/objetos viram string.
        if text is None:
            text = ""
        text = str(text)

        text = text.strip()

        if not text:
            return "Você não disse nada."

        normalized = self._normalize_text(text)
        normalized = self._remove_vega_prefix(normalized)

        if not normalized:
            return varied_listening()

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
            return varied_listening()

        # Portão de small talk local: "bom dia", "boa tarde", "obrigado vega"
        # respondem em 0ms sem ir à nuvem (ver processo_stream_locked).
        if is_small_talk(normalized):
            return varied_small_talk(normalized)

        if self._is_short_nonsense(normalized):
            # Antes de descartar: pode ser resposta a uma pergunta pendente
            # ("sim", "quero", "ok" são falas curtas legítimas aqui).
            pending_answer = self._answer_pending_question(normalized)
            if pending_answer is not None:
                return pending_answer
            return varied_listening()

        capability_response = self._answer_capability_question(normalized)
        if capability_response is not None:
            return capability_response

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

        # ======================================================
        # FERRAMENTAS vêm ANTES da resposta local de hora/data.
        # Sem isto, "notícias de hoje" casava na guarda de data
        # (por causa de "hoje") e a VEGA respondia o dia em vez de
        # buscar notícias. A ferramenta é a intenção dominante;
        # hora/data só responde quando nenhuma ferramenta encaixa.
        #
        # EXCEÇÃO: hora/data puras ("que horas são", "que dia é hoje")
        # continuam com a resposta local em português — a ferramenta
        # current_time/current_date devolve texto cru ("18:00:38") que
        # soa robótico na fala, e a frase local é melhor.
        # ======================================================
        pure_time_date = self._is_pure_time_or_date_question(normalized)
        if not pure_time_date:
            tool_response = self._process_tool_request(normalized)
            if tool_response is not None:
                self.orchestrator.complete_task()
                return tool_response

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

        # MULTI-INTENÇÃO: várias ações numa fala ("toca música e abre a
        # calculadora e me fala o tempo"). Testada ANTES do comando único —
        # precisa de pelo menos 2 cláusulas resolvíveis para assumir o turno.
        multi_response = self._process_multi_intent(normalized)
        if multi_response is not None:
            self.orchestrator.complete_task()
            self._record_local_turn(text, multi_response, kind="command")
            return multi_response

        command_response = self._process_command(
            normalized
        )

        if command_response is not None:
            # MULTI-ETAPA: mesmo suporte do caminho de streaming — anexa a
            # resposta da pergunta de hora/data secundária, se houver.
            trailing_time = extract_trailing_time_date_question(normalized)
            if trailing_time:
                command_response = f"{command_response} {trailing_time}"
            self.orchestrator.complete_task()
            # A VEGA lembra do que disse: sem isto, "aumenta o volume" + "aumenta
            # mais" deixava a IA sem "mais o quê?" (mesmo bug da cotação).
            self._record_local_turn(text, command_response, kind="command")
            return command_response

        # ======================================================
        # (ferramentas já foram avaliadas ANTES da resposta de hora/data,
        # acima — intenção de ferramenta vence data/hora local)

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

    # ==========================================================
    # OTIMIZAÇÃO DE LATÊNCIA DA IA
    # ==========================================================

    # Perguntas puramente explicativas/conversacionais: não precisam de
    # ferramentas executáveis (corta ~2500 tokens de schemas e reduz o TTFT
    # pela metade). Comandos de mídia/abrir app já são capturados antes pelo
    # parser local, então estes padrões só atingem fala conversacional.
    _PURE_EXPLANATORY_RE = re.compile(
        r"^\s*(?:como\s+(?:eu\s+)?fa[çc]o|como\s+fazer|como\s+se\s+faz|"
        r"o\s+que\s+[ée]|oque\s+[ée]|explique|me\s+ensine|ensina\s+(?:eu\s+)?(?:a|como)|"
        r"me\s+conta|conta\s+(?:uma\s+)?(?:piada|curiosidade|hist[óo]ria|sobre)|"
        r"qual\s+(?:[éea\s]+)?(?:sua\s+)?opini[ãa]o|o\s+que\s+voc[êe]\s+acha|"
        r"me\s+fal[ae]|fal[ae]\s+sobre|conte\s+sobre|por\s+que|porque|por\s+qual\s+motivo|"
        r"quem\s+[ée]|quem\s+foi|me\s+recomend\w*|tem\s+alguma\s+dica|me\s+d[áa]\s+uma\s+dica|"
        r"você\s+pode\s+me\s+explicar|voc[êe]\s+pode\s+me\s+explicar)\b",
        re.IGNORECASE,
    )

    # Pedido claro de criação de projeto de software: só precisa da
    # ferramenta create_project (não de clima, mídia, câmbio etc.).
    _PROJECT_CREATION_RE = re.compile(
        r"\b(?:crie|criar|cria|fa[çc]a|fazer|gera|gerar|gere)\s+(?:um\s+|uma\s+|me\s+um\s+)?"
        r"(?:site|sistema|app|aplicativo|projeto|p[áa]gina|landing|dashboard|jogo)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _is_pure_explanatory_query(cls, text):
        return bool(cls._PURE_EXPLANATORY_RE.match(str(text or "")))

    @classmethod
    def _is_project_creation_request(cls, text):
        return bool(cls._PROJECT_CREATION_RE.search(str(text or "")))

    def _stream_model_response(self, messages, image=None):
        structured_stream = getattr(self.ai, "chat_stream_with_tools", None)
        if callable(structured_stream):
            first_user = next(
                (
                    m.get("content", "") for m in messages
                    if isinstance(m, dict) and m.get("role") == "user"
                ),
                "",
            )
            if image is None and self._is_pure_explanatory_query(first_user):
                # Sem ferramentas: resposta conversacional pura, direto via chat_stream.
                return self.ai.chat_stream(
                    messages,
                    include_screen=False,
                    include_image=image,
                )
            if image is None and self._is_project_creation_request(first_user):
                # Apenas a ferramenta create_project é relevante aqui.
                filtered_tools = [
                    t for t in self.tool_manager.list_tools()
                    if getattr(t, "name", "") == "create_project"
                ]
                if filtered_tools:
                    return structured_stream(
                        messages,
                        filtered_tools,
                        self._execute_registered_tool_call,
                        num_predict=MAX_TOOL_OUTPUT_TOKENS,
                        include_screen=False,
                        include_image=image,
                    )
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
            yield varied_listening()

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
            yield varied_listening()
            return
        # ======================================================
        # SMALL TALK LOCAL (portão de 0ms): saudação/agradecimento puro
        # responde sem rede, sem prompt, sem token. Antes, "bom dia" e
        # "obrigado vega" atravessavam TODOS os portões e caíam no Gemini
        # na nuvem — 3 a 5s de espera por um cumprimento.
        # ======================================================
        if image is None and not allow_contextual_short_reply and is_small_talk(normalized):
            yield varied_small_talk(normalized)
            return
        if self._is_short_nonsense(normalized) and image is None:
            if not allow_contextual_short_reply:
                yield varied_listening()
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

        capability_response = self._answer_capability_question(normalized)
        if capability_response is not None:
            yield capability_response
            return

        # ======================================================
        # OLHO NA TELA — visão contínua com avisos por voz
        # ======================================================

        watch_stop = self._stop_continuous_vision(normalized)
        if watch_stop is not None:
            yield watch_stop
            return
        watch_start = self._maybe_start_continuous_vision(normalized)
        if watch_start is not None:
            yield watch_start
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

        # "Deleta/apaga as memórias diárias": zera o histórico conversacional
        # de TODOS os dias, mas NUNCA toca na memória permanente (o que o
        # usuário ensinou — identidade, preferências, fatos — fica intacto).
        if self._is_daily_memory_wipe_request(normalized):
            yield self._wipe_daily_memories(normalized)
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

        # MULTI-INTENÇÃO (mesmo caminho do process não-stream): várias ações
        # numa única fala antes do comando único.
        multi_response = self._process_multi_intent(normalized)
        if multi_response is not None:
            self.orchestrator.complete_task()
            self._record_local_turn(text, multi_response, kind="command")
            yield multi_response
            return

        command_response = self._process_command(
            normalized
        )

        if command_response is not None:
            # MULTI-ETAPA: pedido composto ("toca rock e depois me fala que
            # horas sao"). O comando resolve a ação principal; se sobrou uma
            # pergunta de hora/data no final da fala, a resposta dela é
            # anexada — o usuário recebe as DUAS coisas que pediu.
            trailing_time = extract_trailing_time_date_question(normalized)
            if trailing_time:
                command_response = f"{command_response} {trailing_time}"
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
        # FEEDBACK ANTECIPADO: criação de projeto de software.
        # Abre o editor IMEDIATAMENTE e avisa por voz, enquanto a
        # geração dos arquivos acontece na sequência (o usuário não
        # fica em silêncio 8-16s achando que a VEGA travou).
        # ======================================================
        if intent == "new" and self._is_project_creation_request(normalized) and re.search(r"\bvs\s?code\b|\bvscode\b", normalized):
            try:
                self.commands.open_app("vscode")
            except Exception:
                pass
            yield "Abrindo o VS Code e estruturando o projeto. Já te aviso quando estiver pronto."

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

        # MARCADORES VISUAIS: o modelo marca no texto onde a imagem deve
        # aparecer; os marcadores são removidos da resposta final (o histórico,
        # a conversa e a UI não devem exibir "[visual: ...]"). A fala já sai
        # limpa no vega.py (strip_marker por frase); aqui é a limpeza oficial
        # do texto que PERSISTE.
        full_response = strip_marker(full_response)

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
        # A LEITURA de memórias relevantes acima (retrieve_relevant) varre os
        # MESMOS arquivos que o contextual_recall varreria de novo. Passamos
        # os itens já ranqueados para reuso: metade das varreduras de memória
        # do turno, mesma informação no prompt. Stubs de teste que não aceitam
        # o parâmetro continuam funcionando com a chamada antiga.
        try:
            contextual_memories = self.memory.contextual_recall(
                text, limit=4, precomputed=relevant_memories
            )
        except TypeError:
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

        # ESTADO DA INTERFACE (o que a VEGA tem aberto na tela): players de
        # vídeo/música com o conteúdo deles. É o que permite a IA entender
        # "tira isso" logo depois de "vamos assistir felipe neto" — ela SABE
        # o que ela mesma colocou lá. Vem por último, junto do recibo.
        interface_context = self._interface_context()
        if interface_context:
            messages.insert(0, {
                "role": "system",
                "content": interface_context,
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
        # MODO LIVE — REMOVIDO
        # A VEGA conversa fluida pelo pipeline normal (turno a turno,
        # streaming), sem depender do Gemini Live.
        # ------------------------------------------------------

        live_words = (
            "modo live",
            "modo em tempo real",
            "modo realtime",
        )

        if self._contains_any(
            text,
            live_words
        ):
            return (
                "O modo Live foi removido. Converso normalmente pelo modo "
                "padrão — continua rápido e fluido."
            )

        return None

    # ==========================================================
    # REMOVER "VEGA" DO COMEÇO
    # ==========================================================

    @staticmethod
    def _remove_vega_prefix(text):
        return remove_vega_prefix(text)
    # Ferramentas de documento estruturado que passam pelo TaskRunner
    # (execução com validação de artefato real + registro em data/task_runs).
    _TASK_TOOLS = frozenset({
        "create_resume_docx", "create_document_docx",
        "create_spreadsheet_xlsx", "create_presentation_pptx",
    })

    def _confirm_high_risk_step(self, question: str) -> bool:
        """Gate de etapas de risco alto do TaskRunner.

        Usa o MESMO mecanismo de ações destrutivas existentes: marca a
        pergunta como ação perigosa pendente e devolve False — o TaskRunner
        para a etapa (status "stopped") e a VEGA pergunta ao usuário. Quando
        o usuário confirma na fala seguinte, o fluxo de confirmação existente
        do brain resolve a pendência (PIN + auditoria) antes de retomar.
        """
        self._pending_dangerous_action = {
            "question": question,
            "kind": "task_step",
            "asked_at": __import__("time").time(),
        }
        return False

    def _run_task_goal(self, text: str, tool):
        """Executa um objetivo de documento via TaskRunner (com validação).

        Reusa _build_tool_parameters: se a estrutura necessária (sections/
        slides/rows) não veio da fala, devolve None para a IA montar — mesmo
        contrato do caminho antigo, agora com validação de arquivo e registro.
        """
        params = self._build_tool_parameters(text, tool)
        if "request" in params:
            return None  # estrutura complexa → quem monta é a IA
        # Campos estruturados obrigatórios não extraíveis por regex.
        if tool.name == "create_resume_docx" and not params.get("sections"):
            return None
        if tool.name == "create_presentation_pptx" and not params.get("slides"):
            return None
        if tool.name == "create_spreadsheet_xlsx" and not params.get("headers"):
            return None

        from core.brain.task_runner import TaskRunner
        runner = TaskRunner(
            self.tool_manager,
            memory=getattr(self, "memory", None),
            # Gate de segurança real: etapas de risco alto passam pelo
            # authorize_dangerous_action (PIN + registro de auditoria).
            on_confirm=self._confirm_high_risk_step,
        )
        callback = getattr(self, "tool_result_callback", None)
        if callable(callback):
            try:
                callback("__moon__", ("activate", ["Execução"]))
            except Exception:
                pass
        try:
            result = runner.run(text, plan=[{"tool": tool.name, "params": params}])
        finally:
            if callable(callback):
                try:
                    callback("__moon__", ("deactivate", ["Execução"]))
                except Exception:
                    pass
        # Narrar como resultado de ferramenta (mesma voz do caminho antigo).
        if result["status"] == "completed":
            artifact_path = next(
                (s.get("validation", {}).get("path") or "" for s in result["steps"]
                 if s.get("validation", {}).get("valid")), "")
            # "status": "created" é OBRIGATÓRIO: sem ele, _format_tool_result
            # não reconhece o arquivo como criado e NUNCA abre para o usuário
            # (era exatamente o relato do Endriw: "criei o currículo" sem
            # abrir nada nem dizer onde está).
            response = self._format_tool_result(tool, {
                "ok": True, "status": "created",
                **({"path": artifact_path} if artifact_path else {}),
            })
            if not response.strip():
                response = result["summary"]
        else:
            response = self._format_tool_result(tool, {
                "ok": False, "error": result.get("summary", "falha na execução"),
            })
        self._store_tool_receipt(tool.name, result, user_question=text)
        return response

    # Referências naturais de arquivo que devem ser resolvidas para caminhos
    # reais antes do plano composto: "arquivo teste.txt", "na raiz do pc",
    # "meus downloads", "na área de trabalho".
    _NATURAL_FILE_RE = re.compile(
        r"\b(?:(?:o|a|do|da|meu|minha|um|uma)\s+)?arquivo\s+(?P<name>[\w\-. ]+?\.[a-zA-Z0-9]{2,4})"
        r"|(?P<folder>na raiz do (?:pc|computador|disco c|disco)|meus? downloads?|minhas? imagens?|"
        r"meus? documentos?|na (?:minha )?(?:área de trabalho|area de trabalho|mesa)|no desktop)",
        re.IGNORECASE)

    def _resolve_natural_files(self, text: str) -> str:
        """Converte referências naturais de arquivo em caminhos reais.

        "olha o arquivo na raiz do pc chamado dados.csv" -> "C:\\dados.csv";
        "olha meus downloads e crie... teste.txt" -> caminho real em Downloads.
        Usa FileSecurity.find_files (busca fuzzy já existente). Sem
        substituição quando nada é encontrado — a fala segue intacta.
        """
        from core.security.file_security import FileSecurity

        folder_match = self._NATURAL_FILE_RE.search(text)
        folder = ""
        if folder_match and folder_match.group("folder"):
            folder_raw = folder_match.group("folder").lower()
            if "raiz" in folder_raw:
                folder = ""  # raiz do C: — busca em todas as roots
            elif "download" in folder_raw:
                folder = "downloads"
            elif "imagem" in folder_raw:
                folder = "imagens"
            elif "documento" in folder_raw:
                folder = "documentos"
            elif "trabalho" in folder_raw or "desktop" in folder_raw or "mesa" in folder_raw:
                folder = "desktop"

        name_match = re.search(
            r"\barquivo\s+(?:chamado\s+)?(?P<name>[\w\-. ]+?\.[a-zA-Z0-9]{2,4})", text, re.IGNORECASE)
        if not name_match:
            return text
        name = name_match.group("name").strip()
        matches = FileSecurity.find_files(name, folder=folder) if folder else FileSecurity.find_files(name)
        if not matches:
            return text
        resolved = str(matches[0])
        # Substitui a frase toda por apenas o caminho real.
        replacement = f"o arquivo {resolved}"
        new_text = text[:name_match.start()] + replacement + text[name_match.end():]
        return new_text

    # Conectivos que separam etapas de um objetivo composto. O verbo da
    # ação seguinte (gere/crie/faça) é CAPTURADO e reinjetado na parte,
    # porque "leia X e gere um relatório" sem o verbo viraria "um relatório".
    _COMPOSITE_SPLIT = re.compile(
        r"\s*(?:e\s+)?(?=(?:depois|ent[ãa]o|em seguida|gere|crie|criar|fa[çc]a|monte|prepare)\b)",
        re.IGNORECASE)

    def _composite_plan(self, text: str):
        """Detecta objetivo composto ("leia X e gere um relatório").

        Divide a fala nos conectivos e exige que CADA parte resolva uma
        ferramenta real. Sem pelo menos 2 etapas válidas, devolve None — a
        fala segue pelo fluxo normal de ferramenta única.
        """
        parts = [p.strip() for p in self._COMPOSITE_SPLIT.split(text) if p.strip()]
        if len(parts) < 2:
            return None
        steps = []
        for part in parts:
            tool = self.tool_manager.resolve_for_text(part)
            if re.search(r"\bleia\b", part, re.IGNORECASE) and re.search(r"\w+\.csv", part, re.IGNORECASE) \
                    and self.tool_manager.get_tool("read_spreadsheet"):
                # "leia <arquivo>.csv" — leitura estruturada (linhas), não preview.
                tool = self.tool_manager.get_tool("read_spreadsheet")
            elif tool is None and re.search(r"\bleia\b", part, re.IGNORECASE) \
                    and re.search(r"\w+\.(?:pdf|txt|md|json|docx)", part, re.IGNORECASE):
                tool = self.tool_manager.get_tool("summarize_document")
            if tool is None or (tool.name not in self._TASK_TOOLS
                                and tool.name not in {"summarize_document", "read_spreadsheet", "read_file"}):
                return None  # parte não resolvida → não é plano composto válido
            steps.append({"tool": tool.name, "part": part})
        return steps

    def _run_composite_plan(self, text: str, steps):
        """Executa objetivo composto: cada etapa alimenta a seguinte via {prev}."""
        plan = []
        for step in steps:
            part_tool = self.tool_manager.get_tool(step["tool"])
            params = self._build_tool_parameters(step["part"], part_tool)
            if "request" in params:
                final_file_tool = step["tool"] in {"create_document_docx", "create_presentation_pptx", "create_spreadsheet_xlsx"}
                if not final_file_tool or step is not steps[-1]:
                    return None  # estrutura complexa em etapa não-final → IA
                # Etapa final de arquivo: estrutura vem do pedido + {prev}.
                if step["tool"] == "create_document_docx":
                    params = {"title": "Relatório", "content": "{prev}"}
                elif step["tool"] == "create_presentation_pptx":
                    params = {"title": "Apresentação", "slides": "{prev}"}
                else:  # create_spreadsheet_xlsx — CSV lido fornece headers E rows via {prev}
                    params = {"title": "Planilha", "headers": "{prev}", "rows": "{prev}"}
            if step["tool"] == "create_document_docx" and not params.get("content"):
                # Etapa final: o conteúdo vem da etapa anterior via {prev}.
                params["content"] = "{prev}"
                params["title"] = params.get("title") or "Relatório"
            if step["tool"] == "create_spreadsheet_xlsx" and "{prev}" not in str(params.get("headers", "")) and not params.get("rows"):
                # Dados da etapa anterior alimentam a planilha via {prev}.
                params["rows"] = "{prev}"
            plan.append({"tool": step["tool"], "params": params})
        if len(plan) < 2:
            return None

        from core.brain.task_runner import TaskRunner
        runner = TaskRunner(
            self.tool_manager,
            memory=getattr(self, "memory", None),
            on_confirm=self._confirm_high_risk_step,
        )
        callback = getattr(self, "tool_result_callback", None)
        if callable(callback):
            try:
                callback("__moon__", ("activate", ["Execução"]))
            except Exception:
                pass
        try:
            result = runner.run(text, plan=plan)
        finally:
            if callable(callback):
                try:
                    callback("__moon__", ("deactivate", ["Execução"]))
                except Exception:
                    pass
        if result["status"] != "completed":
            return f"Não consegui concluir todas as etapas: {result.get('summary', '')}"
        artifact_path = next(
            (s.get("validation", {}).get("path") or "" for s in result["steps"]
             if s.get("validation", {}).get("valid")), "")
        final_tool = self.tool_manager.get_tool(plan[-1]["tool"])
        # "status": "created" é OBRIGATÓRIO: sem ele o arquivo não é
        # reconhecido como criado e nunca é aberto (mesmo bug do currículo).
        response = self._format_tool_result(final_tool, {
            "ok": True, "status": "created",
            **({"path": artifact_path} if artifact_path else {}),
        })
        self._store_tool_receipt(plan[-1]["tool"], result, user_question=text)
        return response

    def _process_tool_request(self, text):
        """Resolve e executa uma ferramenta externa quando o pedido tiver contexto adequado."""
        # Referências naturais de arquivo ("meus downloads", "na raiz do pc
        # chamado X") viram caminhos reais antes de qualquer roteamento.
        text = self._resolve_natural_files(text)
        # PERGUNTA SOBRE MEMÓRIA: "onde fica a chave do sofrimento?" era roteada
        # para geocoding (a VEGA tentava geolocalizar um objeto da casa — bug
        # real). Se o assunto da pergunta casa com uma memória guardada
        # ("lembre que a chave fica na caixa"), a memória responde ANTES de
        # qualquer ferramenta externa.
        memory_answer = self._answer_from_memory(text)
        if memory_answer is not None:
            return memory_answer
        # Planos compostos ("leia X e gere um relatório") vão primeiro para o
        # TaskRunner, que divide em etapas encadeadas com passagem de resultado.
        composite = self._composite_plan(text)
        if composite:
            return self._run_composite_plan(text, composite)
        tool = self.tool_manager.resolve_for_text(text)
        if tool is None or tool.implementation is None:
            return None
        # Objetivos de documento vão pelo TaskRunner (validação de artefato).
        if tool.name in self._TASK_TOOLS:
            return self._run_task_goal(text, tool)
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
            # Acende a lua Execução enquanto a ferramenta roda (igual ao
            # caminho do modelo em _execute_registered_tool_call — sem isso,
            # pedidos diretos tipo "cotação do dólar" nunca acendiam a lua).
            callback = getattr(self, "tool_result_callback", None)
            if callable(callback):
                try:
                    callback("__moon__", ("activate", ["Execução"]))
                except Exception:
                    pass
            # Ferramentas visuais (busca real na internet e geração por IA)
            # rodam em DAEMON THREAD: a fala NUNCA espera a rede, o resultado
            # chega na galeria via callback. Um único critério, sem ifs.
            if tool.name in {"image_search", "image_generation"}:
                params["background"] = True
            result = tool.implementation(**params)
            # Apaga a lua Execução com fade (a ferramenta terminou).
            if callable(self.tool_result_callback):
                try:
                    self.tool_result_callback("__moon__", ("deactivate", ["Execução"]))
                except Exception:
                    pass
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
            # Notícias: fotos DO SITE de cada manchete (og:image do artigo) em
            # background — a fala não espera a rede.
            if tool.name == "news" and isinstance(result, dict):
                news_items = result.get("items") or result.get("headlines") or []
                if news_items:
                    self._trigger_news_images(news_items, query_label="fotos das notícias de hoje")
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
            # O usuário não sabe o que é "ferramenta geocoding" — mensagem interna
            # vazava o nome técnico e o erro bruto da API na fala (bug real: "onde
            # fica a chave do sofrimento?" respondia "Não consegui usar a
            # ferramenta geocoding agora: Nenhum resultado encontrado...").
            tool_label = self._TOOL_FRIENDLY_LABELS.get(tool.name, "")
            message = str(error)
            if "Nenhum resultado encontrado" in message or "não encontrad" in message.lower():
                base = f"Não encontrei nenhum lugar chamado '{message.rsplit("'", 1)[-2] if "'" in message else ''}'." if tool.name == "geocoding" else "Não encontrei nada sobre isso."
                return f"{base} Se for um lugar de verdade, posso procurar a cidade ou endereço — me diz qual."
            if tool_label:
                return f"Não consegui consultar {tool_label} agora. Tente de novo em instantes."
            return "Não consegui fazer essa consulta agora. Tente de novo em instantes."

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
            # O texto original vai junto: o handler pode ter um PEDIDO EXTRA
            # na mesma frase ("quero, e pausa a música") — o sim confirma,
            # mas o comando citado precisa chegar ao parser também.
            return pending["handler"](answer, text)
        except TypeError:
            # Handler antigo (assinatura só com o booleano).
            try:
                return pending["handler"](answer)
            except Exception as error:
                print(f"[VEGA Pergunta] Falha ao responder: {error}")
                return None
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

    # --------------------------------------------------------------
    # VISUAL DAS NOTÍCIAS: fotos DO SITE de cada manchete lida
    # --------------------------------------------------------------

    def _trigger_news_images(self, items, *, query_label: str = ""):
        """Fotos REAIS da página de cada notícia que a VEGA está lendo.

        NÃO é busca genérica por tema (image_search): é a FOTO DO ARTIGO —
        og:image da página que o próprio portal declara. "Acidente de moto
        em SP" mostra a foto real do acidente, não uma moto qualquer.

        Roda em daemon thread (a fala NUNCA espera a rede). Quando pronto,
        entra pelo MESMO canal da linguagem visual (set_update_callback ->
        image_result_updated) e aparece na galeria/aba Imagens.
        Falha é silenciosa: a leitura segue sem foto.
        """
        items = [item for item in (items or []) if isinstance(item, dict)]
        if not any(str(item.get("link") or "").strip() for item in items):
            return

        def worker():
            try:
                from tools.news_image_extractor import shared_news_extractor
                from tools.image_tools import record_last_images, get_last_images, set_update_callback

                extractor = shared_news_extractor()
                images = extractor.fetch_for_items(items)
                if not images:
                    return
                result = {
                    "paths": [img["path"] for img in images],
                    "path": images[0]["path"],
                    "query": query_label or "fotos das notícias de hoje",
                    "images": images,
                    "source": images[0].get("source", ""),
                    "pending": False,
                }
                record_last_images(result)
                callback = getattr(self, "news_images_callback", None)
                if callable(callback):
                    try:
                        callback(result)
                        return
                    except Exception:
                        pass
                # Fallback: canal global da linguagem visual.
                from tools import image_tools as _it
                callback = getattr(_it, "_update_callback", None)
                if callable(callback):
                    try:
                        callback(result)
                    except Exception:
                        pass
            except Exception as error:
                print(f"[VEGA Notícias] Falha ao buscar fotos dos sites: {error}")

        threading.Thread(target=worker, daemon=True).start()

    def _store_tool_receipt(self, tool_name, result, user_question=""):
        """Guarda o dado concreto obtido por uma ferramenta.

        É o que impede o buraco do "e por que isso?": a resposta da ferramenta
        fica no histórico como fala da VEGA, e o prompt proíbe o modelo de tratar
        fala da VEGA como fato. O recibo é dado de API com origem explícita, então
        entra no contexto sem esse conflito.

        História curta: os últimos recibos continuam disponíveis — perguntas
        de acompanhamento se referem ao que a VEGA disse AINDA antes, não só
        ao último turno ("e a cotação de ontem?" depois de um clima).
        """
        try:
            receipt = build_receipt(tool_name, result, user_question=user_question)
        except Exception as error:
            print(f"[VegaTools] Não foi possível registrar o recibo: {error}")
            return None
        if receipt is not None:
            self.last_tool_receipt = receipt
            history = getattr(self, "tool_receipt_history", None)
            if history is None:
                history = []
                self.tool_receipt_history = history
            # Dedup pelo assunto: a MESMA ferramenta sobre o MESMO assunto
            # atualiza o recibo existente em qualquer posição — "dólar" →
            # "clima" → "dólar de novo" substitui o valor, não empilha duas
            # cotatções (o antecedente da conversa continua único e correto).
            existing = next(
                (
                    index
                    for index, old in enumerate(history)
                    if old.tool == receipt.tool and old.topic == receipt.topic
                ),
                None,
            )
            if existing is not None:
                history[existing] = receipt
            else:
                history.append(receipt)
            del history[:-3]  # só os 3 mais recentes interessam
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
        """Bloco de sistema com os dados verificados recentes (vazio se esfriou)."""
        blocks = []
        history = getattr(self, "tool_receipt_history", None) or []
        if history:
            blocks = [b for b in (
                receipt.to_context_block() for receipt in history
            ) if b]
        else:
            receipt = getattr(self, "last_tool_receipt", None)
            if receipt is not None:
                try:
                    block = receipt.to_context_block()
                    if block:
                        blocks.append(block)
                except Exception:
                    pass
        if not blocks:
            return ""
        # O mais recente por último: fica colado ao final do prompt, mais
        # perto da fala atual, que é o antecedente provável.
        return "\n\n".join(blocks)

    def _interface_context(self):
        """Bloco de sistema com o ESTADO da interface da VEGA.

        Lista o que está aberto na tela: TODOS os cards/abas (clima, cotação,
        notícias, fotos, players de vídeo/música com o conteúdo). É o que
        permite referências contextuais funcionarem em QUALQUER ferramenta:
        "fecha o card de clima" e "tira isso" precisam que a VEGA lembre do
        que ELA mesma colocou lá — sem isto, a IA nunca sabe o que está na
        interface e não consegue controlar o que mostra.
        """
        lines = []
        try:
            workspace = getattr(self, "card_workspace", None)
            describe = getattr(workspace, "state", None) and workspace.state.describe()
            if describe:
                lines.extend(describe)
        except Exception:
            pass

        # Players internos (YouTube/Spotify) ficam fora do card_workspace:
        # entram aqui para a IA saber o que está tocando/aberto também.
        try:
            open_players = self.commands._open_players()
        except Exception:
            open_players = {}
        label = {"video": "vídeo", "music": "música"}
        for kind, info in open_players.items():
            query = str(info.get("query") or "").strip()
            if query:
                lines.append(f"player de {label.get(kind, kind)}: {query}")

        if not lines:
            return ""
        header = ["O QUE A VEGA TEM ABERTO NA INTERFACE AGORA (ela mesma colocou):"]
        header.extend(f"- {line}" for line in lines)
        header.append(
            "- Quando a pessoa se referir a isso ('tira isso', 'fecha o card de "
            "clima', 'tira o felipe neto'), ela quer remover/controlar EXATAMENTE "
            "o que está listado acima — não pergunte o que é."
        )
        return "\n".join(header)

    def _execute_registered_tool_call(self, name, arguments):
        """Execute only a registered tool with the model-provided arguments."""
        # Acende a lua Execução enquanto uma ferramenta roda (fade ao fim).
        callback = getattr(self, "tool_result_callback", None)
        if callable(callback):
            try:
                callback("__moon__", ("activate", ["Execução"]))
            except Exception:
                pass
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
            elif spec.name in {"image_search", "image_generation"}:
                # Ferramentas visuais NÃO bloqueiam a fala: rodem em
                # background (daemon thread) e a galeria se atualiza via sinal
                # quando pronta. O modelo recebe só o recibo.
                result = spec.implementation(**arguments, background=True)
            else:
                result = spec.implementation(**arguments)
            if spec.name == "report":
                self.memory.add_report_event(result)
            if callable(self.tool_result_callback):
                try:
                    self.tool_result_callback(spec.name, result)
                except Exception as error:
                    print(f"[VegaTools] Não foi possível exibir o resultado: {error}")
            # Notícias via function-calling: mesmas fotos do site em background.
            if spec.name == "news" and isinstance(result, dict):
                news_items = result.get("items") or result.get("headlines") or []
                if news_items:
                    self._store_tool_receipt(spec.name, result)
                    self._trigger_news_images(news_items, query_label="fotos das notícias de hoje")
            # TODA ferramenta com dado factual deixa recibo (cotação, clima,
            # dicionário, filme...) — sem isto, o modelo chamando a ferramenta
            # direto perdia o dado no turno seguinte ("e por que isso?").
            elif isinstance(result, dict) and not result.get("error"):
                self._store_tool_receipt(spec.name, result)
            return result
        except Exception as error:
            print(f"[VegaTools] Erro na ferramenta {spec.name}: {error}")
            return {"error": f"Não consegui usar a ferramenta {spec.name}: {error}"}

    # Números por extenso usados em falas de tempo ("em uma hora",
    # "em meia hora", "em quinze minutos"). O STT quase nunca digita o dígito.
    _WORD_NUMBERS = {
        "uma": 1, "um": 1, "duas": 2, "dois": 2, "tres": 3, "três": 3,
        "quatro": 4, "cinco": 5, "dez": 10, "quinze": 15, "vinte": 20,
        "trinta": 30, "quarenta e cinco": 45, "meia": 0.5,
    }

    def _parse_duration_words(self, text):
        """Converte "em uma hora" / "em meia hora" / "em quinze minutos".

        Devolve (valor, unidade) ou None. O regex principal só aceita dígitos;
        o STT, porém, quase sempre transcreve o número POR EXTENSO — sem isto,
        "me lembre de ligar em uma hora" não criava lembrete nenhum.
        """
        match = re.search(
            r"em\s+(meia|uma|um|duas|dois|tres|três|quatro|cinco|dez|quinze|vinte|trinta|quarenta\s+e\s+cinto|quarenta\s+e\s+cinco)\s+(hora|horas|minuto|minutos)\b",
            text,
        )
        if not match:
            return None
        word, unit = match.group(1), match.group(2)
        value = self._WORD_NUMBERS.get(word)
        if value is None:
            return None
        if unit.startswith("hora"):
            return value, "hora"
        # "meia hora" = 30 minutos; "quinze minutos" = 15 minutos.
        if value <= 1 and word == "meia":
            return 0.5, "hora"
        return value, "minuto"

    def _parse_time_of_day(self, text):
        """Converte "as 7 da tarde" / "as 15 horas" em (hora, minuto) 24h.

        "às 7" sem qualificador = manhã (7:00); "às 7 da tarde/noite" = 19:00.
        Devolve None se não houver hora explícita.
        """
        match = re.search(
            r"(?:as|às|a[s]?|pra[s]?|para[s]?)\s+(\d{1,2})(?::(\d{2}))?\s*"
            r"(horas?|da\s+manha|da\s+tarde|da\s+noite|h)?",
            text,
        )
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        qualifier = (match.group(3) or "").lower()
        if hour < 1 or hour > 23:
            return None
        if "tarde" in qualifier and hour < 12:
            hour += 12
        elif "noite" in qualifier and hour < 12:
            hour += 12
        return hour, minute

    def _process_scheduler_request(self, text):
        # LEMBRETE SEM PRAZO nenhum: "me lembre de comprar pão", "lembrete de
        # pagar o boleto". Antes só existia caminho com "em N minutos"/hora —
        # sem prazo a fala caia fora e ia para a IA, que não criava nada
        # (BUG REAL da bateria de áreas, 26/10/2026).
        simple = re.search(
            r"^(?:me\s+|meu\s+)?(?:lembrete|lembre|lembra|lembrem)(?:-me|\s+me)?\s+(?:de|que|para|pra)\s+(.+?)\s*$",
            text, re.IGNORECASE,
        )
        if simple:
            task = simple.group(1).strip()
            if task and not re.search(r"\b(?:em|as|às|pras|para as|para as)\s", task + " "):
                item = self.scheduler.add_in(task, 1.0)
                return f"Lembrete criado: {task}. Te aviso em breve."

        match = re.search(r"(?:lembre|lembra)(?:-me| me)?(?: que)? (.+?) em (\d+(?:\.\d+)?) (minutos?|horas?)$", text)
        if match:
            task = match.group(1).strip()
            amount = float(match.group(2))
            minutes = amount * (60 if match.group(3).startswith("hora") else 1)
            item = self.scheduler.add_in(task, minutes)
            return f"Lembrete criado para daqui a {amount:g} {match.group(3)}: {task}."

        # Número POR EXTENSO: "me lembre de ligar em uma hora",
        # "me lembre de tomar remédio em meia hora".
        if match is None:
            words = self._parse_duration_words(text)
            if words:
                value, unit = words
                lead = re.search(r"(?:lembre|lembra)(?:-me| me)?(?: que| de)?\s+(.+?)\s+em\s+", text)
                if lead:
                    task = lead.group(1).strip()
                    minutes = value * 60 if unit == "hora" else value
                    # "meia hora" é 0.5h = 30 min.
                    if value < 1:
                        minutes = value * 60
                    self.scheduler.add_in(task, minutes)
                    # "meia hora" fala melhor que "0.5 horas".
                    if value < 1:
                        spoken = "meia hora"
                    elif value == 1:
                        spoken = f"1 {unit}"
                    else:
                        spoken = f"{value:g} {unit}s"
                    return f"Lembrete criado para daqui a {spoken}: {task}."

        # HORA DO DIA: "me lembre de ligar pra mãe as 7 da tarde".
        tod = self._parse_time_of_day(text)
        if tod:
            hour, minute = tod
            lead = re.search(r"(?:lembre|lembra)(?:-me| me)?(?: que| de)?\s+(.+?)\s+(?:as|às|a[s]?|pra[s]?|para[s]?)\s+\d", text)
            if lead:
                task = lead.group(1).strip()
                target = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= datetime.now():
                    # Hora já passou hoje: agenda para amanhã (o esperado ao
                    # pedir "as 7 da tarde" às 20h de hoje).
                    target += timedelta(days=1)
                item = self.scheduler.add(task, target)
                when = target.strftime("%H:%M")
                return f"Lembrete criado para {when}: {task}."

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
            # Fala natural com preposição: "o que eu tenho pra hoje?" — antes
            # só a variante "para hoje"/"hoje" casava e a pergunta escapava
            # para a IA, que respondia a DATA em vez da agenda (bug real).
            "o que tenho pra hoje", "o que eu tenho pra hoje",
            "minha agenda pra hoje", "minha agenda para hoje",
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
            # Mapeamento de siglas comuns para capitais e estados brasileiros
            siglas = {
                "sp": "São Paulo",
                "rj": "Rio de Janeiro",
                "bh": "Belo Horizonte",
                "cwb": "Curitiba",
                "poa": "Porto Alegre",
                "bsb": "Brasília",
                "ssa": "Salvador",
                "rec": "Recife",
                "for": "Fortaleza",
                "vix": "Vitória",
                "mg": "Belo Horizonte",
                "pr": "Curitiba",
                "rs": "Porto Alegre",
                "df": "Brasília",
                "ba": "Salvador",
                "pe": "Recife",
                "ce": "Fortaleza",
                "sc": "Florianópolis",
                "go": "Goiânia",
            }
            # Verifica se há sigla isolada ou após climatempo/tempo/em/de
            sigla_city = None
            for token, full_name in siglas.items():
                if re.search(rf"\b{token}\b", norm_ascii):
                    sigla_city = full_name
                    break

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
            ) or sigla_city or self._extract_after_keywords(norm_ascii, ["em ", "na ", "no ", "cidade de ", "cidade "])
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
        if tool.name == "create_resume_docx":
            # Currículo: estrutura simples extraível da fala ("seções X e Y").
            # Sem seções enumeradas, devolve "request" → quem monta é a IA.
            section_match = re.search(
                r"\b(?:se[cç][oõ]es|seções|partes)\s+(.+)$", text, re.IGNORECASE)
            if not section_match:
                return {"request": text}
            raw = section_match.group(1)
            headings = [h.strip().title() for h in re.split(r"\s+e\s+|,", raw) if h.strip()]
            title_match = re.search(r"\bt[ií]tulo\s+([A-Z][\wÀ-ÿ]+(?:\s+[A-Z][\wÀ-ÿ]+){0,3})", text)
            return {
                "title": title_match.group(1) if title_match else "Currículo",
                "sections": [{"heading": h, "text": ""} for h in headings],
            }

        if tool.name == "create_spreadsheet_xlsx":
            # Planilha: extrai estrutura simples da fala —
            # "planilha de vendas com colunas produto e preço" →
            #   title=Vendas, headers=[Produto, Preço].
            # Sem colunas enumeradas → "request" → quem monta é a IA.
            title_match = re.search(
                r"\bplanilha\s+(?:de|do|da|das)\s+([\wÀ-ÿ]+(?:\s+[\wÀ-ÿ]+){0,2})",
                text, re.IGNORECASE)
            col_match = re.search(
                r"\b(?:colunas?|campos?)\s+(.+)$", text, re.IGNORECASE)
            if not col_match:
                return {"request": text}
            headers = [h.strip().title() for h in re.split(r"\s+e\s+|,", col_match.group(1)) if h.strip()]
            return {
                "title": title_match.group(1).title() if title_match else "Planilha",
                "headers": headers,
                "rows": [],
            }

        if tool.name == "create_presentation_pptx":
            # Apresentação: extrai título e assunto da fala —
            # "apresentação sobre o sistema solar com 5 slides" →
            #   title=O Sistema Solar, slides=esqueleto lógico.
            # "slides?" cobre o SINGULAR ("faça um slide sobre política").
            # Bullets detalhados por slide são da IA; só montamos esqueleto.
            about = re.search(
                r"\b(?:\w+\s+)?(?:apresenta\w*\w\w|slides?|powerpoint)\s+(?:sobre|de|do|da)\s+(.+)$",
                text, re.IGNORECASE)
            if not about:
                return {"request": text}
            subject_raw = about.group(1)
            # "... com N slides": separa a contagem do assunto.
            subject = re.sub(r"\s+com\s+\d+\s+slides\s*$", "", subject_raw, flags=re.IGNORECASE).strip()
            n_slides = re.search(r"\s+com\s+(\d+)\s+slides\s*$", subject_raw, re.IGNORECASE)
            count = max(3, min(12, int(n_slides.group(1)) if n_slides else 4))
            # Capa é adicionada pela própria ferramenta; geramos count-1 slides
            # de conteúdo para o total da apresentação == count.
            slides = [
                *[{"title": f"{subject.title()} — parte {i}", "bullets": ["Tópicos principais", "Exemplos"]}
                  for i in range(2, count)],
                {"title": "Conclusão", "bullets": ["Resumo dos pontos principais"]},
            ] if count > 2 else [{"title": subject.title(), "bullets": ["Visão geral"]}]
            return {"title": subject.title(), "slides": slides}

        if tool.name in {
            "create_document_docx",
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
            # Fonte pedida explicitamente ("notícias do G1", "le de novo as do
            # G1"): vira busca livre no Google News filtrada por site — o
            # pedido de repetir uma fonte NÃO é ignorado nem vira conversa.
            source = self._extract_news_source(normalized)
            if source:
                params["query"] = source
                params["topic"] = ""
            elif self._news_repeat_requested(norm_ascii):
                # "le dnv" / "de novo" sem citar fonte: repete a leitura
                # geral recente — sem filtro, as mesmas fontes de sempre.
                pass
            else:
                # Tema pedido ("notícias de tecnologia", "notícias de esportes")
                topic = self._extract_news_topic(norm_ascii)
                if topic:
                    params["topic"] = topic
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

        # ==========================================================
        # SPOTIFY — extrai o pedido da fala natural
        # ==========================================================
        if tool.name in {
            "spotify_play", "spotify_control", "spotify_now_playing",
            "spotify_playlists", "spotify_queue",
        }:
            return self._build_spotify_parameters(tool.name, norm_ascii, text)

        # Geração de imagem: o prompt é o PEDIDO INTEIRO do usuário (sem
        # inventar detalhes que ele não pediu). O modelo de IA traduz para
        # inglês quando chama via function-calling; no caminho local (regex),
        # o prompt cruza na língua original — o FLUX entende pt reasonably
        # bem, mas o ideal é o caminho do modelo.
        if tool.name == "image_generation":
            prompt = self._extract_image_gen_prompt(norm_ascii)
            if not prompt:
                return {}  # sem prompt não há o que gerar — segue para a IA
            params["prompt"] = prompt
            return params

        return {}

    # --------------------------------------------------------------
    # NOTÍCIAS: fonte e tema pedidos na fala
    # --------------------------------------------------------------

    # Fontes reais citáveis: "notícias do G1", "le de novo as da CNN",
    # "só as da BBC". Quando a pessoa nomeia a fonte, o pedido é FILTRAR
    # por ela — não uma fala genérica para a IA.
    _NEWS_SITES = {
        "g1": "g1.globo.com",
        "globo": "globo.com",
        "cnn": "cnn.com",
        "cnn brasil": "cnnbrasil.com.br",
        "bbc": "bbc.co.uk",
        "bbc brasil": "bbc.com",
        "folha": "folha.uol.com.br",
        "uol": "uol.com.br",
        "estadao": "estadao.com.br",
        "estadão": "estadao.com.br",
        "globo esporte": "globoesporte.globo.com",
        "ge globo": "ge.globo.com",
        "r7": "r7.com",
        "reuters": "reuters.com",
        "record": "r7.com",
        "terra": "terra.com.br",
        "ig": "ig.com.br",
        "band": "band.uol.com.br",
        "correio brasileirense": "correiobraziliense.com.br",
        "correio braziliense": "correiobraziliense.com.br",
        "oglobo": "oglobo.globo.com",
        "o globo": "oglobo.globo.com",

        "apnews": "apnews.com",
        "elpais": "elpais.com",
        "el pais": "elpais.com",
        "veja": "veja.abril.com.br",
        "istoe": "istoe.com.br",
        "poder360": "poder360.com.br",
        "metropoles": "metropoles.com",
        "gizmodo": "gizmodo.com",
        "the verge": "theverge.com",
        "tecmundo": "tecmundo.com.br",
        "olhar digital": "olhardigital.com.br",
    }

    @classmethod
    def _extract_news_source(cls, normalized: str) -> str:
        """Fonte de notícia nomeada na fala -> filtro de site (site:).

        Nomes compostos casam ANTES dos simples ("cnn brasil" vence "cnn").
        A fonte pode vir SEM a palavra "notícia" na fala — "le de novo a do
        G1", "e a da cnn?" (bug real do 24/10: sem a menção explícita, a
        fonte não era extraída e o pedido virava conversa).
        """
        text = str(normalized or "").lower()
        ordered = sorted(cls._NEWS_SITES.items(), key=lambda kv: -len(kv[0]))
        for name, domain in ordered:
            if re.search(rf"\b{re.escape(name)}\b", text):
                return f"site:{domain} quando:1d"
        return ""

    # Contexto da última leitura: "le dnv" sem fonte citada repete a leitura
    # GERAL — não filtra pela primeira fonte do recibo.
    LAST_NEWS_SOURCE_TTL = 600.0  # 10 min: manchete velha não é "a notícia"

    def _news_repeat_requested(self, norm_ascii: str) -> bool:
        """Fala pede REPETIR a leitura recente? ('le dnv', 'le de novo a do g1')

        Só é repetição quando há recibo de notícia fresco — sem isso,
        'de novo' solto não vira filtro de notícia por acidente.
        """
        text = str(norm_ascii or "")
        if not re.search(r"\b(?:de novo|denovo|dnv|de nv|mais uma vez|outra vez)\b", text):
            return False
        receipt = getattr(self, "last_tool_receipt", None)
        return bool(receipt and getattr(receipt, "tool", "") == "news" and receipt.is_fresh())

    @staticmethod
    def _extract_news_topic(norm_ascii: str) -> str:
        """Tema pedido em 'notícias de X' / 'notícias sobre X'."""
        match = re.search(
            r"\bnot[íi]cias\s+(?:de|do|da|dos|das|sobre)\s+(\S.*)$",
            norm_ascii or "",
        )
        if not match:
            return ""
        subject = match.group(1).strip()
        # Remove fechos de fala que não são tema ("de hoje", "agora").
        subject = re.sub(r"\b(de\s+)?hoje\b.*$", "", subject).strip()
        subject = re.sub(r"\b(por favor|agora|pra mim|para mim)\b", "", subject).strip()
        return subject.strip(" ,.?")

    def _extract_image_gen_prompt(self, norm_ascii: str) -> str:
        """Tira o ASSUNTO do pedido: 'crie uma imagem de um dragão' ->
        'um dragão'. Preserva a intenção do usuário, remove só a moldura
        do pedido (verbo + palavra imagem/capa/ícone)."""
        # Corta no gatilho 'imagem/capa/ícone ... de/com/sobre' e pega o resto.
        match = re.search(
            r"\b(?:imagem|imagens|capa|ícone|icone|arte|ilustra[çc][aã]o|cen[áa]rio|desenho)\s+(?:de|do|da|dos|das|com|sobre)\s+(.+)$",
            norm_ascii,
        )
        if match:
            prompt = match.group(1).strip(" ?!").strip()
            # Remove coletivos de fecho que não descrevem a cena
            # ("por favor", "pra mim", "para mim").
            prompt = re.sub(r"\s+(?:por\s+favor|pra\s+mim|para\s+mim|pfv)\s*$", "", prompt)
            return prompt
        # Sem 'de/com/sobre': pedidos tipo 'gera uma capa para meu projeto'
        # ou 'faça um ícone quadrado' — o pedido inteiro é a descrição.
        match = re.search(
            r"\b(?:crie?|gera|gere|fa[çc]|faz|desenhe|pinta)\s+(.+)$",
            norm_ascii,
        )
        if match:
            return match.group(1).strip(" ?!").strip()
        return ""

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
        "create_resume_docx": "currículo",
        "create_presentation_pptx": "apresentação",
        "create_document_docx": "documento",
        "create_spreadsheet_xlsx": "planilha",
        "create_mindmap_image": "mapa mental",
        "draw_image": "desenho",
        "edit_video": "vídeo",
    }

    # Rótulos amigáveis para erros de ferramenta: a fala não expõe nomes
    # técnicos ("ferramenta spotify_play" significa nada para o usuário).
    _TOOL_FRIENDLY_LABELS = {
        "geocoding": "a localização",
        "weather": "o clima",
        "currency": "a cotação",
        "dictionary": "o dicionário",
        "movie": "o filme",
        "news": "as notícias",
        "spotify_play": "o Spotify",
        "spotify_control": "o Spotify",
        "spotify_queue": "a fila do Spotify",
        "spotify_playlists": "suas playlists",
        "spotify_now_playing": "o que está tocando",
        "timezone": "o fuso horário",
        "country": "os dados do país",
        "calendar": "a agenda",
        "image_search": "a busca de imagens",
        "image_generation": "a geração de imagem",
        "youtube_search": "o YouTube",
        "find_files": "a busca de arquivos",
    }

    def _announce_created_file(self, tool_name, result):
        """Abre o arquivo gerado no app padrão e devolve uma fala curta.

        O usuário pediu para VER o resultado. Devolver só o caminho em texto
        (ou mostrar num painel pequeno) fazia o trabalho parecer incompleto.
        """
        import os

        path = str(result.get("path") or "")
        label = self._CREATED_FILE_LABELS.get(tool_name, "arquivo")
        # Artigo correto: "a currículo" soa errado — usa o artigo do rótulo.
        article = "o " if label == "currículo" else "a "
        name = os.path.basename(path) if path else ""

        if not path or not os.path.exists(path):
            return f"Criei {article}{label}, mas não consegui localizar o arquivo para abrir."

        opened = False
        try:
            os.startfile(path)  # noqa: S606 — abre no app padrão do Windows
            opened = True
        except Exception as error:
            print(f"[VEGA] Não consegui abrir o arquivo gerado: {error}")

        if opened:
            return f"Pronto. Criei {article}{label} ({name}) e já abri para você."
        folder = os.path.dirname(path) or "sua pasta de usuário"
        return f"Criei {article}{label}: {name}. Está em {folder}."

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
            # Narração falada: separador "|" virava um emaranhado corrido na
            # voz. Frases com ponto dão pausa natural ao TTS.
            items = result.get("items") or result.get("headlines") or []
            titles = [str(item.get("title", "")).strip() for item in items[:3]]
            titles = [t for t in titles if t]
            if not titles:
                return "Não consegui achar manchetes agora."
            return "Aqui vão as principais notícias: " + ". ".join(titles) + "."

        if tool.name == "image_generation":
            if result.get("pending"):
                return "Estou gerando a imagem — fica pronta em instantes."
            if not result.get("ok"):
                return f"Não consegui gerar a imagem: {result.get('error', 'erro desconhecido')}"
            seconds = result.get("generation_seconds")
            timing = f" em {seconds} segundos" if seconds else ""
            return f"Imagem gerada{timing}. Está aí na tela para você ver."

        if tool.name == "spotify_play":
            status = result.get("status")
            query = result.get("query") or ""
            if status == "playing":
                return f"Tocando {query} no Spotify."
            if status == "playing_local":
                return f"Tocando {query} no app do Spotify."
            if status == "empty":
                return result.get("message") or "Você não tem curtidas no Spotify."
            if status == "not_found":
                return f"Não encontrei {query} no Spotify."
            if status == "resumed":
                return "Retomando a música."
            return "Não consegui tocar isso no Spotify agora."

        if tool.name == "spotify_control":
            status = result.get("status")
            if status == "volume":
                return f"Volume no {result.get('percent')} por cento."
            if status == "paused":
                return "Pausado."
            if status == "resumed":
                return "Tocando de novo."
            if status == "skipped_next":
                return "Próxima música."
            if status == "skipped_previous":
                return "Voltando."
            if status == "shuffle":
                return "Aleatório ativado." if result.get("state") else "Ordem normal ativada."
            return "Feito."

        if tool.name == "spotify_now_playing":
            if not result.get("playing"):
                return "Nada está tocando agora."
            return f"Tocando {result.get('title')} de {result.get('artist')}."

        if tool.name == "spotify_playlists":
            playlists = result.get("playlists", [])
            names = ", ".join(p.get("name", "") for p in playlists[:6])
            liked_total = result.get("liked_total", 0)
            if not playlists and not liked_total:
                return "Você ainda não tem playlists nem curtidas no Spotify."
            return (
                f"Você tem {liked_total} curtidas e suas playlists são: {names}."
            )

        if tool.name == "spotify_queue":
            if result.get("status") == "queued":
                return f"{result.get('title')} adicionada à fila."
            queue = result.get("queue", [])
            if not queue:
                return "A fila está vazia."
            first = queue[0]
            return f"A seguir: {first.get('title')} de {first.get('artist')}."
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

    # ==================================================================
    # SPOTIFY — extração de parâmetros da fala natural
    # ==================================================================

    _SPOTIFY_PLAY_TRIGGERS = (
        "toca", "toque", "toca", "toca a", "coloca a", "poe a",
        "põe a", "coloca o", "poe o", "põe o", "quero ouvir",
        "quero escutar", "manda um som", "solta o som",
    )
    _SPOTIFY_CONTROL_MAP = (
        (r"\b(?:paus[ae]r?|parar|para isso)\b", "pause"),
        (r"\b(?:continu[ae]r?|retomar|despaus[ae]r?|resum[ei]r?)\b", "resume"),
        (r"\b(?:proxim[ao] faixa|proxim[ao] musica|pula|pular|passa|proxima|prox)\b", "next"),
        (r"\b(?:anterior|volta|voltar|retrocede)\b", "previous"),
        (r"\b(?:tira o aleatorio|desembaralha|sem aleatorio|na ordem|sem embaralhar)\b", "shuffle_off"),
        (r"\b(?:aleatorio|embaralh[ae]r?|shuffle)\b", "shuffle_on"),
    )

    def _build_spotify_parameters(self, tool_name, norm_ascii, original_text):
        """Extrai query/ação/volume da fala para as ferramentas Spotify."""
        params = {}
        if tool_name == "spotify_play":
            query = self._extract_spotify_query(norm_ascii)
            if query:
                params["query"] = query
            return params
        if tool_name == "spotify_control":
            params.update(self._extract_spotify_control(norm_ascii))
            return params
        if tool_name == "spotify_queue":
            params["action"] = "add" if re.search(
                r"\b(?:adiciona|coloca|poe|põe|manda)\b", norm_ascii
            ) else "view"
            query = self._extract_spotify_query(norm_ascii)
            if query:
                params["query"] = query
            return params
        # now_playing e playlists não recebem nada da fala.
        return params

    def _extract_spotify_query(self, norm_ascii):
        """Tira o OBJETO do pedido: 'toca efe' -> 'efe'.

        Estratégia: corta no gatilho e limpa palavras de função (a, a
        playlist, minhas curtidas...), preservando o nome que interessa.
        Para pedidos de FILA ('adiciona X na fila'), extrai o objeto entre
        o verbo e o 'na/pra fila'.
        """
        # Fila: 'adiciona evidencias na fila' -> 'evidencias'.
        queue_match = re.search(
            r"\b(?:adiciona|coloca|poe|põe|manda)\s+(.+?)\s+(?:na|pra|para a|na proxima)\s+fila\b",
            norm_ascii,
        )
        if queue_match:
            return queue_match.group(1).strip(" ?!")
        for trigger in sorted(self._SPOTIFY_PLAY_TRIGGERS, key=len, reverse=True):
            match = re.search(rf"\b{re.escape(trigger)}\s+(.+)$", norm_ascii)
            if match:
                rest = match.group(1).strip()
                # Remove frases de função no início do resto.
                rest = re.sub(
                    r"^(?:a |o |as |os |uma |um |minha |meu |pela |pelo |"
                    r"playlist |lista |musica |musicas |sons? )+",
                    " ", rest,
                ).strip()
                # 'curtidas' vira pedido especial (a ferramenta sabe lidar).
                if re.match(r"^(?:minhas )?curtidas?", rest):
                    return "curtidas"
                return rest.strip(" ?!")
        return ""

    def _extract_spotify_control(self, norm_ascii):
        """Ação de controle + volume quando presente."""
        params = {}
        for pattern, action in self._SPOTIFY_CONTROL_MAP:
            if re.search(pattern, norm_ascii):
                params["action"] = action
                break
        volume_match = re.search(r"\b(?:volume|som)\s*(?:para|a|em|de)?\s*(\d{1,3})", norm_ascii)
        if volume_match:
            params["volume"] = int(volume_match.group(1))
            if not params.get("action"):
                params["action"] = "volume"
            return params
        if re.search(r"\b(?:aumenta|sube|sobe)\b", norm_ascii) and "volume" in norm_ascii or "aumenta o som" in norm_ascii:
            params["action"] = "volume_up"
        elif re.search(r"\b(?:abaixa|diminui|baixa)\b", norm_ascii) and ("volume" in norm_ascii or "som" in norm_ascii):
            params["action"] = "volume_down"
        return params

    @staticmethod
    def _extract_named_entity(text, candidates):
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate in text:
                return candidate
        return ""

    # ==========================================================
    # MULTI-INTENÇÃO — várias ações numa única fala
    # ==========================================================

    _MULTI_INTENT_SPLIT = re.compile(
        # Só CONSUME o conectivo (e/depois/então...), vírgula ou ponto-e-vírgula;
        # o verbo seguinte fica na cláusula nova — alternância que engolia "abre"/"toca"
        # cortava a segunda ação pela metade ("aumenta o volume e abre o bloco" virava
        # "... o bloco de notas").
        r"\s*(?:(?:,|;)\s*(?:\be\b|\bdepois\b|\bent[ãa]o\b)?|\b(?:e\s+depois|depois|e\s+ent[ãa]o|ent[ãa]o|e\s+tamb[ée]m|"
        r"tamb[ée]m|e\s+de\s+quebra|e\s+j[áa]\s+aproveita|e\s+aproveita|e)\b)\s*"
        r"(?=(?:me\s+)?(?:fala|diga|fale|faz|fa[çc]a|abre|abra|abrir|toca|toque|"
        r"tocar|mostra|mostre|pesquisa|pesquise|procura|procure|busca|busque|"
        r"coloca|coloque|tira|tire|fecha|feche|aumenta|abaixa|diminui|liga|desliga|quanto|qual|"
        r"que\s+horas|que\s+dia)\b)",
        re.IGNORECASE)

    def _process_multi_intent(self, text):
        """Executa VÁRIAS ações de uma única fala composta.

        "toca uma musica e abre a calculadora e me fala a previsão do tempo"
        tem TRÊS intenções. Antes, só a primeira era executada e o resto da
        frase virava lixo no comando (BUG REAL do Endriw, 21/10/2026).

        Divide a fala nos conectivos, tenta resolver cada cláusula
        independentemente (comando local ou ferramenta) e, se pelo menos 2
        forem resolvidas, executa todas e junta as respostas. Com menos de 2
        resolvidas devolve None — a fala segue pelo fluxo normal.
        """
        if not isinstance(text, str) or not text.strip():
            return None
        # Gate de custo: fala sem verbo de ação nunca vira multi-intenção.
        parts = [
            p.strip(" ,.!?;:")
            for p in self._MULTI_INTENT_SPLIT.split(text.strip())
            if p and p.strip(" ,.!?;:")
        ]
        if len(parts) < 2:
            return None
        # Pergunta pura de hora/data numa cláusula é atendida de graça.
        resolved = []  # (cláusula, resposta)
        for part in parts:
            time_answer = answer_time_or_date_question(part)
            if time_answer:
                resolved.append((part, time_answer))
                continue
            response = self._process_command(part)
            if response is None:
                response = self._process_tool_request(part)
            if response is None:
                return None  # uma cláusula não resolvida: fluxo normal decide
            resolved.append((part, response))
        if len(resolved) < 2:
            return None
        # Executa de fato e junta: cada cláusula já executou sua ação dentro
        # de _process_command/_process_tool_request acima.
        return " ".join(response for _, response in resolved)

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
            # TÓPICO DE MÍDIA: quando a VEGA toca/abre mídia, registra no
            # orquestrador — "o que vc acha dessa musica?" e "volta para o
            # video" precisam saber O QUE está tocando/aberto. Sem isto, o
            # "dessa" não tinha antecedente e a IA respondia no vácuo.
            if response and getattr(self, "orchestrator", None) is not None:
                try:
                    players = self.commands._open_players() if hasattr(self.commands, "_open_players") else {}
                    for kind, info in players.items():
                        label = "a música" if kind == "music" else "o vídeo"
                        self.orchestrator.note_tool_receipt(
                            f"{label} {info.get('query', '')} está aberto na interface",
                            f"{label} {info.get('query', '')}",
                        )
                except Exception:
                    pass
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

        # FIREWALL DE DOMÍNIO: quando a fala claramente pede CRIAÇÃO de
        # conteúdo (slide/apresentação/documento/planilha), nenhuma intenção
        # de MÍDIA (música, vídeo, player) pode executar — nem se o modelo
        # classificar errado. Foi exatamente o bug real: "faça um slide sobre
        # política" caiu no router e TOCOU MÚSICA. A fala errada volta None e
        # segue o fluxo de conversa, que entende o pedido de criação.
        folded = re.sub(r"[^a-z0-9\s]", " ", unicodedata.normalize("NFKD", str(text or "").lower()))
        folded = re.sub(r"\s+", " ", "".join(
            c for c in folded if not unicodedata.combining(c)
        )).strip()
        wants_creation = re.search(
            r"\b(?:slide|slides|apresentac\w+|powerpoint|pptx?|documento|docx|relatorio|planilha|xlsx|spreadsheet|redacao|trabalho)\b",
            folded,
        )
        media_intents = (
            "play_music", "music", "spotify", "media", "video", "youtube",
            "trending", "player", "song",
        )
        if wants_creation and any(term in intent.lower() for term in media_intents):
            print(f"[VEGA Router] Bloqueado: fala pede criação de conteúdo, "
                  f"mas o modelo classificou '{intent}'. Segue fluxo normal.")
            return None

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
    def _looks_like_pending_security_reply(text):
        """A fala é uma resposta plausível à pendência de ação perigosa?

        Confirmações ("sim, confirmo", "pode", PIN, "confirma e apaga")
        interceptam. Falas sobre outro assunto ("que horas são", "notícias",
        "toca playlist", "lembre que...") NÃO — elas seguem o fluxo normal
        e a pendência continua esperando. Sem isto, uma pendência esquecida
        sequestrava a VEGA inteira.
        """
        normalized = (text or "").lower().strip()
        if not normalized:
            return False
        # PIN ditado/digitado é sempre resposta à pendência.
        if VegaBrain._extract_security_pin(normalized):
            return True
        if VegaBrain._has_security_confirmation(normalized):
            return True
        # Falas curtas: só se forem palavras de CONFIRMAÇÃO/NEGAÇÃO conhecidas
        # ("sim", "pode", "não", "confirmo", "ok"). Qualquer outra coisa
        # ("noticias", "esporte", "volume") é outro assunto — segue o fluxo.
        short_words = {
            "sim", "pode", "pode sim", "confirmo", "confirmar", "ok", "beleza",
            "blz", "certo", "certeza", "manda", "vai", "faz", "apaga",
            "nao", "não", "nenhum", "negativo", "para", "parar", "cancela",
        }
        if len(normalized.split()) <= 2:
            return any(w in normalized for w in short_words)
        # Escolha de pasta/arquivo: a pendência pergunta "em qual pasta?" ou
        # "qual dos arquivos?", então uma resposta que cita "pasta" ou
        # "arquivo" é claramente a resposta à pendência (ex.: "na pasta vídeos").
        if "pasta" in normalized or "arquivo" in normalized:
            return True
        # Frases sobre outro assunto: não é resposta.
        return False

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
        #
        # ESCAPE HATCH: palavras de CANCELAMENTO liberam a pendência na hora.
        # Sem isto, uma pendência esquecida sequestrava TODAS as falas seguintes
        # ("que horas são" recebia "Ação perigosa pendente") e a VEGA ficava
        # presa até o usuário adivinhar o ritual de confirmação.
        import re as _re
        if _re.search(r"\b(cancel(?:a|ar|ela)?|desist(?:o|ir)|esquece|esque\u00e7a|deixa)\b", text):
            self._pending_dangerous_action = None
            return "Ação perigosa cancelada. Pode falar o que quiser."
        # CONFIRMAÇÃO OU OUTRO ASSUNTO?
        # Se a fala é claramente sobre OUTRO assunto (pergunta de hora/data,
        # ferramenta, música, lembrete...), ela NÃO deve ser capturada pela
        # pendência — responde-se a fala e a pendência continua esperando.
        # Só intercepta: confirmação explícita, PIN, ou fala ambígua curta
        # ("sim", "pode", "confirmo") que plausivelmente responde à pendência.
        if not self._looks_like_pending_security_reply(text):
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

    def _answer_from_memory(self, text):
        """Responde perguntas cuja resposta JÁ está na memória permanente.

        Devolve str quando a memória responde; None quando a fala deve seguir
        o fluxo normal (ferramenta/IA). Regras:
        - Pergunta de lugar ("onde fica X?", "onde está X?", "cadê X?")
          consultada ANTES de geocoding: se existe memória sobre X, responda
          por ela. Cidades/países reais continuam indo ao geocoding porque
          "onde fica Paris" não tem memória (a menos que o usuário tenha
          guardado uma — nesse caso a memória dele vence, o que é correto).
        - Pergunta genérica ("quando é o aniversário da minha mãe?") acha a
          memória pelo assunto.
        """
        if not text:
            return None
        normalized = text.strip().lower().rstrip("?!").strip()
        if not normalized:
            return None

        place = re.match(
            r"(?:onde|aonde)\s+(?:fica|ficam|esta|está|estao|estão|tá|ta)\s+(?:o|a|os|as|meu|minha)?\s*(.+)$",
            normalized,
        )
        subject = None
        if place:
            subject = place.group(1).strip()
        else:
            # Pergunta direta sobre o assunto guardado: "quando é o
            # aniversário da minha mãe?", "qual o meu pin?" etc.
            # "esse/esse tal de/isso" marciam o assunto: "quem é ESSE tal de
            # doctor doom" capturava "esse tal de doctor" em vez da pessoa.
            # O "e/é" é OPCIONAL: "qual o princípio da vega?", "quem Dom
            # Casmurro" não têm verbo, mas são perguntas de memória legítimas.
            m = re.match(
                r"(?:quando|qual|quais|quem|o que|que)\s+(?:(?:e|é|sao|são|era|foi)\s+)?"
                r"(?:o|a|os|as|meu|minha|esse|essa|isso|ese|esa)?\s*"
                r"(?:tal\s+de\s+)?(.+)$",
                normalized,
            )
            if m:
                subject = m.group(1).strip()
        if not subject:
            return None

        # Remove Artigos/referências possessivas residuais.
        subject = re.sub(r"^(?:o|a|os|as|meu|minha|meus|minhas)\s+", "", subject).strip("?!").strip()
        if not subject:
            return None

        permanent = self.memory.get_permanent()
        subject_words = set(re.findall(r"\w{3,}", subject))
        if not subject_words:
            return None

        best_key, best_value, best_score = None, None, 0
        for key, memory in permanent.items():
            value = memory.get("value") if isinstance(memory, dict) else memory
            if not value:
                continue
            hay = f"{key} {value}".lower()
            words = set(re.findall(r"\w{3,}", hay))
            overlap = len(subject_words & words)
            if overlap > best_score:
                best_key, best_value, best_score = key, value, overlap

        if best_score >= 1 and best_key:
            # Anti-falso-positivo (bug do Endriw, 28/10/2026): "quem é esse
            # tal de doctor doom" casava com "principio da vega" porque as
            # memórias de identidade da VEGA são LONGAS e citam muitas
            # palavras comuns — qualquer pergunta sobre pessoa/lugar que
            # tivesse 1 palavra fora do vocabulário delas acabava 'respondida'
            # por uma memória que nada tinha a ver. A memória só responde
            # quando o ASSUNTO dela realmente aparece na pergunta (palavra
            # da CHAVE presente na fala), não basta colisão no corpo do valor.
            folded_key_words = set(re.findall(r"\w{3,}", best_key.strip().lower()))
            folded_value_words = set(re.findall(r"\w{3,}", str(best_value).strip().lower()))
            if not (folded_key_words & subject_words) and not (
                folded_value_words & subject_words
            ):
                return None
            # Chave contida no valor (ou genérica): só o fato, sem repetição
            # ("a reuniao e quinta a reuniao e quinta as 10" — bug de fala).
            folded_key = best_key.strip().lower()
            folded_value = str(best_value).strip().lower()
            if (
                folded_key in {"informacao", "informação", "geral", "nota"}
                or folded_key in folded_value
                or folded_value.startswith(folded_key)
            ):
                return f"Pela minha memória: {best_value}."
            # Fórmula genérica: chave não aparece nem na fala nem no início do
            # valor (ex.: "qual e o meu nome" -> "origem do nome vega ...").
            # Monta a frase com a CHAVE como rótulo: lê natural.
            return f"Pela minha memória: {best_key} {best_value}."
        return None

    def _remember(self, text):

        content = text[
            len("lembre que "):
        ].strip()

        if not content:

            return (
                "O que você quer "
                "que eu lembre?"
            )

        folded = content.lower()

        # "X fica na/em/no Y" — a chave vira X (o assunto), o valor vira Y.
        # Antes, sem o " é ", caía na chave genérica "informacao", que é
        # SOBRESCRITA pelo próximo lembrete — a pergunta seguinte ("onde fica
        # a chave do sofrimento?") não achava nada (bug real do dia a dia).
        place_match = re.match(
            r"(.+?)\s+(?:fica|ficam|está|esta|ta|estão|estao)\s+(na|no|nas|nos|em)\s+(.+)$",
            folded,
        )
        if place_match:
            key = place_match.group(1).strip()
            # Mantém a preposição ORIGINAL no valor: "fica na caixa vermelha",
            # não "fica caixa vermelha" (a fala soava errada — bug real).
            value = f"fica {place_match.group(2).strip()} {place_match.group(3).strip()}"
            self.memory.remember(
                key,
                value,
                category="pessoal",
                importance=1.0,
                source="explicit",
            )
            return f"Beleza. Vou lembrar que {key} {value}."

        if " é " in folded:

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

        # Sem estrutura "X é/fica Y": guarda por CHAVE DERIVADA do assunto
        # (primeiras palavras), não na chave única "informacao" — que era
        # SOBRESCRITA pelo próximo lembrete e perdia o anterior (bug real:
        # "lembre que minha mãe faz aniversário dia 12" era apagado pelo
        # lembrete seguinte).
        key = " ".join(folded.split()[:4]).strip()
        self.memory.remember(
            key,
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
    # LIMPAR MEMÓRIAS DIÁRIAS (histórico conversacional)
    # ==========================================================

    _DAILY_MEMORY_WIPE_RE = re.compile(
        r"\b(?:deleta|deletar|delete|apaga|apagar|apague|limpa|limpar|limpe|"
        r"zera|zerar|esvazia|esvaziar|exclua|excluir|remova|remover)\b[^.]{0,40}"
        r"\b(?:memori(?:a|as)|memoria|memorias)\b[^.]{0,30}\b(?:diari(?:a|as|o|os)|"
        r"do dia|cotidian[ao]|temporari(?:a|as|o|os)|todas|tudo|toda)\b|"
        r"\b(?:deleta|deletar|delete|apaga|apagar|apague|limpa|limpar|limpe|"
        r"zera|zerar|esvazia|esvaziar)\b[^.]{0,20}\b(?:historico|hist[oó]rico|"
        r"conversas?|conversas? anteriores)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _is_daily_memory_wipe_request(cls, text):
        """Detecta "deleta todas as suas memórias diárias" e variações.

        Cuidado com a colisão: "esqueça X" apaga UM fato permanente e é
        tratado antes; aqui é só o pedido de ZERAR o histórico diário.
        "Memórias permanentes" NÃO casa (e não deve): a permanente é
        intocável por comando — só fato a fato via "esqueça".
        """
        normalized = str(text or "").lower()
        if not cls._DAILY_MEMORY_WIPE_RE.search(normalized):
            return False
        # Proteção explícita: se o pedido mencionar "permanente", não é wipe
        # diário (e a resposta explica por que não executa).
        return "permanente" not in normalized and "permanentes" not in normalized

    def _wipe_daily_memories(self, text):
        removed = self.memory.clear_daily()
        # O contexto de conversa em RAM também morre: sem isso, a VEGA
        # continuaria "lembrando" o que falou nesta sessão mesmo com os
        # arquivos apagados.
        conversation = getattr(self, "conversation", None)
        if conversation is not None and hasattr(conversation, "clear"):
            try:
                conversation.clear()
            except Exception:
                pass
        orchestrator = getattr(self, "orchestrator", None)
        if orchestrator is not None and hasattr(orchestrator, "clear"):
            try:
                orchestrator.clear()
            except Exception:
                pass
        if removed:
            return (
                f"Pronto, apaguei {removed} arquivo(s) de memória diária. "
                "Minha memória permanente sobre você continua intacta."
            )
        return (
            "Não havia memórias diárias para apagar — já estava limpo. "
            "Minha memória permanente segue intacta."
        )

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
