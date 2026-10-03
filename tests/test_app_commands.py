import os

from core.actions.commands import VegaCommands


def test_close_app_accepts_generic_process_name(monkeypatch):
    monkeypatch.setattr(VegaCommands, "_is_windows", staticmethod(lambda: True))

    def fake_run(command, capture_output=True, text=True, timeout=5):
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("core.actions.commands.subprocess.run", fake_run)
    monkeypatch.setattr(VegaCommands, "_find_matching_processes", staticmethod(lambda app_name: ["notepad.exe"]))

    assert VegaCommands.close_app("notepad") == "Fechando notepad."


def test_close_app_knows_windows_apps_opened_by_open_app(monkeypatch):
    monkeypatch.setattr(VegaCommands, "_is_windows", staticmethod(lambda: True))
    calls = []

    def fake_run(command, capture_output=True, text=True, timeout=5):
        calls.append(command)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("core.actions.commands.subprocess.run", fake_run)

    assert VegaCommands.close_app("bloco de notas") == "Fechando bloco de notas."
    assert calls == [["taskkill", "/IM", "notepad.exe", "/T", "/F"]]


def test_find_executable_falls_back_to_generic_name_match(monkeypatch, tmp_path):
    app_dir = tmp_path / "Program Files"
    app_dir.mkdir()
    app_exe = app_dir / "MyApp.exe"
    app_exe.write_bytes(b"test")

    monkeypatch.setenv("PROGRAMFILES", str(app_dir))
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    result = VegaCommands._find_executable("myapp")

    assert result is not None
    assert result.name.lower() == "myapp.exe"


def test_vega_prompt_explicitly_defines_identity():
    from core.brain.ai import VEGA_RUNTIME_PROMPT

    text = VEGA_RUNTIME_PROMPT.lower()
    assert "vega" in text
    assert "lívia" in text or "livia" in text
    assert "não é" in text or "não e" in text


def test_speaker_keeps_short_sentences_in_fewer_chunks():
    from core.audio.speaker import VegaSpeaker

    chunks = VegaSpeaker._split_text_for_speech(
        "Olá, tudo bem? Eu sou a VEGA, e estou pronta. Vamos testar."
    )

    assert chunks
    assert len(chunks) <= 2
    assert all(len(chunk) <= 260 for chunk in chunks)
    assert any(chunk.lower().startswith("olá") for chunk in chunks)


def test_speaker_uses_kokoro_defaults(monkeypatch):
    from core.audio.speaker import VegaSpeaker

    monkeypatch.setenv("EDGE_TTS_VOICE", "pt-BR-FranciscaNeural")

    voice = VegaSpeaker._resolve_tts_settings()

    assert voice == "pt-BR-FranciscaNeural"


def test_open_app_supports_windows_settings_and_task_manager(monkeypatch):
    monkeypatch.setattr(VegaCommands, "_is_windows", staticmethod(lambda: True))
    calls = []

    def fake_popen(args, creationflags=0):
        calls.append(args)
        return object()

    monkeypatch.setattr("core.actions.commands.subprocess.Popen", fake_popen)

    assert VegaCommands.open_app("configurações") == "Abrindo configurações."
    assert VegaCommands.open_app("gerenciador de tarefas") == "Abrindo gerenciador de tarefas."
    assert calls


def test_speaker_keeps_hyphen_and_semicolon_text_in_same_phrase(monkeypatch):
    from core.audio.speaker import VegaSpeaker

    chunks = VegaSpeaker._split_text_for_speech(
        "Vou abrir as configurações—agora; em seguida, teste a resposta."
    )

    assert chunks
    assert any("configurações" in chunk.lower() for chunk in chunks)
    assert any("em seguida" in chunk.lower() for chunk in chunks)
    assert len(chunks) <= 2


def test_brain_accepts_short_yes_no_and_maybe_answers():
    from core.brain.brain import VegaBrain

    assert VegaBrain._is_short_nonsense("sim") is False
    assert VegaBrain._is_short_nonsense("nao") is False
    assert VegaBrain._is_short_nonsense("não") is False
    assert VegaBrain._is_short_nonsense("talvez") is False


def test_holographic_environment_aliases_are_routed_directly():
    from core.brain.brain import VegaBrain

    aliases = (
        "novo projeto",
        "abrir projeto",
        "abrir ambiente holográfico",
        "iniciar ambiente holografico",
    )
    assert all(VegaBrain._is_holographic_environment_request(alias) for alias in aliases)


def test_holographic_environment_callback_is_invoked():
    from core.brain.brain import VegaBrain

    brain = VegaBrain.__new__(VegaBrain)
    calls = []
    brain._pending_dangerous_action = None
    brain.environment_callback = lambda: calls.append(True)
    response = brain.process("novo projeto")

    assert response == "Ambiente holográfico inicializado."
    assert calls == [True]
