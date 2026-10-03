import pytest

from core.actions.commands_parser import VegaCommandsParser


# ==========================================================
# Fakes
# ==========================================================


class FakeCommands:
    """Records emitted command calls so the parser can be tested in isolation."""

    def __init__(self, pending_homework=False):
        self.calls = []
        self._pending_homework = pending_homework

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return name

    # Homework dialog hooks
    def has_pending_homework(self):
        return self._pending_homework

    def handle_homework_dialog(self, text):
        self._record("handle_homework_dialog", text)
        return "homework-dialog"

    def start_homework_dialog(self, folder_or_keyword=None, language=None):
        self._record(
            "start_homework_dialog",
            folder_or_keyword=folder_or_keyword,
            language=language,
        )
        return "start-homework"

    # A handful of system/media/app commands used by the tests below.
    def close_app(self, name):
        return self._record("close_app", name)

    def close_window(self):
        return self._record("close_window")

    def open_app(self, name):
        return self._record("open_app", name)

    def open_google(self):
        return self._record("open_google")

    def open_youtube(self):
        return self._record("open_youtube")

    def open_chrome(self):
        return self._record("open_chrome")

    def open_documents(self):
        return self._record("open_documents")

    def open_spotify(self):
        return self._record("open_spotify")

    def minimize_all(self):
        return self._record("minimize_all")

    def minimize_window(self):
        return self._record("minimize_window")

    def search_google(self, query):
        return self._record("search_google", query)

    def search_youtube(self, query):
        return self._record("search_youtube", query)

    def volume_up(self):
        return self._record("volume_up")

    def volume_down(self):
        return self._record("volume_down")

    def volume_mute(self):
        return self._record("volume_mute")

    def brightness_max(self):
        return self._record("brightness_max")

    def shutdown_pc(self):
        return self._record("shutdown_pc")

    def cancel_shutdown(self):
        return self._record("cancel_shutdown")

    def open_database(self):
        return self._record("open_database")


@pytest.fixture
def parser():
    return VegaCommandsParser(FakeCommands())


# ==========================================================
# Valid commands
# ==========================================================


@pytest.mark.parametrize(
    "text, expected",
    [
        ("aumenta o volume", "volume_up"),
        ("abaixa o som", "volume_down"),
        ("muta o som", "volume_mute"),
    ],
)
def test_parse_valid_volume_commands(parser, text, expected):
    assert parser.parse(text) == expected
    assert parser.commands.calls[-1][0] == expected


def test_parse_normalizes_accent_and_case(parser):
    # Uppercase, accents and a trailing "!" must not change the outcome.
    assert parser.parse("AUMENTA O VOLUME!") == "volume_up"


def test_parse_normalizes_misspelled_app_name():
    # "spotfy" is normalized to "spotify" and routed to open_spotify.
    commands = FakeCommands()
    parser = VegaCommandsParser(commands)

    assert parser.parse("abre o spotfy") == "open_spotify"
    assert commands.calls[-1][0] == "open_spotify"


def test_parse_open_google_and_youtube(parser):
    assert parser.parse("abre o google") == "open_google"
    assert parser.parse("abre o youtube") == "open_youtube"


def test_parse_compound_browser_search_google():
    commands = FakeCommands()
    parser = VegaCommandsParser(commands)

    result = parser.parse("abre o chrome e pesquisa carros")

    assert result is not None
    called = [call[0] for call in commands.calls]
    assert "open_chrome" in called
    assert "search_google" in called
    assert ("search_google", ("carros",), {}) in commands.calls


def test_parse_compound_browser_search_youtube():
    commands = FakeCommands()
    parser = VegaCommandsParser(commands)

    parser.parse("abre o youtube e pesquisa musicas")

    assert ("search_youtube", ("musicas",), {}) in commands.calls


def test_parse_search_youtube_without_open(parser):
    assert parser.parse("pesquise no youtube gatos") == "search_youtube"


def test_parse_close_app_extracts_name():
    commands = FakeCommands()
    parser = VegaCommandsParser(commands)

    assert parser.parse("fecha o notepad") == "close_app"
    assert commands.calls[-1] == ("close_app", ("notepad",), {})


@pytest.mark.parametrize(
    "text",
    (
        "fechar spotfy",
        "feche o spotify",
        "encerre o spotify",
        "pode fechar o spotify",
        "fecha o aplicativo spotify",
    ),
)
def test_parse_close_app_accepts_voice_variants(text):
    commands = FakeCommands()
    parser = VegaCommandsParser(commands)

    assert parser.parse(text) == "close_app"
    assert commands.calls[-1] == ("close_app", ("spotify",), {})


def test_named_program_closes_app_but_generic_program_closes_window():
    commands = FakeCommands()
    parser = VegaCommandsParser(commands)

    assert parser.parse("fecha o programa spotify") == "close_app"
    assert commands.calls[-1] == ("close_app", ("spotify",), {})

    assert parser.parse("fecha o programa") == "close_window"
    assert commands.calls[-1] == ("close_window", (), {})

    assert parser.parse("encerra o aplicativo") == "close_window"
    assert commands.calls[-1] == ("close_window", (), {})


# ==========================================================
# Ambiguous commands / precedence
# ==========================================================


