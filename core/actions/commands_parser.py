import re
import unicodedata


class VegaCommandsParser:

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

    # ==========================================================
    # PROCESSAR COMANDO
    # ==========================================================

    def parse(self, text):

        """Ponto de entrada seguro: nunca levanta, devolve None sem match."""

        try:
            return self._parse_inner(text)
        except Exception as error:
            print(f"[VegaCommandsParser] Falha ao interpretar comando: {error}")
            return None

    def _parse_inner(self, text):

        # Entrada malformada nunca deve levantar: devolve um "no-match" seguro.
        if not isinstance(text, str):
            return None
        if not text or not text.strip():
            return None
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
        # SISTEMA
        # ======================================================

        # Cancelamento precisa vir antes de desligamento.
        if self._is_cancel_shutdown_request(normalized):
            return self.commands.cancel_shutdown()

        if self._is_restart_request(normalized):
            return self.commands.restart_pc()

        if self._is_shutdown_request(normalized):
            return self.commands.shutdown_pc()

        # ======================================================
        # TELA / SEGURANÇA
        # ======================================================

        if self._is_screen_off_request(normalized):
            return self.commands.turn_off_screen()

        if self._is_lock_request(normalized):
            return self.commands.lock_pc()

        # ======================================================
        # BLUETOOTH
        # ======================================================

        if self._is_bluetooth_on_request(normalized):
            return self.commands.set_bluetooth(True)

        if self._is_bluetooth_off_request(normalized):
            return self.commands.set_bluetooth(False)

        # ======================================================
        # NOTIFICAÇÕES
        # ======================================================

        if self._is_notifications_on_request(normalized):
            return self.commands.set_notifications(True)

        if self._is_notifications_off_request(normalized):
            return self.commands.set_notifications(False)

        # ======================================================
        # BRILHO
        # ======================================================

        if self._is_brightness_max_request(normalized):
            return self.commands.brightness_max()

        if self._is_brightness_min_request(normalized):
            return self.commands.brightness_min()

        if self._is_brightness_up_request(normalized):
            return self.commands.brightness_up()

        if self._is_brightness_down_request(normalized):
            return self.commands.brightness_down()

        # ======================================================
        # VOLUME
        # ======================================================

        if self._is_mute_request(normalized):
            return self.commands.volume_mute()

        if self._is_unmute_request(normalized):
            return self.commands.volume_unmute()

        if self._is_volume_max_request(normalized):
            return self.commands.volume_max()

        if self._is_volume_min_request(normalized):
            return self.commands.volume_min()

        if self._is_volume_up_request(normalized):
            return self.commands.volume_up()

        if self._is_volume_down_request(normalized):
            return self.commands.volume_down()

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

        if self._is_media_play_pause_request(normalized):
            return self.commands.media_play_pause()

        if self._is_media_next_request(normalized):
            return self.commands.media_next()

        if self._is_media_previous_request(normalized):
            return self.commands.media_previous()

        if self._is_media_stop_request(normalized):
            return self.commands.media_stop()

        # ======================================================
        # FECHAR APLICATIVO
        # ======================================================
        #
        # This must run before the generic "close window" shortcut. A
        # request such as "fecha o programa Spotify" also contains the
        # generic phrase "fecha o programa", but it has a named target and
        # should terminate that application instead of only closing the
        # currently active window.
        close_app_name = self._extract_close_app_name(normalized)

        if close_app_name:
            close_app_name = self._normalize_app_name(close_app_name)
            if close_app_name:
                return self.commands.close_app(close_app_name)

        # ======================================================
        # JANELAS
        # ======================================================

        if self._is_close_window_request(normalized):
            return self.commands.close_window()

        if self._is_minimize_all_request(normalized):
            return self.commands.minimize_all()

        if self._is_fullscreen_request(normalized):
            return self.commands.fullscreen()

        if self._is_maximize_request(normalized):
            return self.commands.maximize_window()

        if self._is_restore_request(normalized):
            return self.commands.restore_window()

        if self._is_minimize_request(normalized):
            return self.commands.minimize_window()

        if self._is_switch_window_request(normalized):
            return self.commands.switch_window()

        # ======================================================
        # TECLADO
        # ======================================================

        if self._is_select_all_request(normalized):
            return self.commands.select_all()

        if self._is_copy_request(normalized):
            return self.commands.copy()

        if self._is_paste_request(normalized):
            return self.commands.paste()

        if self._is_cut_request(normalized):
            return self.commands.cut()

        if self._is_undo_request(normalized):
            return self.commands.undo()

        if self._is_redo_request(normalized):
            return self.commands.redo()

        if self._is_save_request(normalized):
            return self.commands.save()

        if self._is_new_request(normalized):
            return self.commands.new()

        if self._is_alt_tab_request(normalized):
            return self.commands.alt_tab()

        # ======================================================
        # CONECTIVIDADE — CELULAR / TV
        # (antes de música/pesquisa: "pausa a música no celular"
        # deve ir para o celular, não para a mídia do PC)
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
            return self.commands.connectivity_action(action, text=normalized, **kwargs)

        # ======================================================
        # FUNK
        # ======================================================

        if self._is_funk_request(normalized):
            return self.commands.play_funk()

        # ======================================================
        # PESQUISA YOUTUBE
        # ======================================================

        youtube_query = self._extract_search_query(
            normalized,
            "youtube"
        )

        if youtube_query:
            return self.commands.search_youtube(
                youtube_query
            )

        # ======================================================
        # PESQUISA GOOGLE
        # ======================================================

        google_query = self._extract_google_query(
            normalized
        )

        if google_query:
            return self.commands.search_google(
                google_query
            )

        # ======================================================
        # SITES
        # ======================================================

        if self._is_open_request(
            normalized,
            (
                "google",
                "navegador google",
            )
        ):
            return self.commands.open_google()

        if self._is_open_request(
            normalized,
            (
                "youtube",
            )
        ):
            return self.commands.open_youtube()

        if self._is_open_request(
            normalized,
            (
                "canva",
            )
        ):
            return self.commands.open_canva()

        if self._is_open_request(
            normalized,
            (
                "whatsapp",
                "whatsapp web",
            )
        ):
            return self.commands.open_whatsapp()

        if self._is_open_request(
            normalized,
            (
                "spotify",
            )
        ):
            return self.commands.open_spotify()

        # ======================================================
        # PASTAS
        # ======================================================

        if self._contains_any(
            normalized,
            (
                "documentos",
                "meus documentos",
            )
        ):
            if self._contains_open_action(normalized):
                return self.commands.open_documents()

        if self._contains_any(
            normalized,
            (
                "downloads",
                "meus downloads",
            )
        ):
            if self._contains_open_action(normalized):
                return self.commands.open_downloads()

        if self._contains_any(
            normalized,
            (
                "desktop",
                "área de trabalho",
            )
        ):
            if self._contains_open_action(normalized):
                return self.commands.open_desktop()

        # ======================================================
        # ABRIR APLICATIVO
        # ======================================================

        app_name = self._extract_app_name(
            normalized
        )

        if app_name:

            app_name = self._normalize_app_name(
                app_name
            )

            if app_name == "google":
                return self.commands.open_google()

            if app_name in {
                "youtube",
            }:
                return self.commands.open_youtube()

            if app_name in {
                "spotify",
            }:
                return self.commands.open_spotify()

            if app_name in {
                "canva",
            }:
                return self.commands.open_canva()

            if app_name in {
                "whatsapp",
                "whatsapp web",
            }:
                return self.commands.open_whatsapp()

            if app_name in {
                "documentos",
                "meus documentos",
            }:
                return self.commands.open_documents()

            if app_name in {
                "downloads",
                "meus downloads",
            }:
                return self.commands.open_downloads()

            if app_name in {
                "desktop",
                "área de trabalho",
            }:
                return self.commands.open_desktop()

            return self.commands.open_app(
                app_name
            )

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

            # Chrome
            "chorme": "chrome",
            "crome": "chrome",
            "cromee": "chrome",

            # Discord
            "discrod": "discord",
            "discor": "discord",

            # Steam
            "stema": "steam",
            "steem": "steam",

            # VS Code
            "vscod": "vscode",
            "vs code": "vscode",
            "visual studio code": "vscode",

            # WhatsApp
            "whats app": "whatsapp",
            "what's app": "whatsapp",
            "wpp": "whatsapp",

            # Outros
            "áudio": "audio",
            "brilho da tela": "brilho",
            "luminosidade": "brilho",

            "computadorzinho": "pc",
            "computador": "pc",

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

    _PHONE_WORDS = ("celular", "telefone", "smartphone", "android", "meu phone")
    _TV_WORDS = ("tv", "televisao", "televisor", "chromecast")
    # Palavras que apontam para o PC explicitamente.
    _PC_WORDS = ("computador", "pc", "desktop", "monitor", "meu pc", "no pc")

    # Frases que pedem captura de tela (mesma intenção para PC e celular).
    _SCREENSHOT_PHRASES = (
        "tira um print", "tira print", "tira um print da tela",
        "tira print da tela", "print da tela", "screenshot",
        "captura a tela", "capturar a tela", "tira uma foto da tela",
        "print",  # cobre "print do celular" e "print do pc"
    )

    # Frases que pedem espelhamento (celular) ou mostrar a tela.
    # "parar" é verificado ANTES, para não casar com "espelhar".
    _STOP_MIRROR_PHRASES = (
        "para de espelhar", "para o espelhamento", "parar o espelhamento",
        "fecha a tela do celular", "fecha o espelhamento",
        "encerra o espelhamento", "encerrar o espelhamento",
    )
    _MIRROR_PHRASES = (
        "espelha", "espelhar", "espelhamento", "mirror",
        "me mostra a tela", "mostra a tela", "mostre a tela",
        "exibe a tela", "exibir a tela", "quero ver a tela",
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

        device = cls._mentions_device(text)

        # Identificação do aparelho: "que celular é esse?", "qual é o meu celular?"
        if device == "phone" and any(phrase in text for phrase in (
            "que celular", "qual celular", "qual e o meu celular", "qual é o meu celular",
            "que aparelho", "qual aparelho", "de quem e esse celular",
            "de quem é esse celular", "meu celular e qual", "identifica o celular",
            "qual o modelo", "que modelo",
        )):
            return ("identify", {})

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
                if "pausa" in text or "pause" in text:
                    return ("media_pause", {})
                if "próxima" in text or "proxima" in text or "pula" in text:
                    return ("media_next", {})
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
                if any(word in text for word in ("pausa", "pause")):
                    return ("pause", {})
                if any(word in text for word in ("retoma", "continua", "volta a tocar", "play")):
                    return ("resume", {})
                if any(word in text for word in ("para a", "parar", "stop")):
                    return ("stop", {})
                if any(word in text for word in ("desliga", "desligar", "turn off")):
                    return ("turn_off", {})
                level = cls._extract_volume_level(text)
                if level is not None:
                    return ("volume", {"level": level})
                if "volume" in text or "som" in text:
                    return ("volume", {})
                if any(word in text for word in ("conecta", "conectar", "procura",
                                                 "liga a tv", "status da tv")):
                    return ("list", {})

        # Ações que fazem sentido sem citar o dispositivo explicitamente.
        if any(phrase in text for phrase in ("quais dispositivos", "meus dispositivos",
                                             "dispositivos conectados")):
            return ("__discover__", {})

        return None

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
        """Extrai um volume em % de "coloca o volume da tv em 40%" -> 0.4."""
        match = re.search(r"(\d{1,3})\s*(?:%|por cento)?", text)
        if not match:
            return None
        value = int(match.group(1))
        if value > 100:
            return None
        return value / 100.0

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
                if query:
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
            )
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
