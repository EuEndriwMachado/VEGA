from datetime import datetime
from pathlib import Path
import json

from core.logic.ai import VegaAI
from core.conversation import Conversation
from core.logic.memory import VegaMemory
from core.logic.commands import VegaCommands
from core.logic.commands_parser import VegaCommandsParser
from core.logic.modes import VegaModes
from core.logic.text_utils import normalize_text, remove_vega_prefix, is_short_nonsense
from core.logic.time_answer import answer_time_or_date_question, portuguese_number
from core.logic.memory_context import inject_memory
from core.logic.brain_view import is_brain_view_request
from core.logic.daily_query import DailyQuery
from config.ai import MAX_HISTORY
from config import file_access
from config.personality import is_serious_context, evolve_personality
from tools.api_tools import build_tool_registry
from core.logic.scheduler import VegaScheduler
from core.logic.security import VegaSecurity
from core.logic.file_security import FileSecurity
from core.agent_framework import AgentOrchestrator
import re
import unicodedata
from datetime import timedelta


class VegaBrain:

    def __init__(self):

        self.ai = VegaAI()

        self.memory = VegaMemory()
        self.brain_view_callback = None
        self.database_view_callback = None
        self.daily_query = DailyQuery(self.memory)

        self.conversation = Conversation(
            max_history=MAX_HISTORY
        )
        self.environment_callback = None
        self.holographic_command_callback = None

        # ======================================================
        # COMANDOS
        # ======================================================

        self.commands = VegaCommands(ai=self.ai)

        self.commands_parser = VegaCommandsParser(
            self.commands
        )

        self.tool_manager = build_tool_registry()
        self.orchestrator = AgentOrchestrator()
        self.last_screen_summary = None
        self.security = VegaSecurity()
        self._pending_dangerous_action = None
        self._last_opened_app = None
        self._pending_close_app = None
        self.scheduler = VegaScheduler(on_due=self._on_scheduled_item)

    def _on_scheduled_item(self, item):
        self.memory.add_daily_message("system", f"Lembrete: {item.get('text', '')}")

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
        if not normalized:
            return False

        if "mapa mental" in normalized and self.last_screen_summary:
            return True

        triggers = (
            "olha isso na minha tela",
            "olha isso na tela",
            "olha a minha tela",
            "olha a tela",
            "analisa a tela",
            "analisa minha tela",
            "o que está na minha tela",
            "o que tem na minha tela",
            "olha o que está na tela",
            "olha isso",
        )

        return any(trigger in normalized for trigger in triggers)

    def _build_visual_prompt(self, text, previous_summary=None):
        normalized = self._normalize_text(text)
        prompt = (
            "Você está olhando a tela do usuário agora. "
            "Responda em português brasileiro, em 1 a 2 frases curtas, e diga "
            "algo como 'Estou vendo você lendo sobre X'. "
            "Se houver um tema principal visível, identifique-o com clareza."
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

    def _handle_visual_request(self, text):
        messages = [{
            "role": "user",
            "content": f"{text}\n\n{self._build_visual_prompt(text, self.last_screen_summary)}",
        }]

        response = self.ai.chat(messages, include_screen=True)
        if response:
            self.last_screen_summary = re.sub(r"\s+", " ", response).strip()[:500]
            self.conversation.add_user_message(text)
            self.conversation.add_assistant_message(response)
            self.memory.add_daily_message("user", text)
            self.memory.add_daily_message("assistant", response)

        return response or "Não consegui analisar a tela agora."

    def _handle_visual_request_stream(self, text):
        messages = [{
            "role": "user",
            "content": f"{text}\n\n{self._build_visual_prompt(text, self.last_screen_summary)}",
        }]

        full_response = ""
        for chunk in self.ai.chat_stream(messages, include_screen=True):
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

    def process(self, text):

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
            return "Estou ouvindo."

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
            return self._handle_visual_request(text)

        if self._is_database_view_request(normalized):
            callback = getattr(self, "database_view_callback", None)
            if callback:
                callback()
            return "Abrindo meu banco de dados no MySQL."

        if is_brain_view_request(text):
            if self.brain_view_callback:
                self.brain_view_callback()
            return "Abrindo a visualização do meu segundo cérebro."

        time_date_response = self._answer_time_or_date_question(normalized)
        if time_date_response is not None:
            return time_date_response

        daily_response = self.daily_query.answer(text)
        if daily_response is not None:
            return daily_response

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

        security_response = self._process_security_request(normalized)
        if security_response is not None:
            return security_response

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

        command_response = self._process_command(
            normalized
        )

        if command_response is not None:

            return command_response

        # ======================================================
        # TOOLS / APIs EXTERNAS
        # ======================================================

        tool_response = self._process_tool_request(
            normalized
        )

        if tool_response is not None:

            return tool_response

        if self.orchestrator.is_complex_task(text):
            return self.orchestrator.handle_task(text)

        # ======================================================
        # MEMÓRIA
        # ======================================================

        self.memory.add_daily_message(
            "user",
            text
        )

        self.conversation.add_user_message(
            text
        )

        self._update_context_state(text)

        messages = (
            self.conversation.get_messages()
        )

        memory_context = (
            self.memory.build_memory_context(
                text
            )
        )
        relevant_memories = self.memory.retrieve_relevant(text, limit=4)
        context_summary = self.conversation.context.snapshot()

        if memory_context or relevant_memories:
            messages = self._inject_memory(
                messages,
                memory_context,
                context_summary,
                relevant_memories,
            )

        if is_serious_context(text):
            messages.insert(0, {
                "role": "system",
                "content": "O contexto parece sério ou urgente. Reduza humor e sarcasmo, seja clara, cuidadosa e objetiva.",
            })

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

    def process_stream(self, text, image=None):

        text = text.strip()

        if not text:

            yield "Você não disse nada."

            return

        normalized = self._normalize_text(text)
        normalized = self._remove_vega_prefix(normalized)

        if not normalized:
            yield "Estou ouvindo."

            return

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
            yield "Estou ouvindo."
            return

        if self._is_short_nonsense(normalized):
            yield "Estou ouvindo."
            return

        security_response = self._process_security_request(normalized)
        if security_response is not None:
            yield security_response
            return
        security_response = self._process_pending_security_response(normalized)
        if security_response is not None:
            yield security_response
            return

        if self._is_holographic_environment_request(normalized):
            if self.environment_callback:
                self.environment_callback()
            yield "Ambiente holográfico inicializado."
            return

        holo_response = self._process_holographic_interaction_command(normalized)
        if holo_response is not None:
            yield holo_response
            return

        if self._is_visual_analysis_request(normalized):
            yield from self._handle_visual_request_stream(text)
            return

        if self._is_database_view_request(normalized):
            callback = getattr(self, "database_view_callback", None)
            if callback:
                callback()
            yield "Abrindo meu banco de dados no MySQL."
            return

        if is_brain_view_request(text):
            if self.brain_view_callback:
                self.brain_view_callback()
            yield "Abrindo a visualização do meu segundo cérebro."
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

        security_response = self._process_security_request(normalized)
        if security_response is not None:
            yield security_response
            return

        if self._is_recycle_bin_request(normalized):
            if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
                yield self._file_changes_disabled_response()
                return
            yield "Vou preparar o esvaziamento da Lixeira."
            yield self._process_recycle_bin_request()
            return

        if self._is_file_delete_request(normalized):
            if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
                yield self._file_changes_disabled_response()
                return
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

            yield mode_response

            return

        # ======================================================
        # COMANDOS
        # ======================================================

        command_response = self._process_command(
            normalized
        )

        if command_response is not None:

            yield command_response
            return

        # ======================================================
        # TOOLS / APIs EXTERNAS
        # ======================================================

        tool_response = self._process_tool_request(
            normalized
        )

        if tool_response is not None:

            yield tool_response

            return

        # ======================================================
        # MEMÓRIA / IA
        # ======================================================

        self.memory.add_daily_message(
            "user",
            text
        )

        self.conversation.add_user_message(
            text
        )

        self._update_context_state(text)

        messages = (
            self.conversation.get_messages()
        )

        memory_context = (
            self.memory.build_memory_context(
                text
            )
        )
        relevant_memories = self.memory.retrieve_relevant(text, limit=4)
        context_summary = self.conversation.context.snapshot()

        if memory_context or relevant_memories:
            messages = self._inject_memory(
                messages,
                memory_context,
                context_summary,
                relevant_memories,
            )

        if is_serious_context(text):
            messages.insert(0, {
                "role": "system",
                "content": "O contexto parece sério ou urgente. Reduza humor e sarcasmo, seja clara, cuidadosa e objetiva.",
            })

        full_response = ""

        for chunk in self.ai.chat_stream(
            messages,
            include_screen=False,
            include_image=image,
        ):

            if chunk is None:
                continue

            chunk_text = self.ai._coerce_text(chunk) if hasattr(self.ai, "_coerce_text") else str(chunk)
            if not chunk_text:
                continue

            full_response += chunk_text

            yield chunk_text

        full_response = full_response.strip()

        if not full_response:
            return

        self.conversation.add_assistant_message(
            full_response
        )

        self.memory.add_daily_message(
            "assistant",
            full_response
        )

    # ==========================================================
    # HORA / DATA
    # ==========================================================

    @staticmethod
    def _portuguese_number(value):
        return portuguese_number(value)

    @staticmethod
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
            result = tool.implementation(**params)
            if tool.name == "report":
                self.memory.add_report_event(result)
            return self._format_tool_result(tool, result)
        except Exception as error:
            print(f"[VegaTools] Erro na ferramenta {tool.name}: {error}")
            return f"Não consegui usar a ferramenta {tool.name} agora: {error}"

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

        if tool.name in {"dictionary", "movie", "summarize_document", "read_file", "read_spreadsheet", "vscode"}:
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

    def _format_tool_result(self, tool, result):
        if not isinstance(result, dict):
            return str(result)

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
                if not confirmed:
                    self._pending_dangerous_action = normalized
                    return "Confirme a ação perigosa e informe o PIN para continuar."
                if not pin_match:
                    self._pending_dangerous_action = normalized
                    return "Essa ação exige confirmação e PIN antes da execução."
                authorized = self._authorize_pending_action(normalized, pin_match.group(1))
                if authorized is not None:
                    return authorized
            response = self.commands_parser.parse(normalized)
            self._remember_opened_app_from_command(normalized, response)
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
        if not self._has_security_confirmation(text):
            return "Ação perigosa pendente. Diga que confirma e informe o PIN."
        pin_match = self._extract_security_pin(text)
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