def test_parse_returns_none_for_empty_or_blank_input(parser):
    assert parser.parse("") is None
    assert parser.parse(None) is None
    assert parser.parse("    ") is None
    # No command should have been emitted for blank input.
    assert parser.commands.calls == []


def test_parse_returns_none_for_unrecognized_text(parser):
    assert parser.parse("qual é a cor do céu") is None


def test_cancel_shutdown_takes_precedence_over_shutdown(parser):
    # "cancela o desligamento" contains the shutdown keyword but must cancel.
    assert parser.parse("cancelar desligamento") == "cancel_shutdown"


def test_minimize_all_wins_over_single_minimize():
    commands = FakeCommands()
    parser = VegaCommandsParser(commands)

    assert parser.parse("minimiza tudo") == "minimize_all"
    assert commands.calls[-1][0] == "minimize_all"

    assert parser.parse("minimiza a janela") == "minimize_window"
    assert commands.calls[-1][0] == "minimize_window"


def test_minimize_request_is_not_minimize_all(parser):
    # A plain minimize should not be classified as minimize-all.
    assert VegaCommandsParser._is_minimize_all_request("minimiza a janela") is False
    assert VegaCommandsParser._is_minimize_request("minimiza a janela") is True
    assert VegaCommandsParser._is_minimize_request("minimiza tudo") is False


def test_homework_dialog_takes_precedence_when_pending():
    commands = FakeCommands(pending_homework=True)
    parser = VegaCommandsParser(commands)

    result = parser.parse("sim")

    assert result == "homework-dialog"
    assert commands.calls[-1][0] == "handle_homework_dialog"


@pytest.mark.parametrize(
    "text",
    (
        "vamos fazer a lista",
        "quero resolver as atividades de biologia",
        "me ajuda com o dever que está no PDF",
    ),
)
def test_homework_intent_is_deferred_to_semantic_tool_selection(parser, text):
    assert parser.parse(text) is None
    assert parser.commands.calls == []


# ==========================================================
# Malformed / edge-case input
# ==========================================================


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        "",
        "   ",
        "!@#$%^&*()",
        "\n\t",
        "🎉",
    ],
)
def test_parse_handles_malformed_without_raising(parser, malformed):
    # Must never raise and must not emit a command for junk input.
    result = parser.parse(malformed)
    assert result is None


def test_normalize_returns_empty_string_for_falsy_input():
    assert VegaCommandsParser.normalize(None) == ""
    assert VegaCommandsParser.normalize("") == ""


def test_normalize_lowercases_and_strips_punctuation():
    assert VegaCommandsParser.normalize("AUMENTA O VOLUME!!!") == "aumenta o volume"
    assert VegaCommandsParser.normalize("Volume---UP, agora") == "volume up agora"


def test_normalize_leaves_plain_ascii_untouched():
    assert VegaCommandsParser.normalize("abre o spotify") == "abre o spotify"


def test_contains_any_handles_empty_text():
    assert VegaCommandsParser._contains_any("", ("qualquer",)) is False
    assert VegaCommandsParser._contains_any(None, ("qualquer",)) is False


def test_extract_close_app_name_returns_none_when_no_match():
    assert VegaCommandsParser._extract_close_app_name("") is None
    assert VegaCommandsParser._extract_close_app_name("fecha") is None


# ==========================================================
# Hardening: parse() never raises on malformed input
# ==========================================================


class ExplodingCommands:
    """Commands whose dispatch methods always raise, to exercise the guard."""

    def __getattr__(self, name):
        def boom(*args, **kwargs):
            raise RuntimeError("dispatch failed")

        return boom


@pytest.mark.parametrize("bad", [None, 123, object(), [], {}, "", "   ", "\n\t"])
def test_parse_returns_none_for_non_string_or_blank(bad):
    parser = VegaCommandsParser(FakeCommands())
    assert parser.parse(bad) is None


def test_parse_does_not_raise_when_dispatch_fails():
    # Any exception raised deeper in dispatch must surface as a safe no-match.
    parser = VegaCommandsParser(ExplodingCommands())
    assert parser.parse("aumenta o volume") is None


def test_parse_returns_none_for_junk_text():
    parser = VegaCommandsParser(FakeCommands())
    assert parser.parse("!@#$%^&*()") is None


@pytest.mark.parametrize(
    "phrase",
    [
        "Vega, abre seu banco de dados",
        "abre seu banco de dados",
        "deixa eu ver seu banco de dados",
        "deixa ver seu banco de dados",
        "quero ver seu banco de dados",
        "mostra seu banco de dados",
        "olhar seu banco de dados",
        "deixa eu olhar seu banco de dados",
        "visualizar banco de dados",
        "abre o banco da vega",
        "abre seu banco",
        "mostra o seu banco de dados",
        "deixa eu ver o seu banco de dados",
        "pode abrir seu banco de dados",
        "acessar o banco de dados",
        "exibir banco de dados",
        "abre seu banco de dados por favor",
    ],
)
def test_database_view_variations(phrase):
    fake = FakeCommands()
    parser = VegaCommandsParser(fake)
    result = parser.parse(phrase)
    assert result == "open_database"
    assert ("open_database", (), {}) in fake.calls
