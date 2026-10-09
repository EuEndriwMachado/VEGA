import re
import unicodedata

from core.actions.intent_catalog import IntentCatalog, IntentContext


class _DryRunCommands:
    """Substituto seguro do VegaCommands para interpretar sem executar.

    Ações (desligar, apagar, transmitir...) devolvem uma resposta neutra
    em vez de executar. Consultas (has_*, is_*, get_*) passam para o objeto
    real: o parser precisa delas para decidir o roteamento, e consultas não
    têm efeito colateral.
    """

    _NEUTRAL = "[dry-run] comando reconhecido (nao executado)."

    # Prefixos de métodos de CONSULTA: seguem para o objeto real.
    _QUERY_PREFIXES = ("has_", "is_", "get_", "list_", "_extract")

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        real_value = getattr(self._real, name)
        if not callable(real_value):
            return real_value
        if name.startswith(self._QUERY_PREFIXES):
            return real_value

        def neutral(*args, **kwargs):
            return _DryRunCommands._NEUTRAL
        return neutral


class VegaCommandsParser:

    # URL preservada do texto original para pedidos de mídia na TV.
    # A normalização destrói "https://...", então ela é capturada antes.
    _pending_media_url = ""

    _DATABASE_VIEW_PATTERN = re.compile(
        r"^(?:por\s+favor\s+|por\s+gentileza\s+)?"
        r"(?:vega\s+)?"
        r"(?:(?:deixa\s+(?:eu\s+)?|quero\s+|pode\s+|poderia\s+|consegue\s+|vamos\s+|vai\s+)?"
        r"(?:abre|abra|abrir|ver|olhar|olha|olhe|mostra|mostre|mostrar|exibe|exiba|exibir|visualiza|visualize|visualizar|acessa|acesse|acessar|consulta|consulte|consultar)"
        r"(?:\s+ai|\s+aí)?)\s+"
        r"(?:(?:o|a|os|as|um|uma)\s+)?"
        r"(?:seu|sua|da\s+vega|vega|meu)?\s*"
        r"(?:banco\s+de\s+dados|base\s+de\s+dados|banco|db)\b"
        r"(?:\s+(?:da\s+vega|vega|seu|meu|local|mysql))?"
        r"(?:\s+(?:por\s+favor|pfv|agora|pra\s+mim|para\s+mim))?$",
        re.IGNORECASE,
    )

    _DECLARATIVE_INTENTS = (
        (_DATABASE_VIEW_PATTERN, lambda cmds: cmds.open_database()),
    )

    def __init__(self, commands):
        self.commands = commands
        self._intent_catalog = self._build_intent_catalog()

    def parse(self, text):

        """Ponto de entrada seguro: nunca levanta, devolve None sem match."""

        try:
            return self._parse_inner(text)
        except Exception as error:
            print(f"[VegaCommandsParser] Falha ao interpretar comando: {error}")
            return None

    def interpret(self, text):
        """Interpreta uma fala SEM executar nada.

        Devolve (matched, response). É o caminho para testes/diagnósticos
        verificarem o roteamento com segurança total — antes, testar
        "desliga o pc" com o parser real APAGAVA o computador do usuário.
        """
        previous = self.commands
        self.commands = _DryRunCommands(previous)
        try:
            response = self.parse(text)
            return response is not None, response
        finally:
            self.commands = previous

    # ==========================================================
    # CATÁLOGO DE INTENÇÕES
    # ==========================================================
    #
    # Cada capacidade é um DADO (nome, resolvedor, executor, prioridade), não
    # mais um `if` escrito na mão dentro de _parse_inner. Adicionar uma frase
    # nova passa a ser adicionar uma regra, sem tocar no fluxo de reconhecimento.
    #
    # Os resolvedores delegam aos reconhecedores `_is_*` já existentes (e já
    # cobertos por testes): a migração muda ONDE o critério é registrado, não o
    # critério em si. Isso mantém a cobertura e o comportamento idênticos.
    def _build_intent_catalog(self):
        from core.actions.intents_apps import build_app_rules
        from core.actions.intents_media import build_media_rules
        from core.actions.intents_system import build_system_rules

        catalog = IntentCatalog()
        catalog.extend(build_system_rules(self))
        catalog.extend(build_media_rules(
            self,
            extract_close_app_name=self._extract_close_app_name,
            normalize_app_name=self._normalize_app_name,
        ))
        catalog.extend(build_app_rules(self))
        return catalog

    def _process_compound_browser_command(self, text):
        """Reservado para comandos compostos de navegador (ex.: abrir + buscar)."""
        return None

    def _parse_inner(self, text):

        # Entrada malformada nunca deve levantar: devolve um "no-match" seguro.
        if not isinstance(text, str):
            return None
        if not text or not text.strip():
            return None
        # A URL de um pedido de mídia é preservada do texto ORIGINAL: a
        # normalização remove ":" e "/", o que destruiria "https://...".
        VegaCommandsParser._pending_media_url = self._extract_media_url(text)
        try:
            normalized = self.normalize(text)
        except Exception:
            return None
        if not normalized:
            return None
        # ======================================================
        # DIÁLOGO DE TRABALHO / LISTA PENDENTE
        # ======================================================

        if hasattr(self.commands, "has_pending_homework") and self.commands.has_pending_homework():
            dialog_response = self.commands.handle_homework_dialog(
                text
            )
            if dialog_response is not None:
                return dialog_response

        # ======================================================
        # COMANDOS COMPOSTOS
        # ======================================================

        compound_response = self._process_compound_browser_command(
            normalized
        )

        if compound_response is not None:
            return compound_response

        # ======================================================
        # INTENÇÕES DECLARATIVAS (SEM IF/ELSE ANINHADOS)
        # ======================================================

        for pattern, handler in self._DECLARATIVE_INTENTS:
            if pattern.search(normalized):
                return handler(self.commands)

        # ======================================================
        # SISTEMA / TELA / BLUETOOTH / NOTIFICAÇÕES / BRILHO / VOLUME
        # Reconhecimento via catálogo de intenções (ordem por prioridade).
        # A ordem preserva o que os comentários originais exigiam: cancelamento
        # antes de desligamento; "máximo/mínimo" antes de "aumenta/diminui".
        # ======================================================

        catalog_context = IntentContext(
            text=normalized,
            original=text,
            commands=self.commands,
            parser=self,
        )
        catalog_rule, catalog_response = self._intent_catalog.resolve(catalog_context)
        if catalog_rule is not None:
            return catalog_response

        # ======================================================
        # CONECTIVIDADE — CELULAR / TV
        # (antes do volume/música/pesquisa: "aumenta o volume da tv"
        # deve ir para a TV, não para o volume do PC; "pausa a música
        # no celular" deve ir para o celular, não para a mídia do PC)
        # ======================================================

        connectivity = self._resolve_connectivity_request(normalized)
        if connectivity is not None:
            action, kwargs = connectivity
            if action == "__discover__":
                return self.commands.discover_devices()
            if action == "__pc_screenshot__":
                return self.commands.screenshot_pc()
            if action == "__phone_screenshot__":
                return self.commands.connectivity_action(
                    "screenshot", text=normalized, kind="phone",
                )
            # O kwargs pode trazer a própria chave "text" (ex.: send_message
            # com o texto da mensagem). O texto da FALA sempre vence para o
            # roteamento; o texto da mensagem vai dentro do kwargs e é usado
            # pelo handler. Sem a ordem certa, Python levanta "got multiple
            # values for 'text'" ou o roteador perdia o dispositivo.
            dispatch_kwargs = {**kwargs, "text": normalized}
            return self.commands.connectivity_action(action, **dispatch_kwargs)

        # ======================================================
        # PLAYLISTS PESSOAIS ("toca minha playlist X")
        # Vem ANTES da música genérica: "minha playlist" não deve
        # ser tratada como busca de música qualquer.
        # ======================================================

        personal_playlist = self._extract_personal_playlist(normalized)

        if personal_playlist is not None:
            name, platform = personal_playlist
            return self.commands.play_personal_playlist(name, platform=platform)

        save_playlist = self._extract_save_playlist(normalized)

        if save_playlist is not None:
            name, platform = save_playlist
            return self.commands.save_personal_playlist(name, platform=platform)

        if self._extract_list_playlists(normalized):
            return self.commands.list_personal_playlists()

        # ======================================================
        # MÚDIA — TOCAR MÚSICA/PLAYLIST ESPECÍFICA
        # (antes do play/pause genérico, senão "toca musica X"
        # seria comido pelo "toca musica")
        # ======================================================

        music_request = self._extract_music_request(normalized)

        if music_request:
            query, platform, playlist = music_request
            return self.commands.play_music(
                query,
                platform=platform,
                playlist=playlist,
            )

        # ======================================================
        # MÍDIA
        # ======================================================

        # Pedido genérico de TOCAR música ("toca música", "toca uma música")
        # não é play/pause: sem faixa tocando, o teclado de mídia não faz
        # nada. Encaminha para play_music, que pergunta qual música tocar
        # quando nenhuma foi dita.
        if normalized in (
            "toca musica", "tocar musica", "toque musica",
            "toca uma musica", "tocar uma musica", "toque uma musica",
            "toca uma música", "tocar uma música", "toque uma música",
        ):
            return self.commands.play_music("", platform="youtube")

        if self._is_media_play_pause_request(normalized):
            return self.commands.media_play_pause()

        if self._is_media_next_request(normalized):
            return self.commands.media_next()

        if self._is_media_previous_request(normalized):
            return self.commands.media_previous()

        if self._is_media_stop_request(normalized):
            return self.commands.media_stop()

        # ======================================================
        # MÍDIA / JANELAS / TECLADO / FUNK / BUSCA / SITES / PASTAS / APPS
        # Migrados para o catálogo de intenções (ver `_build_intent_catalog`).
        # A resolução acontece logo abaixo, em prioridade, dentro de
        # `self._intent_catalog.resolve(...)`. Os reconhecedores `_is_*` e os
        # extratores continuam existindo e testados: mudou ONDE são
        # registrados, não o critério.
        # ======================================================

        return None

    # ==========================================================
    # FECHAR APP POR NOME
    # ==========================================================

    @staticmethod
    def _extract_close_app_name(text):

        if not text:
            return None

        value = VegaCommandsParser.normalize(text)

        # Keep the prefix anchored so conversational sentences are not
        # mistaken for an operating-system command. The alternatives mirror
        # the opening command vocabulary and cover common speech-to-text
        # conjugations, including "feche" (which text_utils produces for
        # the frequent transcription "fechi").
        action = r"(?:fecha|fechar|feche|encerra|encerrar|encerre|termina|terminar|termine)"
        patterns = (
            rf"^(?:por favor\s+)?(?:pode|poderia|consegue)\s+{action}\s+",
            rf"^(?:por favor\s+)?{action}\s+",
        )

        app = None

        for pattern in patterns:
            match = re.match(pattern, value, flags=re.IGNORECASE)
            if match:
                app = value[match.end():].strip()
                break

        if not app:
            return None

        app = re.sub(r"^(o|a|os|as)\s+", "", app).strip()
        app = re.sub(r"^(app|aplicativo|programa)\s+", "", app).strip()
        app = re.sub(r"^(esse|essa|isso)\s+", "", app).strip()
        app = re.sub(r"^(ai|aí)\s+", "", app).strip()
        app = re.sub(r"^(tambem|também|agora|agr)\s+", "", app).strip()

        app = re.split(r"\s+(?:e|depois|agora)\s+", app, maxsplit=1)[0]
        app = re.sub(r"\s+", " ", app).strip(" .,!?;:")

        # These are requests to close the active window, not application
        # names. Returning None lets _is_close_window_request handle them.
        if app in {
            "janela", "isso", "essa", "esse", "programa", "app",
            "aplicativo",
        }:
            return None

        if not app:
            return None

        return app

    # ==========================================================
    # FRASES COMPOSTAS
    # ==========================================================

    def _process_compound_browser_command(self, text):

        if not text:
            return None

        search_patterns = (
            "pesquise ",
            "pesquisa ",
            "pesquisar ",
            "procure ",
            "procura ",
            "procurar ",
        )

        search_position = None
        search_prefix = None

        for pattern in search_patterns:

            position = text.find(pattern)

            if position == -1:
                continue

            # Evita encontrar "pesquisa" no meio de uma palavra.
            if position > 0 and text[position - 1].isalnum():
                continue

            if (
                search_position is None
                or position < search_position
            ):
                search_position = position
                search_prefix = pattern

        if search_position is None:
            return None

        query = text[
            search_position + len(search_prefix):
        ].strip()

        if not query:
            return None

        query = re.sub(
            r"^(por|sobre)\s+",
            "",
            query
        ).strip()

        if not query:
            return None

        before_search = text[
            :search_position
        ].strip()

        before_search = re.sub(
            r"\s+(e|,)\s*$",
            "",
            before_search
        ).strip()

        # ------------------------------------------------------
        # Detecta se foi solicitado um navegador específico.
        # ------------------------------------------------------

        browser = self._extract_browser_to_open(
            before_search
        )

        if browser:

            browser_normalized = self._normalize_app_name(
                browser
            )

            # Só trata como comando composto se realmente
            # houver uma abertura explícita.
            if self._looks_like_browser(
                browser_normalized
            ):

                open_response = self._open_browser_target(
                    browser_normalized
                )

                search_response = self._search_with_detected_platform(
                    query,
                    browser_normalized
                )

                return self._combine_responses(
                    open_response,
                    search_response
                )

        # ------------------------------------------------------
        # Caso:
        #
        # "chrome pesquisa carros"
        # "google pesquisa carros"
        # ------------------------------------------------------

        browser_name = self._extract_browser_name(
            before_search
        )

        if browser_name:

            browser_name = self._normalize_app_name(
                browser_name
            )

            open_response = self._open_browser_target(
                browser_name
            )

            search_response = self._search_with_detected_platform(
                query,
                browser_name
            )

            return self._combine_responses(
                open_response,
                search_response
            )

        return None

    # ==========================================================
    # PESQUISA COM PLATAFORMA
    # ==========================================================

    def _search_with_detected_platform(
        self,
        query,
        browser
    ):

        browser = self._normalize_app_name(
            browser
        )

        if browser == "youtube":
            return self.commands.search_youtube(
                query
            )

        return self.commands.search_google(
            query
        )

    # ==========================================================
    # EXTRAIR NAVEGADOR
    # ==========================================================

    @staticmethod
    def _extract_browser_to_open(text):

        if not text:
            return None

        value = text.strip()

        open_patterns = (
            r"^pode abrir\s+",
            r"^poderia abrir\s+",
            r"^consegue abrir\s+",
            r"^quero abrir\s+",
            r"^quero iniciar\s+",
            r"^quero que voce abra\s+",
            r"^quero que você abra\s+",
            r"^por favor abra\s+",
            r"^por favor abre\s+",
            r"^abre\s+",
            r"^abra\s+",
            r"^abrir\s+",
            r"^inicia\s+",
            r"^inicie\s+",
            r"^iniciar\s+",
            r"^executa\s+",
            r"^execute\s+",
            r"^executar\s+",
            r"^roda\s+",
            r"^rodar\s+",
        )

        for pattern in open_patterns:

            match = re.match(
                pattern,
                value,
                flags=re.IGNORECASE
            )

            if not match:
                continue

            target = value[
                match.end():
            ].strip()

            target = re.sub(
                r"^(o|a|os|as)\s+",
                "",
                target
            ).strip()

            target = re.sub(
                r"\s+(e|,)\s*$",
                "",
                target
            ).strip()

            if target:
                return target

        return None

    @staticmethod
    def _extract_browser_name(text):

        if not text:
            return None

        value = text.lower().strip()

        value = re.sub(
            r"^(o|a|os|as)\s+",
            "",
            value
        ).strip()

        browsers = (
            "google chrome",
            "chrome",
            "youtube",
            "navegador",
            "google",
        )

        # Maior primeiro para evitar colisões.
        for browser in browsers:

            if value == browser:
                return browser

            if value.endswith(
                " " + browser
            ):
                return browser

        return None

    @staticmethod
    def _looks_like_browser(name):

        return name in {
            "chrome",
            "google chrome",
            "navegador",
            "google",
            "youtube",
        }

    # ==========================================================
    # ABRIR NAVEGADOR
    # ==========================================================

    def _open_browser_target(self, target):

        target = self._normalize_app_name(
            target
        )

        if target in {
            "chrome",
            "google chrome",
            "navegador",
        }:
            return self.commands.open_chrome()

        if target == "google":
            return self.commands.open_google()

        if target == "youtube":
            return self.commands.open_youtube()

        return self.commands.open_app(
            target
        )

    # ==========================================================
    # COMBINAR RESPOSTAS
    # ==========================================================

    @staticmethod
    def _combine_responses(
        first,
        second
    ):

        if first and second:
            return (
                f"{first}\n"
                f"{second}"
            )

        if first:
            return first

        return second

    # ==========================================================
    # NORMALIZAÇÃO
    # ==========================================================

    @staticmethod
    def normalize(text):

        if not text:
            return ""

        text = str(text).lower().strip()

        # Repair older strings saved with a UTF-8/Latin-1 mismatch before
        # comparing them. Normal voice transcriptions pass through unchanged.
        if "\u00c3" in text or "\u00c2" in text:
            try:
                text = text.encode("latin-1").decode("utf-8")
            except UnicodeError:
                pass

        text = unicodedata.normalize("NFD", text)
        text = "".join(
            char
            for char in text
            if unicodedata.category(char) != "Mn"
        )

        corrections = {

            # Spotify
            "spotfy": "spotify",
            "spotif": "spotify",
            "spofity": "spotify",
            "spotifyy": "spotify",
            "spotfy premium": "spotify",

            # Chrome
            "chorme": "chrome",
            "crome": "chrome",
            "cromee": "chrome",
            "crhome": "chrome",

            # Discord
            "discrod": "discord",
            "discor": "discord",
            "discorde": "discord",

            # Steam
            "stema": "steam",
            "steem": "steam",
            "stima": "steam",

            # VS Code
            "vscod": "vscode",
            "vs code": "vscode",
            "visual studio code": "vscode",
            "visual code": "vscode",
            "vs cand": "vscode",

            # WhatsApp
            "whats app": "whatsapp",
            "what's app": "whatsapp",
            "wpp": "whatsapp",
            "wap": "whatsapp",

            # YouTube
            "youtub": "youtube",
            "you tube": "youtube",
            "utube": "youtube",

            # Outros
            "áudio": "audio",
            "brilho da tela": "brilho",
            "luminosidade": "brilho",
            "telas": "tela",

            "computadorzinho": "pc",
            "computador": "pc",
            "notebook": "pc",
            "note": "pc",

            # Formas comuns
            "área de trabalho": "área de trabalho",
            "area de trabalho": "área de trabalho",
        }

        for wrong, correct in corrections.items():

            text = re.sub(
                rf"\b{re.escape(wrong)}\b",
                correct,
                text
            )

        text = re.sub(
            r"[.,!?;:]+|[\-–—]+",
            " ",
            text
        )

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        return text.strip()

    # ==========================================================
    # TEXTO
    # ==========================================================

    @staticmethod
    def _contains_any(text, words):

        if not text:
            return False

        for word in words:

            word = VegaCommandsParser.normalize(word)

            if not word:
                continue

            # Frases com mais de uma palavra.
            if " " in word:

                if word in text:
                    return True

            else:

                if re.search(
                    rf"\b{re.escape(word)}\b",
                    text
                ):
                    return True

        return False

    # ==========================================================
    # ABRIR
    # ==========================================================

    @staticmethod
    def _contains_open_action(text):

        actions = (

            "abra",
            "abre",
            "abrir",
            "abrindo",

            "inicia",
            "inicie",
            "iniciar",

            "executa",
            "execute",
            "executar",

            "roda",
            "rodar",

            "pode abrir",
            "poderia abrir",
            "consegue abrir",

            "quero abrir",
            "quero iniciar",

            "abre ai",
            "abre aí",
        )

        return VegaCommandsParser._contains_any(
            text,
            actions
        )

    def _is_open_request(self, text, apps):

        if not self._contains_open_action(text):
            return False

        return self._contains_any(
            text,
            apps
        )

    # ==========================================================
    # VOLUME
    # ==========================================================

    @staticmethod
    def _is_volume_up_request(text):

        if "volume" not in text and "som" not in text:
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "aumenta",
                "aumentar",
                "aumente",
                "sobe",
                "subir",
                "mais alto",
                "mais forte",
                "sobe o som",
                "aumenta o som",
                "aumenta volume",
                "aumentar volume",
            )
        )

    @staticmethod
    def _is_volume_down_request(text):

        if "volume" not in text and "som" not in text:
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "diminui",
                "diminuir",
                "diminua",
                "abaixa",
                "abaixar",
                "desce",
                "baixar",
                "mais baixo",
                "mais fraco",
                "abaixa o som",
                "diminui o som",
                "abaixa volume",
                "diminuir volume",
            )
        )

    @staticmethod
    def _is_volume_max_request(text):

        if "volume" not in text and "som" not in text:
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "máximo",
                "maximo",
                "100",
                "100%",
                "no talo",
                "no máximo",
                "no maximo",
                "lá em cima",
                "la em cima",
                "volume total",
            )
        )

    @staticmethod
    def _is_volume_min_request(text):

        if "volume" not in text and "som" not in text:
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "mínimo",
                "minimo",
                "zero",
                "0%",
                "no mínimo",
                "no minimo",
                "bem baixo",
                "volume mínimo",
                "volume minimo",
            )
        )

    @staticmethod
    def _is_mute_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "muta",
                "mutar",
                "mute",
                "silencia",
                "silenciar",
                "fica sem som",
                "tira o som",
                "desliga o som",
            )
        )

    @staticmethod
    def _is_unmute_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "desmuta",
                "desmutar",
                "unmute",
                "ativa o som",
                "volta o som",
                "liga o som",
            )
        )

    # ==========================================================
    # CONECTIVIDADE — CELULAR / TV
    # ==========================================================

    # "whatsapp"/"zap" contam como celular: ligar/mandar mensagem por eles é
    # operar o aparelho, mesmo que a fala não diga "celular".
    _PHONE_WORDS = (
        "celular", "telefone", "smartphone", "android", "meu phone",
        "whatsapp", "whats app", "zap",
    )
    _TV_WORDS = ("tv", "televisao", "televisor", "chromecast")
    # Palavras que apontam para o PC explicitamente.
    _PC_WORDS = ("computador", "pc", "desktop", "monitor", "meu pc", "no pc")

    # Frases que pedem captura de tela (mesma intenção para PC e celular).
    #
    # AQUI só entram pedidos de FOTO/PRINT. Pedidos de VER a tela sem citar
    # dispositivo ("olha minha tela", "me mostra a tela") são tratados em
    # `_resolve_view_request`, porque o alvo depende do dispositivo citado:
    #   - "olha minha tela"              -> print da tela do PC (padrão)
    #   - "olha a tela do celular"       -> espelhar o celular (quer VER, não foto)
    #   - "print da tela do celular"     -> foto da tela do celular
    _SCREENSHOT_PHRASES = (
        "tira um print", "tira print", "tira um print da tela",
        "tira print da tela", "print da tela", "screenshot",
        "captura a tela", "capturar a tela", "tira uma foto da tela",
        "print",  # cobre "print do celular" e "print do pc"
    )

    # Pedidos de OLHAR a tela (sem a palavra print). Com dispositivo explícito
    # de celular vão para o espelhamento; sem dispositivo, para a tela do PC.
    _VIEW_SCREEN_PHRASES = (
        "olha minha tela", "olha a minha tela", "olha pra minha tela",
        "olha para a minha tela", "ve minha tela", "ver minha tela",
        "vê minha tela", "mostra a tela", "mostre a tela",
        "me mostra a tela", "me mostre a tela", "exibe a tela",
        "exibir a tela", "o que tem na minha tela", "o que esta na minha tela",
        "o que aparece na minha tela", "analisa a minha tela",
        "analisa minha tela", "olha a tela", "olha isso na tela",
        "olha isso na minha tela", "descreve a tela", "da uma olhada na tela",
        "quero ver a tela", "deixa eu ver a tela",
    )

    @classmethod
    def _resolve_view_request(cls, text):
        """Traduz um pedido de OLHAR a tela, decidindo o dispositivo.

        Regra pedida pelo usuário (e que evita a confusão anterior, em que
        "olha minha tela" espelhava o celular):

          - citou celular/telefone -> espelhar o celular (o usuário quer VER);
          - qualquer outra coisa   -> print da tela do PC (o padrão).

        Retorna None quando a fala não é um pedido de olhar a tela.
        """
        if not any(phrase in text for phrase in cls._VIEW_SCREEN_PHRASES):
            return None
        device = cls._mentions_device(text)
        if device == "phone":
            return ("mirror", {})
        return ("__pc_screenshot__", {})

    # Frases que pedem espelhamento (celular) ou mostrar a tela.
    # "parar" é verificado ANTES, para não casar com "espelhar".
    _STOP_MIRROR_PHRASES = (
        "para de espelhar", "para o espelhamento", "parar o espelhamento",
        "fecha a tela do celular", "fecha o espelhamento",
        "encerra o espelhamento", "encerrar o espelhamento",
    )
    _MIRROR_PHRASES = (
        "espelha", "espelhar", "espelhamento", "mirror",
        # "deixa eu ver a tela" e "quero ver a tela" ficam FORA: sem citar o
        # celular, o usuário quer a tela do PC. Só o celular explícito espelha.
        "deixa eu ver a tela do celular",
        "quero ver a tela do celular", "ver a tela do celular",
        "mostra a tela do celular", "mostre a tela do celular",
        "me mostra a tela do celular", "me mostre a tela do celular",
        "me mostra o celular", "mostra o celular",
    )

    @classmethod
    def _mentions_device(cls, text):
        """Retorna ('phone'|'tv'|'pc'|'') conforme o dispositivo citado.

        O PC só é reconhecido quando citado EXPLICITAMENTE. Isso garante
        que "abre o instagram" (sem menção) siga para o PC, e
        "abre o instagram no celular" vá para o celular — sem ambiguidade.
        """
        if any(word in text for word in cls._PHONE_WORDS):
            return "phone"
        if any(word in text for word in cls._TV_WORDS):
            return "tv"
        if any(word in text for word in cls._PC_WORDS):
            return "pc"
        return ""

    @classmethod
    def _resolve_screenshot_request(cls, text):
        """Decide se um pedido de print é do PC ou do celular.

        Regra:
          - menciona celular/telefone  -> print do celular;
          - menciona computador/pc     -> print do PC;
          - sem menção                 -> print do PC (comportamento padrão).
        """
        if not any(phrase in text for phrase in cls._SCREENSHOT_PHRASES):
            return None

        device = cls._mentions_device(text)
        if device == "phone":
            return ("__phone_screenshot__", {})
        # PC ou sem menção explícita: print do computador.
        return ("__pc_screenshot__", {})

    @classmethod
    def _resolve_connectivity_request(cls, text):
        """Traduz uma fala em (action, kwargs) de conectividade, ou None.

        Reconhece ações como: bateria, listar, abrir app, print, enviar
        arquivo, espelhar, controle de mídia (celular); listar, tocar URL,
        pausar, retomar, parar, volume, desligar (TV).
        """

        if not text:
            return None

        # Print: PC ou celular? Resolvido antes de tudo, para não confundir.
        screenshot = cls._resolve_screenshot_request(text)
        if screenshot is not None:
            return screenshot

        # Pedido de OLHAR a tela (sem print): com celular explícito é
        # espelhamento; sem menção é a tela do PC. O padrão é o PC — antes,
        # "olha minha tela" espelhava o celular sem o usuário pedir.
        view = cls._resolve_view_request(text)
        if view is not None:
            return view

        device = cls._mentions_device(text)

        # Ações do celular que podem ser pedidas SEM dizer "celular":
        # "liga para a rita pelo meu celular", "responde o miguel".
        if device == "phone" or cls._looks_like_phone_action(text):
            reply = cls._extract_reply_request(text)
            if reply:
                return ("send_whatsapp" if reply.get("text") else "read_conversation", reply)
            conversation = cls._extract_conversation_read_request(text)
            if conversation:
                return ("read_conversation", conversation)

        # Identificação do aparelho: "que celular é esse?", "qual é o meu celular?"
        if device == "phone" and any(phrase in text for phrase in (
            "que celular", "qual celular", "qual e o meu celular", "qual é o meu celular",
            "que aparelho", "qual aparelho", "de quem e esse celular",
            "de quem é esse celular", "meu celular e qual", "identifica o celular",
            "qual o modelo", "que modelo",
        )):
            return ("identify", {})

        # Pesquisa no YouTube SEM citar dispositivo: fica no PC (página de
        # resultados no navegador), como sempre foi. Só vai para a TV quando
        # a fala cita a TV ("pesquisa X no youtube da tv") — regra do usuário:
        # sem "da tv", não é comando da TV.

        # Listagem genérica de dispositivos: não exige escolher celular ou TV.
        if any(word in text for word in ("quais dispositivos", "meus dispositivos",
                                         "dispositivos conectados", "lista os dispositivos")):
            return ("__discover__", {})

        # Ações que exigem um dispositivo explícito.
        if device:
            if "bateria" in text or "carregando" in text or "carga" in text:
                return ("battery", {})

            if device == "phone":
                # "parar" precisa vir antes: "para de espelhar" contém "espelhar".
                if any(phrase in text for phrase in cls._STOP_MIRROR_PHRASES):
                    return ("stop_mirror", {})

                # Respostas e leitura de conversa antes de "mensagem", para
                # "responde o miguel" não cair no envio genérico.
                reply = cls._extract_reply_request(text)
                if reply:
                    return ("send_whatsapp" if reply.get("text") else "read_conversation", reply)
                conversation = cls._extract_conversation_read_request(text)
                if conversation:
                    return ("read_conversation", conversation)
                if any(phrase in text for phrase in cls._MIRROR_PHRASES):
                    return ("mirror", {})
                if any(phrase in text for phrase in ("sem fio", "sem cabo", "wifi", "wi-fi",
                                                     "pelo wi-fi", "pelo wifi")):
                    if any(word in text for word in ("reconecta", "reconectar", "conecta de novo")):
                        return ("reconnect", {})
                    if any(word in text for word in ("ativa", "ativar", "liga", "ligar", "habilita")):
                        return ("enable_wireless", {})
                    return ("reconnect", {})
                if any(word in text for word in ("reconecta", "reconectar")):
                    return ("reconnect", {})

                # ---- Notificações ----
                if any(phrase in text for phrase in (
                    "notificacoes", "notificações", "notificacao", "notificação",
                    "chegou mensagem", "chegou alguma mensagem", "tem mensagem",
                    "alguma notificacao", "o que chegou",
                )):
                    return ("notifications", {})

                # ---- Alarme ----
                alarm = cls._extract_alarm_request(text)
                if alarm:
                    return ("set_alarm", alarm)
                if any(phrase in text for phrase in (
                    "meus alarmes", "quais alarmes", "listar alarmes", "meus despertadores",
                )):
                    return ("list_alarms", {})

                # ---- Localizar o celular ----
                if any(phrase in text for phrase in (
                    "onde esta meu celular", "onde está meu celular",
                    "onde ta meu celular", "acha meu celular", "achar meu celular",
                    "localiza o celular", "localizar o celular", "faz o celular tocar",
                    "perdi meu celular",
                )):
                    return ("locate", {})

                # ---- Foto com o celular ----
                if any(phrase in text for phrase in (
                    "tira uma foto com o celular", "tira foto com o celular",
                    "foto pelo celular", "usa o celular como camera",
                    "usa o celular como câmera", "tira uma foto pelo celular",
                )):
                    return ("take_photo", {})

                # ---- Bluetooth do celular (antes de "ligar", que também
                #      pode significar telefonar) ----
                if "bluetooth" in text or "blutooth" in text:
                    if any(word in text for word in ("desliga", "desligar", "desativa", "desativar", "off")):
                        return ("bluetooth", {"enabled": False})
                    return ("bluetooth", {"enabled": True})

                # ---- Ligação ----
                call = cls._extract_call_request(text)
                if call:
                    return ("call", call)

                # ---- Mensagem ----
                message = cls._extract_message_request(text)
                if message:
                    return ("send_message", message)

                if any(word in text for word in ("pausa", "pause")):
                    return ("media_pause", {})
                if any(word in text for word in ("próxima", "proxima", "pula", "avança", "avanca")):
                    return ("media_next", {})
                if any(word in text for word in ("anterior", "volta uma", "voltar uma")):
                    return ("media_previous", {})
                if any(word in text for word in ("para a mídia", "para mídia", "pare a mídia", "stop na mídia")):
                    return ("media_stop", {})
                # ---- Pesquisa na web NO CELULAR ----
                # "pesquise sobre eleições no celular" precisa abrir a busca
                # no aparelho, não no Google do PC. Roda antes de tudo o que
                # possa capturar a fala como pesquisa genérica do computador.
                search_query = cls._extract_phone_search_query(text)
                if search_query:
                    return ("search_web", {"query": search_query})

                # ---- Música no celular ----
                # "toca menos é mais no spotify no celular" deve TOCAR, não só
                # abrir o app.
                music_query, music_platform = cls._extract_phone_music_request(text)
                if music_query:
                    return ("play_music", {"query": music_query, "platform": music_platform})

                app = cls._extract_phone_app(text)
                if app:
                    return ("open_app", {"app": app})
                file_path = cls._extract_file_to_send(text)
                if file_path:
                    return ("send_file", {"path": file_path})
                if any(word in text for word in ("conecta", "conectar", "pareia", "parear",
                                                 "liga no celular", "status do celular")):
                    return ("list", {})

            if device == "tv":
                # ---- Ligar a TV (antes de "liga" virar outra coisa).
                # Regex com fronteira: "desliga a tv" contém "liga a tv".
                if re.search(r"\b(?:liga|acende|acorda)\s+(?:a|o)\s+"
                             r"(?:tv|televisao|televisão|televisor)\b", text):
                    return ("wake_on", {})
                # ---- Volume (primeiro: "coloca o volume da tv em 40%" NÃO é
                #      um pedido de mídia, apesar do verbo "coloca") ----
                if any(word in text for word in ("muta", "mutar", "mudo", "sem som", "tira o som")):
                    return ("volume", {"level": 0.0})
                level = cls._extract_volume_level(text)
                if level is not None:
                    return ("volume", {"level": level})
                # Aumentar/diminuir sem valor explícito vira um passo (delta).
                if any(word in text for word in ("aumenta", "aumentar", "sobe", "subir",
                                                 "levanta", "mais alto", "mais volume")):
                    return ("volume", {"delta": 0.1})
                if any(word in text for word in ("abaixa", "abaixar", "diminui", "diminuir",
                                                 "baixa", "baixar", "desce", "menos volume",
                                                 "mais baixo")):
                    return ("volume", {"delta": -0.1})
                if "volume" in text or "som" in text:
                    return ("volume", {})
                # ---- Enviar mídia / controle de reprodução ----
                # Enviar um vídeo/link direto para a TV.
                url = getattr(cls, "_pending_media_url", "")
                if url:
                    return ("play_url", {"url": url})
                # ---- Spotify da TV: "toca X no spotify da tv" ----
                if "spotify" in text and any(word in text for word in (
                        "toca", "tocar", "toque", "passa", "passar", "coloca",
                        "coloque", "manda", "manda", "envia", "reproduz", "ouvir",
                        "ouca", "queria ouvir")):
                    query = cls._extract_tv_spotify_query(text)
                    if query:
                        return ("play_spotify", {"query": query})
                # ---- Apps da TV: "abre o netflix na tv" ----
                app = cls._extract_tv_app(text)
                if app:
                    return ("open_app", {"app": app})
                # ---- Pesquisar (sem tocar) no YouTube da TV ----
                if "youtube" in text and any(word in text for word in (
                        "pesquisa", "pesquisar", "busca", "buscar", "procura", "procurar")):
                    query = cls._extract_tv_youtube_query(text)
                    return ("search_youtube", {"query": query})
                # ---- Tocar vídeo no YouTube da TV (app, de verdade) ----
                youtube_query = cls._extract_tv_youtube_query(text)
                if youtube_query:
                    return ("play_youtube", {"query": youtube_query})
                # Pedido de tocar "algo" na TV sem dizer o quê: pede o conteúdo
                # em vez de cair no YouTube Music do PC.
                if any(word in text for word in ("toca", "tocar", "toque", "passa",
                                                 "passar", "joga", "jogar", "reproduz",
                                                 "reproduzir", "video", "vídeo", "filme",
                                                 "musica", "música", "clip", "clipe")):
                    return ("play_youtube", {})
                if any(word in text for word in ("pausa", "pause")):
                    return ("pause", {})
                # ---- Teclas do controle: "aperte enter na tv" ----
                if any(word in text for word in ("aperte", "apertar", "pressione",
                                                 "press", "clica", "clicar")):
                    for key in ("enter", "ok", "cima", "up", "baixo", "down",
                                "esquerda", "left", "direita", "right",
                                "voltar", "back", "sair", "exit",
                                "home", "inicio", "menu", "info"):
                        if key in text:
                            return ("key", {"key": key})
                    return ("key", {})
                if any(word in text for word in ("retoma", "continua", "volta a tocar", "play")):
                    return ("resume", {})
                if "volta" in text or "voltar" in text:
                    return ("key", {"key": "voltar"})
                if any(word in text for word in ("para a", "parar", "stop")):
                    return ("stop", {})
                if any(word in text for word in ("desliga", "desligar", "turn off")):
                    return ("turn_off", {})
                if any(word in text for word in ("conecta", "conectar", "procura",
                                                 "liga a tv", "status da tv")):
                    return ("list", {})

        # Ações que fazem sentido sem citar o dispositivo explicitamente.
        if any(phrase in text for phrase in ("quais dispositivos", "meus dispositivos",
                                             "dispositivos conectados")):
            return ("__discover__", {})

        return None

    @classmethod
    def _looks_like_phone_action(cls, text):
        """Reconhece ações do celular mesmo sem a palavra "celular" na fala.

        Ex.: "liga para a rita pelo meu celular", "responde o miguel",
        "manda mensagem pro joao no zap".
        """
        if not text:
            return False
        if cls._extract_call_request(text):
            return True
        if cls._extract_reply_request(text):
            return True
        if cls._extract_conversation_read_request(text):
            return True
        if re.search(r"\b(?:whatsapp|whats|zap)\b", text) and re.search(
            r"\b(?:manda|mandar|envia|enviar|escreve|escrever|responde|responder|le|ler|leia)\b",
            text,
        ):
            return True
        return False

    @staticmethod
    def _extract_phone_app(text):
        """Extrai o app pedido para abrir no celular."""
        match = re.search(
            r"(?:abre|abrir|abra|executa|executar)\s+(?:o\s+|a\s+)?(?:app\s+)?(.+?)\s+"
            r"(?:no|na|do|da)\s+(?:meu\s+|minha\s+)?(?:celular|telefone|smartphone|android)",
            text,
        )
        if not match:
            return ""
        app = match.group(1).strip(" ,.!")
        # Remove palavras de recheio.
        app = re.sub(r"^(?:o|a|app)\s+", "", app).strip()
        return app

    # Verbos de pesquisa que podem iniciar um pedido na web.
    _SEARCH_VERBS = (
        "pesquise", "pesquisa", "pesquisar", "busque", "busca", "buscar",
        "procure", "procura", "procurar",
    )

    @classmethod
    def _extract_phone_search_query(cls, text):
        """Extrai a busca de "pesquise sobre eleições no celular".

        Só devolve algo quando a fala cita o celular E um verbo de pesquisa:
        assim "abre o instagram no celular" não vira pesquisa, e "pesquisa X"
        sem dispositivo continua indo para o PC (comportamento esperado).
        """
        if not text or not cls._looks_like_phone_action(text):
            # Pesquisa não é "phone action" pelo nome, então checa o verbo aqui.
            pass

        if not any(re.search(rf"\b{verb}\b", text) for verb in cls._SEARCH_VERBS):
            return ""

        # Precisa citar o celular explicitamente.
        if not any(word in text for word in cls._PHONE_WORDS):
            return ""

        # Remove o verbo inicial e os conectivos.
        cleaned = text
        for verb in cls._SEARCH_VERBS:
            cleaned = re.sub(rf"\b{verb}\b", " ", cleaned, count=1)
        cleaned = re.sub(r"^\s*(?:por|sobre|de|do|da|no|na|o|a)\s+", " ", cleaned)

        # Remove a menção ao aparelho (e preposições que sobrarem).
        cleaned = re.sub(
            r"\s+(?:no|na|do|da|pelo|pela|para\s+o|para\s+a|pro|pra)?\s*"
            r"(?:meu\s+|minha\s+)?(?:celular|telefone|smartphone|android)\s*$",
            " ",
            cleaned,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!")
        return cleaned

    @classmethod
    def _extract_phone_music_request(cls, text):
        """Extrai (query, platform) de "toca X no spotify no celular".

        Devolve ("", "") quando não é um pedido de música no celular.
        """
        if not text:
            return "", ""

        verbs = ("toca", "tocar", "toque", "passa", "passar", "coloca",
                 "coloque", "bota", "bote", "reproduz", "reproduzir")
        if not any(re.search(rf"\b{verb}\b", text) for verb in verbs):
            return "", ""

        if not any(word in text for word in cls._PHONE_WORDS):
            return "", ""

        # Precisa de "spotify"/"música" para não confundir com abrir app.
        if "spotify" not in text and "musica" not in text and "música" not in text:
            return "", ""

        platform = "spotify" if "spotify" in text else "youtube"

        cleaned = text
        for verb in verbs:
            cleaned = re.sub(rf"\b{verb}\b", " ", cleaned, count=1)

        # Remove "no spotify", "no youtube" etc.
        cleaned = re.sub(r"\b(?:no|na)\s+(?:spotify|youtube|deezer)\b", " ", cleaned)
        # Remove "no celular" e similares (com preposições residuais).
        cleaned = re.sub(
            r"\s+(?:no|na|do|da|pelo|pela|para\s+o|para\s+a|pro|pra)?\s*"
            r"(?:meu\s+|minha\s+)?(?:celular|telefone|smartphone|android)\s*$",
            " ",
            cleaned,
        )
        # Remove artigos e recheio do começo.
        cleaned = re.sub(r"^\s*(?:o|a|os|as|um|uma|musica|música|a\s+musica|a\s+música)\s+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!")
        return cleaned, platform

    @staticmethod
    def _extract_file_to_send(text):
        """Extrai o nome do arquivo em "manda o arquivo X pro celular".

        A normalização remove pontuação, então reconstruímos a extensão
        provável (ex.: "relatorio pdf" -> "relatorio.pdf") para que o
        arquivo seja localizável no disco.
        """
        match = re.search(
            r"(?:manda|mandar|envia|enviar|passa|passar)\s+(?:o\s+|a\s+)?(?:arquivo\s+)?(.+?)\s+"
            r"(?:pro|para o|para a|no|na)\s+(?:meu\s+|minha\s+)?(?:celular|telefone|smartphone|android)",
            text,
        )
        if not match:
            return ""

        candidate = match.group(1).strip(" ,.!")
        if not candidate:
            return ""

        # "relatorio pdf" -> "relatorio.pdf" (extensão separada pela normalização).
        candidate = re.sub(
            r"\s+(pdf|docx?|xlsx?|pptx?|txt|csv|png|jpe?g|gif|mp3|mp4|zip)$",
            r".\1",
            candidate,
        )
        return candidate

    @staticmethod
    def _extract_volume_level(text):
        """Extrai um volume em % de "coloca o volume da tv em 40%" -> 0.4.

        Só aceita números com contexto de volume (% / "por cento" / próximo
        de "volume" ou "som"). Antes, qualquer número no texto virava volume:
        "joga ...video.mp4 na tv" era lido como volume 4%, e o pedido de
        vídeo nunca chegava à TV.
        """
        # Remove URLs e extensões para não confundir "mp4"/"1080p" com volume.
        cleaned = re.sub(r"https?://\S+", " ", text)
        cleaned = re.sub(r"\b\d{3,4}p\b", " ", cleaned)

        match = re.search(r"(\d{1,3})\s*(?:%|por cento)", cleaned)
        if not match:
            match = re.search(
                r"\b(?:volume|som)\b[^\d]{0,20}?(\d{1,3})\b",
                cleaned,
            )
        if not match:
            match = re.search(
                r"\b(\d{1,3})\b[^\d]{0,20}?(?:volume|som)\b",
                cleaned,
            )
        if not match:
            return None
        value = int(match.group(1))
        if value > 100:
            return None
        return value / 100.0

    @classmethod
    def _extract_tv_app(cls, text):
        """Extrai o app pedido para a TV: "abre o netflix na tv" -> "netflix".

        Só casa quando há verbo de abrir + um app conhecido + a TV na frase.
        """
        match = re.search(
            r"\b(?:abre|abrir|abra|executa|executar|inicia|iniciar|quer ver|"
            r"quero ver)\s+(?:o\s+|a\s+|no\s+|na\s+)?([a-z0-9\s]+?)(?:\s+(?:na|no|em)\s+(?:tv|televisao|televisor)|\s+(?:na|no)\s+tv)?$",
            text,
        )
        if not match:
            return ""
        candidate = match.group(1).strip(" ,.!")
        if not candidate:
            return ""
        # Reconhece apelidos conhecidos (netflix, youtube, spotify, hbo...).
        from core.connectivity.tv import TVConnector
        app_id, resolved = TVConnector.resolve_tv_app(candidate)
        return resolved if app_id else ""

    @staticmethod
    def _extract_media_url(text):
        """Extrai um link de vídeo/áudio para enviar à TV.

        A URL completa (com o esquema http/https) é preservada; por isso
        usamos o texto ORIGINAL e não o normalizado, que remove a pontuação.
        """
        match = re.search(r"https?://\S+", str(text or ""))
        if not match:
            return ""
        return match.group(0).rstrip(".,;!?")

    @staticmethod
    def _youtube_search_url(query):
        """Monta a URL de resultado do YouTube para o Cast abrir o vídeo."""
        from urllib.parse import quote_plus
        return "https://www.youtube.com/results?search_query=" + quote_plus(query)

    @classmethod
    def _extract_tv_youtube_query(cls, text):
        """Extrai o que tocar na TV: "toca X na tv", "passa o video X na tv".

        Devolve a busca (sem as palavras de comando e sem "na tv"), ou "".
        """
        if not text:
            return ""
        # Precisa ser um pedido de reprodução/envio/pesquisa.
        verbs = ("toca", "tocar", "toque", "passa", "passar", "joga", "jogar",
                 "manda", "mandar", "envia", "enviar", "coloca", "coloque", "colocar",
                 "abre", "abrir", "mostra", "mostrar", "reproduz", "reproduzir",
                 "pesquisa", "pesquise", "pesquisar", "busca", "busque", "buscar",
                 "procura", "procure", "procurar")
        if not any(re.search(rf"\b{verb}\b", text) for verb in verbs):
            return ""

        # Remove o verbo inicial, o destino ("na tv") e palavras de recheio.
        cleaned = re.sub(
            r"\b(?:toca|tocar|toque|passa|passar|joga|jogar|manda|mandar|envia|enviar|"
            r"coloca|coloque|colocar|abre|abrir|mostra|mostrar|reproduz|reproduzir|"
            r"pesquisa|pesquise|pesquisar|busca|busque|buscar|procura|procure|procurar)\b",
            " ",
            text,
        )
        cleaned = re.sub(
            r"\b(?:na|no|para a|para a|pra|pro|pela|pelo)\s+(?:minha\s+|meu\s+)?"
            r"(?:tv|televisao|televisor|chromecast|youtube)\b",
            " ",
            cleaned,
        )
        cleaned = re.sub(r"\b(?:o|a|os|as|um|uma|esse|essa|este|esta|video|vídeo|musica|música|filme|clip|clipe)\b", " ", cleaned)
        # Conectivos residuais de fala natural: "pesquisa POR lofi", "toca "
        # "MUSICA DE" X etc.
        cleaned = re.sub(r"^\s*(?:por|sobre|de|do|da)\s+", " ", cleaned)
        # Resíduo de "...no youtube da tv": a expressão acima remove o bloco
        # "no youtube" e sobra "da tv" no fim.
        cleaned = re.sub(r"\s+(?:da|do|na|no)?\s*(?:tv|televisao|televisor)\s*$", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!")
        return cleaned

    @classmethod
    def _extract_tv_spotify_query(cls, text):
        """Extrai a busca do Spotify da TV: "toca menos e mais no spotify da tv".

        Retorna o termo (sem "no spotify da tv"), ou "".
        """
        if not text or "spotify" not in text:
            return ""
        match = re.search(
            r"\b(?:toca|tocar|toque|passa|passar|coloca|coloque|manda|mandar|"
            r"envia|enviar|reproduz|reproduzir|ouca|ouvir|queria ouvir|quero ouvir)\s+"
            r"(.+?)\s+(?:no|na)\s+spotify(?:\s+(?:da|do)\s+(?:tv|televisao|televisor))?\s*$",
            text,
        )
        if not match:
            return ""
        query = match.group(1).strip(" ,.!")
        # Remove artigos/recheio comuns no início da busca.
        query = re.sub(r"^(?:o|a|os|as|um|uma|musica|música|banda|cantor|cantora)\s+", "", query)
        return query.strip(" ,.!")

    @staticmethod
    def _extract_alarm_request(text):
        """Extrai (hour, minute, label) de "me acorda as 6 e 30 no celular".

        Aceita "6:30", "6 e 30", "6h30", "6 horas", "as 7".
        """
        if not any(word in text for word in (
            "alarme", "despertador", "me acorda", "me acorde", "acorda", "desperta",
        )):
            return None

        # "6:30" / "6h30" / "6 e 30" — testado ANTES da hora simples,
        # senão "5:45" seria lido como "5".
        # Obs.: o normalize_text troca ':' por espaço, então "5:45" chega
        # como "5 45" e precisa ser tratado aqui também.
        match = re.search(r"(\d{1,2})\s*(?::|h|e)\s*(\d{2})", text)
        if not match:
            match = re.search(r"(\d{1,2})\s+(\d{2})\b", text)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
        else:
            # Só a hora: "as 7" / "7 horas". Ancora no verbo para não pegar
            # números soltos do resto da frase.
            match = re.search(
                r"(?:as|às|a|para\s+as|para\s+às)\s+(\d{1,2})\s*(?:h|horas?)?",
                text,
            )
            if not match:
                match = re.search(r"\b(\d{1,2})\s*(?:h|horas?)\b", text)
            if not match:
                return None
            hour, minute = int(match.group(1)), 0

        # Ajusta período: "7 da noite" -> 19h.
        if any(word in text for word in ("da noite", "da tarde")) and hour < 12:
            hour += 12
        if "da manha" in text or "da manhã" in text:
            if hour == 12:
                hour = 0

        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            return None

        return {"hour": hour, "minute": minute, "label": "VEGA"}

    # Meios que ainda são "ligar": telefone comum, WhatsApp ou chamada de voz.
    _CALL_MEDIA = r"(?:whatsapp|whats|zap|telefone|celular|ligacao|ligação|voz)"

    @classmethod
    def _extract_call_request(cls, text):
        """Extrai (contact|number) de "liga pro joao no celular".

        Aceita também "liga para a rita pelo meu celular", "chama o joao no
        whatsapp" e "liga para a rita no whatsapp pelo celular".
        """
        # O alvo pode vir antes do meio ("... pelo meu celular") ou depois
        # ("... no whatsapp pelo celular").
        media = rf"(?:no|na|pelo|pela|por|do|da|via)\s+(?:meu\s+|minha\s+)?{cls._CALL_MEDIA}"
        match = re.search(
            rf"(?:liga|ligar|ligue|disque|discar|chama|chamar|telefona|telefonar)\s+"
            rf"(?:para\s+o\s+|para\s+a\s+|para\s+|pro\s+|pra\s+|o\s+|a\s+)?"
            rf"([\w\s]+?)\s+{media}",
            text,
        )
        if not match:
            return None

        target = match.group(1).strip(" ,.!")
        if not target:
            return None

        # Remove o nome do app quando ele ficou grudado no contato
        # ("chama o miguel no whatsapp" -> "miguel").
        target = re.sub(
            r"\s+(?:no|na|pelo|pela|por|do|da|via)\s+(?:meu\s+|minha\s+)?"
            rf"{cls._CALL_MEDIA}\s*$",
            "",
            target,
        ).strip(" ,.!")
        if not target:
            return None

        # Meio da chamada: WhatsApp quando citado, senão telefone comum.
        app = "whatsapp" if re.search(r"whatsapp|whats|zap", text) else ""

        # Número explícito?
        if re.fullmatch(r"[\d\s()+-]{4,}", target):
            return {"number": target, "app": app}
        return {"contact": target, "app": app}

    @staticmethod
    def _extract_reply_request(text):
        """Extrai (contact, text) de "responde o miguel" ou "responde o miguel dizendo que eu vou".

        Sem o conteúdo, a VEGA lê a última mensagem e propõe uma resposta;
        com "dizendo que...", o conteúdo é o que o usuário falou.
        """
        match = re.search(
            r"(?:responde|responder|resposta\s+para|responde\s+para)\s+"
            r"(?:o\s+|a\s+|para\s+o\s+|para\s+a\s+|pro\s+|pra\s+)?"
            r"([\w\s]+?)"
            r"(?:\s+(?:dizendo|falando|escrito)\s+(?:que\s+)?(.+?))?$",
            text,
        )
        if not match:
            return None
        contact = match.group(1).strip(" ,.!")
        if not contact:
            return None
        # "responde o miguel no celular" -> o meio não faz parte do nome.
        contact = re.sub(
            r"\s+(?:no|na|pelo|pela|por|do|da|via)\s+(?:meu\s+|minha\s+)?"
            r"(?:celular|telefone|smartphone|android|whatsapp|whats|zap)\s*$",
            "",
            contact,
        ).strip(" ,.!")
        if not contact:
            return None
        message = (match.group(2) or "").strip(" ,.!")
        return {"contact": contact, "text": message}

    @staticmethod
    def _extract_conversation_read_request(text):
        """Extrai o contato de "o que o miguel me mandou?" / "le as mensagens do miguel"."""
        patterns = (
            r"(?:o\s+que|quais)\s+(?:o\s+|a\s+)?([\w\s]+?)\s+(?:me\s+)?(?:mandou|manda|escreveu|enviou)",
            r"(?:le|ler|leia|mostra|mostre|ver)\s+(?:as\s+)?(?:mensagens|msgs|conversas?)\s+(?:do|da|de)\s+([\w\s]+?)$",
            r"(?:mensagens|msgs)\s+(?:do|da|de)\s+([\w\s]+?)$",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            contact = match.group(1).strip(" ,.!?")
            if contact:
                return {"contact": contact}
        return None

    @staticmethod
    def _extract_message_request(text):
        """Extrai (contact, text) de "manda mensagem pro joao dizendo que eu vou".

        O texto da mensagem é OPCIONAL: "manda mensagem pro joao no whatsapp"
        (sem conteúdo) devolve o contato com text="" e a VEGA pergunta o que
        escrever. Antes, sem "dizendo...", o pedido era ignorado por inteiro
        e a fala ia para a IA — a VEGA fingia que não ouviu.
        """
        match = re.search(
            r"(?:manda|mandar|envia|enviar|escreve|escrever)\s+"
            r"(?:uma\s+)?(?:mensagem|msg|zap)\s+"
            r"(?:para\s+o\s+|para\s+a\s+|pro\s+|pra\s+|ao\s+|a\s+)?"
            r"([\w\s]+?)\s+"
            r"(?:dizendo|falando|escrito)\s+(?:que\s+)?(.+?)"
            r"(?:\s+(?:no|pelo|do)\s+(?:meu\s+|minha\s+)?(?:celular|telefone|whatsapp|zap|smartphone))?$",
            text,
        )
        if match:
            contact = match.group(1).strip(" ,.!")
            message = match.group(2).strip(" ,.!")
            if contact and message:
                return {"contact": contact, "text": message}

        # Sem o texto: captura o destinatário e deixa a VEGA perguntar o que
        # escrever ("manda mensagem pro joao no whatsapp").
        match = re.search(
            r"(?:manda|mandar|envia|enviar|escreve|escrever)\s+"
            r"(?:uma\s+)?(?:mensagem|msg|zap)\s+"
            r"(?:para\s+o\s+|para\s+a\s+|pro\s+|pra\s+|ao\s+|a\s+)?"
            r"([\w\s]+?)\s*"
            r"(?:\s+(?:no|pelo|do)\s+(?:meu\s+|minha\s+)?(?:celular|telefone|whatsapp|zap|smartphone))?$",
            text,
        )
        if match:
            contact = match.group(1).strip(" ,.!")
            # Sem destinatário ("manda mensagem no whatsapp"): nem entra.
            if contact and contact.lower() not in {"no", "pelo", "no meu"}:
                return {"contact": contact, "text": ""}
        return None

    # ==========================================================
    # MÚSICA — TOCAR MÚSICA/PLAYLIST ESPECÍFICA
    # ==========================================================

    _MUSIC_PLAY_PATTERNS = (
        "toca a música ", "tocar a música ", "toque a música ",
        "toca musica ", "tocar musica ", "toque musica ",
        "toca a musica ", "tocar a musica ", "toque a musica ",
        "põe a música ", "poe a musica ", "põe música ", "poe musica ",
        "coloca a música ", "coloca musica ", "coloca a musica ",
        "solta a música ", "solta musica ", "solta a musica ",
        "manda a música ", "manda musica ", "manda a musica ",
        "toca a playlist ", "tocar a playlist ", "toque a playlist ",
        "toca playlist ", "tocar playlist ", "toque playlist ",
        "toca a minha playlist ", "tocar a minha playlist ", "toque a minha playlist ",
        "toca minha playlist ", "tocar minha playlist ", "toque minha playlist ",
        "toca uma playlist minha ", "tocar uma playlist minha ", "toque uma playlist minha ",
        "toca as minhas playlists ", "tocar as minhas playlists ", "toque as minhas playlists ",
        "toca minhas playlists ", "tocar minhas playlists ", "toque minhas playlists ",
        "toca as minhas musicas ", "tocar as minhas musicas ", "toque as minhas musicas ",
        "toca minhas musicas ", "tocar minhas musicas ", "toque minhas musicas ",
        "põe a playlist ", "poe a playlist ", "põe playlist ", "poe playlist ",
        "põe a minha playlist ", "poe a minha playlist ", "põe minha playlist ", "poe minha playlist ",
        "coloca a playlist ", "coloca playlist ", "coloca a playlist ",
        "coloca a minha playlist ", "coloca minha playlist ",
        "solta a playlist ", "solta playlist ",
        "solta a minha playlist ", "solta minha playlist ",
        "toca ", "tocar ", "toque ",  # genérico por último
    )

    _MUSIC_PLATFORMS = (
        "no spotify", "no youtube", "no yt", "no deezer", "na deezer",
        "no youtube music", "na youtube music",
    )

    @classmethod
    def _extract_music_request(cls, text):
        """Extrai (query, platform, playlist) de um pedido de música.

        Retorna None quando o texto não é um pedido de tocar música.
        Ignora pedidos que são de outra natureza (pesquisa, abrir app).
        """

        if not text:
            return None

        normalized = text.strip()

        # Tolerância ao wake word: "vega toca a musica X".
        normalized = re.sub(r"^vega\s+", "", normalized)

        # Não capturar pesquisas e comandos de app:
        if (
            "pesquis" in normalized
            or "procur" in normalized
            or normalized.startswith("abre")
            or normalized.startswith("abra")
            or normalized.startswith("abrir")
        ):
            return None

        # Play/pause/skip de mídia genérico não é "tocar música X".
        if normalized in (
            "play", "pause", "próxima", "proxima", "toca musica", "tocar musica",
            "toque musica", "toca a musica", "tocar a musica", "toque a musica",
            "toca uma musica", "tocar uma musica", "toque uma musica",
        ):
            return None

        playlist = "playlist" in normalized

        platform = "youtube"
        for marker in cls._MUSIC_PLATFORMS:
            if marker in normalized:
                if "spotify" in marker:
                    platform = "spotify"
                elif "deezer" in marker:
                    platform = "deezer"
                break

        # Tenta os padrões mais específicos primeiro (playlist antes de música).
        for prefix in cls._MUSIC_PLAY_PATTERNS:
            if normalized.startswith(prefix):
                query = normalized[len(prefix):].strip()
                # Remove a menção da plataforma da query: "toca radio ga ga no spotify"
                for marker in cls._MUSIC_PLATFORMS:
                    if marker in query:
                        query = query.split(marker)[0].strip(" ,.!")
                        break
                # Remove caudas comuns de fala.
                query = re.sub(r"\s+(por favor|ai|aí|pra mim|para mim)$", "", query).strip()
                # Remove artigos/preposições iniciais residuais ("de funk" -> "funk", "do alok" -> "alok")
                query = re.sub(r"^(?:de|da|do|das|dos)\s+", "", query).strip()
                if not query:
                    # "toca no youtube" sem faixa: é um pedido de música na
                    # plataforma, não uma faixa chamada "no youtube". O
                    # play_music pergunta o que tocar quando a query vem vazia.
                    return ("", platform, playlist)
                return (query, platform, playlist)

        return None

    # ==========================================================
    # PLAYLISTS PESSOAIS — "toca minha playlist X"
    # ==========================================================

    _MY_PLAYLIST_MARKERS = (
        "minha playlist", "minhas playlists", "a minha playlist",
        "as minhas playlists",
    )

    @classmethod
    def _extract_personal_playlist(cls, text):
        """Extrai (nome, platform) de um pedido de playlist pessoal.

        Reconhece "toca minha playlist X", "coloca a minha playlist X no
        spotify" e formas equivalentes. Retorna None quando o pedido é de
        música normal ("toca a música X") ou não menciona "minha playlist".
        """

        if not text:
            return None

        normalized = text.strip()
        normalized = re.sub(r"^vega\s+", "", normalized)

        if not any(marker in normalized for marker in cls._MY_PLAYLIST_MARKERS):
            return None

        # Precisa ser um verbo de reprodução.
        if not any(
            normalized.startswith(verb)
            for verb in ("toca", "tocar", "toque", "poe", "põe", "coloca", "colocar", "solta", "abre", "abra")
        ):
            return None

        platform = "youtube"
        for marker in cls._MUSIC_PLATFORMS:
            if marker in normalized:
                if "spotify" in marker:
                    platform = "spotify"
                elif "deezer" in marker:
                    platform = "deezer"
                break

        # Isola o nome que vem depois do marcador "minha playlist".
        match = re.search(
            r"(?:minha playlist|minhas playlists)\s+(.+)$",
            normalized,
        )
        if not match:
            # "toca minha playlist no spotify" sem nome nomeado.
            return ("", platform)

        name = match.group(1).strip()

        # Remove a plataforma citada no fim: "treino no spotify" -> "treino".
        for marker in cls._MUSIC_PLATFORMS:
            if marker in name:
                name = name.split(marker)[0].strip(" ,.!")
                break

        name = re.sub(r"\s+(por favor|ai|aí|pra mim|para mim)$", "", name).strip()
        name = re.sub(r"^(?:de|da|do|das|dos)\s+", "", name).strip()
        name = re.sub(r"^(?:que|chamada|chamado)\s+", "", name).strip()

        return (name, platform)

    @classmethod
    def _extract_save_playlist(cls, text):
        """Extrai (nome, platform) de "guarda essa playlist X"."""

        if not text:
            return None

        normalized = text.strip()
        normalized = re.sub(r"^vega\s+", "", normalized)

        match = re.match(
            r"(?:guarda|guardar|salva|salvar|salve)\s+"
            r"(?:essa|esta|a)?\s*playlist\s+(.+)$",
            normalized,
        )
        if not match:
            return None

        name = match.group(1).strip()
        platform = "youtube"
        for marker in cls._MUSIC_PLATFORMS:
            if marker in name:
                if "spotify" in marker:
                    platform = "spotify"
                elif "deezer" in marker:
                    platform = "deezer"
                name = name.split(marker)[0].strip(" ,.!")
                break

        name = re.sub(r"\s+(por favor|ai|aí|pra mim|para mim)$", "", name).strip()
        return (name, platform) if name else None

    @classmethod
    def _extract_list_playlists(cls, text):
        """Reconhece pedidos de listar as playlists guardadas."""
        if not text:
            return False
        normalized = text.strip()
        return bool(re.search(
            r"\b(?:quais|liste|listar|mostra|mostre|minhas)\b.*\bplaylists?\b",
            normalized,
        ))

    # ==========================================================
    # MÍDIA
    # ==========================================================

    @staticmethod
    def _is_media_play_pause_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "pausa a música",
                "pausar música",
                "pausa música",
                "pausar a música",
                "pausa a mídia",
                "pausar mídia",
                "pausar a mídia",
                "continua a música",
                "continuar música",
                "continua música",
                "continuar a música",
                "toca musica",
                "tocar musica",
                "toque musica",
                "toca uma musica",
                "tocar uma musica",
                "toque uma musica",
                "play pause",
                "play",
                "pause",
            )
        )

    @staticmethod
    def _is_media_next_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "próxima música",
                "proxima musica",
                "próxima mídia",
                "proxima midia",
                "pula música",
                "pular música",
                "pula a música",
                "pular a música",
                "próxima",
                "proxima",
            )
        )

    @staticmethod
    def _is_media_previous_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "música anterior",
                "musica anterior",
                "mídia anterior",
                "midia anterior",
                "volta música",
                "voltar música",
                "volta a música",
                "voltar a música",
            )
        )

    @staticmethod
    def _is_media_stop_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "para a música",
                "parar música",
                "para música",
                "parar a música",
                "para a mídia",
                "parar mídia",
                "parar a mídia",
            )
        )

    # ==========================================================
    # JANELAS
    # ==========================================================

    @staticmethod
    def _is_close_window_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "fecha a janela",
                "fechar a janela",
                "fecha janela",
                "fechar janela",
                "fecha isso",
                "fechar isso",
                "fecha essa",
                "fechar essa",
                "fecha o programa",
                "fechar o programa",
                "encerra o programa",
                "encerrar o programa",
                "fecha o aplicativo",
                "fechar o aplicativo",
                "encerra o aplicativo",
                "encerrar o aplicativo",
                "encerra a janela",
                "encerra isso",
                "sai daqui",
            )
        )

    @staticmethod
    def _is_minimize_all_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "minimiza tudo",
                "minimizar tudo",
                "minimiza todas",
                "minimizar todas",
                "minimiza todas as janelas",
                "minimizar todas as janelas",
                "mostra a área de trabalho",
                "mostra area de trabalho",
                "vai pra área de trabalho",
                "vai pra area de trabalho",
            )
        )

    @staticmethod
    def _is_minimize_request(text):

        if VegaCommandsParser._is_minimize_all_request(text):
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "minimiza",
                "minimizar",
                "manda pra baixo",
                "joga pra baixo",
                "esconde a janela",
                "esconde isso",
                "deixa pequeno",
            )
        )

    @staticmethod
    def _is_maximize_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "maximiza",
                "maximizar",
                "aumenta a janela",
                "aumentar a janela",
                "abre a janela toda",
                "deixa grande",
            )
        )

    @staticmethod
    def _is_restore_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "restaura a janela",
                "restaurar a janela",
                "restaura janela",
                "volta a janela",
                "voltar a janela",
                "desmaximiza",
            )
        )

    @staticmethod
    def _is_switch_window_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "troca de janela",
                "trocar de janela",
                "muda de janela",
                "mudar de janela",
                "próxima janela",
                "proxima janela",
                "outra janela",
                "alterna janela",
                "alternar janela",
            )
        )

    @staticmethod
    def _is_fullscreen_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "tela cheia",
                "tela inteira",
                "fullscreen",
                "modo tela cheia",
            )
        )

    # ==========================================================
    # BRILHO
    # ==========================================================

    @staticmethod
    def _is_brightness_up_request(text):

        if "brilho" not in text:
            # Formas naturais que NÃO dizem "brilho" e por isso escapavam para
            # a IA — que então respondia sobre outra coisa ("mais claro" era
            # interpretado como clima). Aqui elas são reconhecidas localmente,
            # de graça e sem ambiguidade.
            if VegaCommandsParser._contains_any(
                text,
                (
                    "mais claro",
                    "mais clara",
                    "deixa claro",
                    "deixa mais claro",
                    "deixa a tela mais clara",
                    "aumenta a luz",
                    "aumenta a claridade",
                    "mais luminosidade",
                    "mais iluminado",
                ),
            ):
                # "claro" também significa tempo bonito: exige contexto de tela/pc.
                if VegaCommandsParser._contains_any(
                    text, ("tela", "pc", "computador", "monitor", "aqui", "notebook")
                ) or VegaCommandsParser._contains_any(
                    text, ("deixa mais claro", "mais claro", "mais clara")
                ):
                    return True
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "aumenta",
                "aumentar",
                "sobe",
                "subir",
                "mais brilho",
                "clareia",
                "clarear",
                "aumente",
            )
        )

    @staticmethod
    def _is_brightness_down_request(text):

        if "brilho" not in text:
            if VegaCommandsParser._contains_any(
                text,
                (
                    "mais escuro",
                    "mais escura",
                    "escurece a tela",
                    "deixa mais escuro",
                    "menos luz",
                    "menos luminosidade",
                ),
            ):
                if VegaCommandsParser._contains_any(
                    text, ("tela", "pc", "computador", "monitor", "aqui", "notebook")
                ) or VegaCommandsParser._contains_any(
                    text, ("deixa mais escuro", "mais escuro", "mais escura")
                ):
                    return True
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "diminui",
                "diminuir",
                "abaixa",
                "abaixar",
                "desce",
                "menos brilho",
                "escurece",
                "escurecer",
                "diminua",
            ),
        )

    @staticmethod
    def _is_brightness_max_request(text):

        if "brilho" not in text:
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "máximo",
                "maximo",
                "100",
                "100%",
                "no máximo",
                "no maximo",
                "brilho total",
            )
        )

    @staticmethod
    def _is_brightness_min_request(text):

        if "brilho" not in text:
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "mínimo",
                "minimo",
                "zero",
                "0%",
                "no mínimo",
                "no minimo",
                "brilho mínimo",
                "brilho minimo",
            )
        )

    # ==========================================================
    # BLUETOOTH
    # ==========================================================

    @staticmethod
    def _is_bluetooth_on_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "liga o bluetooth",
                "ligar o bluetooth",
                "ativa o bluetooth",
                "ativar o bluetooth",
                "liga bluetooth",
                "ligar bluetooth",
                "ativa bluetooth",
                "ativar bluetooth",
            )
        )

    @staticmethod
    def _is_bluetooth_off_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "desliga o bluetooth",
                "desligar o bluetooth",
                "desativa o bluetooth",
                "desativar o bluetooth",
                "desliga bluetooth",
                "desligar bluetooth",
                "desativa bluetooth",
                "desativar bluetooth",
            )
        )

    # ==========================================================
    # NOTIFICAÇÕES
    # ==========================================================

    @staticmethod
    def _is_notifications_on_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "liga as notificações",
                "ligar as notificações",
                "ativa as notificações",
                "ativar as notificações",
                "ativa notificações",
                "ativar notificações",
                "liga notificações",
                "ligar notificações",
            )
        )

    @staticmethod
    def _is_notifications_off_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "desliga as notificações",
                "desligar as notificações",
                "desativa as notificações",
                "desativar as notificações",
                "silencia as notificações",
                "silenciar notificações",
                "desliga notificações",
                "desligar notificações",
            )
        )

    # ==========================================================
    # TELA
    # ==========================================================

    @staticmethod
    def _is_screen_off_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "desliga a tela",
                "desligar a tela",
                "apaga a tela",
                "apagar a tela",
                "desliga o monitor",
                "desligar o monitor",
                "apaga o monitor",
                "apagar o monitor",
                "desliga a tela do pc",
                "desliga o vídeo do pc",
                "desligar o vídeo",
            )
        )

    # ==========================================================
    # BLOQUEAR
    # ==========================================================

    @staticmethod
    def _is_lock_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "bloqueia o pc",
                "bloquear o pc",
                "bloqueia pc",
                "bloquear pc",
                "bloqueia computador",
                "bloquear computador",
                "trava o pc",
                "travar o pc",
                "trava pc",
                "travar pc",
                "bloqueia minha tela",
                "bloquear minha tela",
            )
        )

    # ==========================================================
    # SISTEMA
    # ==========================================================

    @staticmethod
    def _is_restart_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "reinicia o pc",
                "reiniciar o pc",
                "reinicia pc",
                "reiniciar pc",
                "reinicia computador",
                "reiniciar computador",
                "reinicia a máquina",
                "reiniciar a máquina",
                "reinicia minha máquina",
                "reiniciar minha máquina",
            )
        )

    @staticmethod
    def _is_shutdown_request(text):

        if VegaCommandsParser._is_cancel_shutdown_request(text):
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "desliga o pc",
                "desligar o pc",
                "desliga pc",
                "desligar pc",
                "desliga computador",
                "desligar computador",
                "desliga a máquina",
                "desligar a máquina",
                "desliga meu pc",
                "desligar meu pc",
            )
        )

    @staticmethod
    def _is_cancel_shutdown_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "cancela o desligamento",
                "cancelar o desligamento",
                "cancela desligamento",
                "cancelar desligamento",
                "para o desligamento",
                "parar o desligamento",
                "cancela desligar",
                "cancelar desligar",
            )
        )

    # ==========================================================
    # FUNK
    # ==========================================================

    @staticmethod
    def _is_funk_request(text):

        if not re.search(
            r"\bfunk\b",
            text
        ):
            return False

        return VegaCommandsParser._contains_any(
            text,
            (
                "toca",
                "toque",
                "coloca",
                "coloque",
                "bota",
                "botar",
                "quero ouvir",
                "quero um",
                "manda",
                "põe",
                "poe",
                "tocar",
                "colocar",
                "ouvir",
            )
        )

    # ==========================================================
    # TECLADO
    # ==========================================================

    @staticmethod
    def _is_copy_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "copiar",
                "copia isso",
                "copia",
            )
        )

    @staticmethod
    def _is_paste_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "colar",
                "cola isso",
                "cola",
            )
        )

    @staticmethod
    def _is_cut_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "recortar",
                "recorta",
                "cortar",
            )
        )

    @staticmethod
    def _is_select_all_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "selecionar tudo",
                "seleciona tudo",
                "selecionar todos",
                "seleciona todos",
            )
        )

    @staticmethod
    def _is_undo_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "desfazer",
                "desfaz",
                "voltar ação",
                "volta a ação",
                "voltar a ação",
            )
        )

    @staticmethod
    def _is_redo_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "refazer",
                "refaz",
            )
        )

    @staticmethod
    def _is_save_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "salvar",
                "salva",
                "salve",
            )
        )

    @staticmethod
    def _is_new_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "criar novo",
                "cria novo",
                "criar uma nova",
                "cria uma nova",
                "novo documento",
                "novo arquivo",
            )
        )

    @staticmethod
    def _is_alt_tab_request(text):

        return VegaCommandsParser._contains_any(
            text,
            (
                "alt tab",
                "alterna janela",
                "alternar janela",
                "troca de janela",
                "trocar de janela",
            )
        )

    # ==========================================================
    # EXTRAIR APP
    # ==========================================================

    @staticmethod
    def _extract_app_name(text):

        if not text:
            return None

        value = VegaCommandsParser.normalize(
            text
        )

        patterns = (

            r"^pode abrir\s+",
            r"^poderia abrir\s+",
            r"^consegue abrir\s+",
            r"^quero abrir\s+",
            r"^quero iniciar\s+",

            r"^quero que voce abra\s+",
            r"^quero que você abra\s+",

            r"^por favor abra\s+",
            r"^por favor abre\s+",

            r"^abre\s+",
            r"^abra\s+",
            r"^abrir\s+",

            r"^inicia\s+",
            r"^inicie\s+",
            r"^iniciar\s+",

            r"^executa\s+",
            r"^execute\s+",
            r"^executar\s+",

            r"^roda\s+",
            r"^rodar\s+",
        )

        app = None

        for pattern in patterns:

            match = re.match(
                pattern,
                value,
                flags=re.IGNORECASE
            )

            if match:

                app = value[
                    match.end():
                ].strip()

                break

        if not app:
            return None

        app = re.sub(
            r"^(o|a|os|as)\s+",
            "",
            app
        ).strip()

        app = re.sub(
            r"^(ai|aí)\s+",
            "",
            app
        ).strip()

        app = re.sub(
            r"^(tambem|também|agora|agr)\s+",
            "",
            app
        ).strip()

        endings = (

            r"\s+pra mim$",
            r"\s+para mim$",
            r"\s+por favor$",
            r"\s+por gentileza$",
            r"\s+ai$",
            r"\s+aí$",
            r"\s+tambem$",
            r"\s+também$",
            r"\s+agora$",
        )

        changed = True

        while changed:

            changed = False

            for ending in endings:

                new_app = re.sub(
                    ending,
                    "",
                    app,
                    flags=re.IGNORECASE
                ).strip()

                if new_app != app:

                    app = new_app
                    changed = True

        # ------------------------------------------------------
        # Evita transformar comandos compostos em nome de app.
        # ------------------------------------------------------

        app = re.split(
            r"\s+(?:e|depois)\s+",
            app,
            maxsplit=1
        )[0]

        app = re.sub(
            r"\b(ai|aí)\b",
            " ",
            app
        )

        app = re.sub(
            r"\s+",
            " ",
            app
        )

        app = app.strip(
            " .,!?;:"
        )

        if not app:
            return None

        return app

    # ==========================================================
    # NORMALIZAR APP
    # ==========================================================

    @staticmethod
    def _normalize_app_name(name):

        if not name:
            return ""

        name = name.lower().strip()

        replacements = {

            "spotfy": "spotify",
            "spotif": "spotify",
            "spofity": "spotify",
            "spotifyy": "spotify",

            "google chrome": "chrome",

            "visual studio code": "vscode",
            "visualstudio code": "vscode",
            "vs code": "vscode",

            "microsoft edge": "edge",

            "discord app": "discord",

            "whats app": "whatsapp",
            "what's app": "whatsapp",

            "configuracoes": "configuracoes",
            "gerenciador de tarefas": "gerenciador de tarefas",
            "task manager": "gerenciador de tarefas",

            "navegador": "chrome",

        }

        for old, new in replacements.items():

            if name == old:
                name = new

        removable = {
            "tambem",
            "também",
            "ai",
            "aí",
            "agora",
            "agr",
            "por favor",
            "pfv",
            "pra mim",
            "para mim",
            "por gentileza",
            "aplicativo",
            "app",
        }

        words = name.split()

        words = [
            word
            for word in words
            if word not in removable
        ]

        return " ".join(words).strip()

    # ==========================================================
    # PESQUISAS
    # ==========================================================

    @staticmethod
    def _extract_search_query(
        text,
        platform_name
    ):

        patterns = (

            f"pesquise no {platform_name} por ",
            f"pesquisa no {platform_name} por ",
            f"pesquisar no {platform_name} por ",
            f"procure no {platform_name} por ",
            f"procura no {platform_name} por ",
            f"procurar no {platform_name} por ",

            f"pesquise no {platform_name} ",
            f"pesquisa no {platform_name} ",
            f"pesquisar no {platform_name} ",
            f"procure no {platform_name} ",
            f"procura no {platform_name} ",
            f"procurar no {platform_name} ",
        )

        for prefix in patterns:

            if text.startswith(prefix):

                query = text[
                    len(prefix):
                ].strip()

                query = re.sub(
                    r"^(por|sobre)\s+",
                    "",
                    query
                ).strip()

                if query:
                    return query

        return None

    @staticmethod
    def _extract_google_query(text):

        patterns = (

            "pesquise no google por ",
            "pesquisa no google por ",
            "pesquisar no google por ",
            "procure no google por ",
            "procura no google por ",
            "procurar no google por ",

            "pesquise no google ",
            "pesquisa no google ",
            "pesquisar no google ",
            "procure no google ",
            "procura no google ",
            "procurar no google ",

            "pesquise por ",
            "pesquisa por ",
            "pesquisar por ",
            "procure por ",
            "procura por ",
            "procurar por ",

            "pesquise ",
            "pesquisa ",
            "pesquisar ",
            "procure ",
            "procura ",
            "procurar ",
        )

        for prefix in patterns:

            if not text.startswith(prefix):
                continue

            query = text[
                len(prefix):
            ].strip()

            if not query:
                continue

            query = re.sub(
                r"^(por|sobre)\s+",
                "",
                query
            ).strip()

            if query:
                return query

        return None

    @classmethod
    def _is_database_request(cls, text):
        if not text:
            return False
        return bool(cls._DATABASE_VIEW_PATTERN.search(cls.normalize(text)))
