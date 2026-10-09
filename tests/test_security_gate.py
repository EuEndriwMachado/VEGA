from pathlib import Path
from types import SimpleNamespace

import pytest

from config import file_access
from core.security.file_security import FileSecurity
from core.security.security import VegaSecurity
from core.brain.text_utils import normalize_text
from core.brain.brain import VegaBrain


@pytest.fixture
def file_changes_enabled(monkeypatch):
    """Preserva os testes do fluxo autorizado quando a chave local é ativada."""
    monkeypatch.setattr(file_access, "ALLOW_PERSONAL_FILE_CHANGES", True)


def test_dangerous_action_requires_confirmation_and_pin(tmp_path):
    security = VegaSecurity(path=str(tmp_path / "security.json"))
    security.set_pin("1234")

    assert security.authorize_dangerous_action("apagar pasta de imagens", confirmed=False, pin="1234") is False
    assert security.authorize_dangerous_action("apagar pasta de imagens", confirmed=True, pin="9999") is False
    token = security.authorize_dangerous_action("apagar pasta de imagens", confirmed=True, pin="1234")
    assert token  # agora retorna um token de uso único


def test_pin_rotation_requires_current_pin(tmp_path):
    security = VegaSecurity(path=str(tmp_path / "security.json"))
    security.set_pin("1234")

    try:
        security.set_pin("5678")
        raise AssertionError("Rotation without current PIN should fail")
    except PermissionError:
        assert security.verify("1234")
        assert not security.verify("5678")

    try:
        security.set_pin("5678", current_pin="0000")
        raise AssertionError("Rotation with wrong current PIN should fail")
    except PermissionError:
        assert security.verify("1234")
        assert not security.verify("5678")

    security.set_pin("5678", current_pin="1234")
    assert not security.verify("1234")
    assert security.verify("5678")


def test_verify_locks_after_max_attempts(tmp_path, monkeypatch):
    """Lockout com relógio controlado (determinístico, sem sleep real).

    Antes usava LOCKOUT_SECONDS=0.05 + sleep(0.06): margem de 10 ms. Em uma
    execução lenta (suíte completa rodando antes), o lockout expirava antes do
    sleep terminar e o PIN correto era ACEITO quando o teste esperava falha.
    """
    import core.security.security as security_module

    fake_time = [1000.0]
    monkeypatch.setattr(security_module.time, "time", lambda: fake_time[0])

    security = VegaSecurity(path=str(tmp_path / "security.json"))
    security.set_pin("1234")

    original = VegaSecurity.LOCKOUT_SECONDS
    try:
        VegaSecurity.LOCKOUT_SECONDS = 30.0
        for _ in range(VegaSecurity.MAX_ATTEMPTS):
            assert not security.verify("9999")

        # Locked out: even the correct PIN is refused.
        assert not security.verify("1234")
        # Rotation is also denied while locked.
        try:
            security.set_pin("5678", current_pin="1234")
            raise AssertionError("Rotation while locked should fail")
        except PermissionError:
            pass

        # Avança o relógio falso além do lockout (sem dormir de verdade).
        fake_time[0] += 31.0
        assert security.verify("1234")
    finally:
        VegaSecurity.LOCKOUT_SECONDS = original


def test_face_validation_is_optional_when_camera_is_unavailable(tmp_path):
    security = VegaSecurity(path=str(tmp_path / "security.json"))
    security.set_pin("1234")

    assert security.authorize_dangerous_action(
        "deletar arquivos importantes",
        confirmed=True,
        pin="1234",
        face_supported=False,
    )


def test_security_accepts_natural_confirmation_and_pin_phrases():
    assert VegaBrain._has_security_confirmation("confirmo, a senha é 1234")
    assert VegaBrain._extract_security_pin("confirmo, o código 1234").group(1) == "1234"
    assert VegaBrain._extract_security_pin("confirmo, a senha é dezesseis vinte e quatro").group(0) == "1624"
    assert VegaBrain._extract_security_pin("confirmo, a senha é um, seis, dois, quatro").group(0) == "1624"
    assert VegaBrain._extract_security_pin("confirmo, a senha é 1, 6, 2, 4").group(0) == "1624"
    assert VegaBrain._has_security_confirmation("sim, é esse mesmo, senha 1624")
    assert VegaBrain._has_security_confirmation("pode apagar, código 1624")


@pytest.mark.parametrize(
    "spoken_pin",
    (
        "cinco seis sete nove",
        "cinquenta e seis setenta e nove",
        "cinquenta e seis sete nove",
        "cinquenta seis setenta nove",
        "cinquenta seis sete nove",
        "5 6 7 9",
    ),
)
def test_security_pin_accepts_spoken_group_variations(spoken_pin):
    match = VegaBrain._extract_security_pin(f"confirmo, senha {spoken_pin}")
    assert match is not None
    assert match.group(0) == "5679"


def test_pin_cannot_be_changed_by_voice():
    brain = VegaBrain.__new__(VegaBrain)
    assert "Não altero" in brain._process_security_request("defina o pin 1234")
    assert "Não altero" in brain._process_security_request("desativar pin")


def test_destructive_imperative_variants_are_protected():
    assert VegaSecurity._is_dangerous_action("apague a pasta vídeos")
    assert VegaSecurity._is_dangerous_action("exclua o arquivo de teste")
    assert VegaSecurity._is_dangerous_action("remova este arquivo")


def test_apague_phrase_returns_confirmation_prompt(monkeypatch, tmp_path, file_changes_enabled):
    brain = VegaBrain.__new__(VegaBrain)
    brain._pending_dangerous_action = None
    target = tmp_path / "garfo.txt"
    target.write_text("teste", encoding="utf-8")
    monkeypatch.setattr(FileSecurity, "find_files", lambda _query, **_kwargs: [target])
    response = brain._process_command("apague o arquivo garfo")
    assert "garfo.txt" in response
    assert any(term in response.lower() for term in ("confirme", "confirma", "quer apagar"))
    assert "senha" in response.lower() or "pin" in response.lower()
    assert f"na pasta {target.parent.name}" in response
    assert str(target) not in response
    assert brain._pending_dangerous_action["path"] == str(target)


def test_apaga_arquivo_chamado_extracts_real_filename(monkeypatch, tmp_path, file_changes_enabled):
    brain = VegaBrain.__new__(VegaBrain)
    target = tmp_path / "garfo.txt"
    target.write_text("teste", encoding="utf-8")
    monkeypatch.setattr(FileSecurity, "find_files", lambda query, **_kwargs: [target] if query == "garfo" else [])

    response = brain._process_command("apaga o arquivo chamado garfo")

    assert "garfo.txt" in response
    assert brain._pending_dangerous_action["query"] == "garfo"


def test_file_command_extracts_folder_qualifier(monkeypatch, tmp_path, file_changes_enabled):
    brain = VegaBrain.__new__(VegaBrain)
    target = tmp_path / "garfo.txt"
    target.write_text("teste", encoding="utf-8")
    calls = []

    def find_files(query, **kwargs):
        calls.append((query, kwargs.get("folder")))
        return [target]

    monkeypatch.setattr(FileSecurity, "find_files", find_files)
    response = brain._process_command("apague o arquivo chamado garfo dentro da pasta vídeos")

    assert "garfo.txt" in response
    assert calls == [("garfo", "vídeos")]


def test_file_search_prefers_exact_name_and_folder(tmp_path):
    videos = tmp_path / "Videos"
    tests_folder = tmp_path / "tests"
    videos.mkdir()
    tests_folder.mkdir()
    exact = videos / "garfo.txt"
    partial = tests_folder / "garfo_backup.txt"
    exact.write_text("exact", encoding="utf-8")
    partial.write_text("partial", encoding="utf-8")

    matches = FileSecurity.find_files("garfo", roots=[tmp_path], folder="vídeos")

    assert matches == [exact.resolve()]


def test_folder_alias_searches_music_directory(monkeypatch, tmp_path):
    from core.security import file_security

    music = tmp_path / "Music"
    music.mkdir()
    target = music / "interface.png"
    target.write_text("image", encoding="utf-8")
    monkeypatch.setattr(file_security.Path, "home", staticmethod(lambda: tmp_path))

    matches = FileSecurity.find_files("interface", folder="músicas")

    assert matches == [target.resolve()]


def test_compound_folder_phrase_is_extracted_correctly():
    text = "apague o arquivo chamado portas abertas na pasta vídeos"
    assert VegaBrain._extract_file_query(text) == "portas abertas"
    assert VegaBrain._extract_file_folder(text) == "vídeos"


def test_file_search_keeps_relevant_partial_names(tmp_path):
    exact = tmp_path / "garfo.txt"
    partial = tmp_path / "garfo_backup.txt"
    exact.write_text("exact", encoding="utf-8")
    partial.write_text("partial", encoding="utf-8")

    matches = FileSecurity.find_files("garfo", roots=[tmp_path])

    assert matches == [exact.resolve(), partial.resolve()]


def test_file_search_understands_singular_plural_and_incomplete_keywords(tmp_path):
    names = (
        "portas abertas.txt",
        "porta aberta.pdf",
        "portas aberta.docx",
        "porta abertas.py",
        "relatorio financeiro.txt",
    )
    for name in names:
        (tmp_path / name).write_text("test", encoding="utf-8")

    full_matches = FileSecurity.find_files("portas abertas", roots=[tmp_path])
    singular_matches = FileSecurity.find_files("porta aberta", roots=[tmp_path])
    keyword_matches = FileSecurity.find_files("portas", roots=[tmp_path])

    relevant = {path.name for path in full_matches}
    assert {"portas abertas.txt", "porta aberta.pdf", "portas aberta.docx", "porta abertas.py"} <= relevant
    assert "relatorio financeiro.txt" not in relevant
    assert "portas abertas.txt" in {path.name for path in singular_matches}
    assert "portas abertas.txt" in {path.name for path in keyword_matches}


def test_folder_choice_selects_file_from_multiple_matches(monkeypatch, tmp_path, file_changes_enabled):
    brain = VegaBrain.__new__(VegaBrain)
    videos = tmp_path / "Videos"
    documents = tmp_path / "Documentos"
    videos.mkdir()
    documents.mkdir()
    video_file = videos / "garfo_backup.txt"
    document_file = documents / "garfo.txt"
    video_file.write_text("video", encoding="utf-8")
    document_file.write_text("document", encoding="utf-8")
    monkeypatch.setattr(FileSecurity, "find_files", lambda _query, **_kwargs: [document_file, video_file])

    response = brain._process_command("apague o arquivo garfo")
    assert "garfo.txt" in response and "garfo_backup.txt" in response

    response = brain._process_pending_security_response("na pasta vídeos")
    assert "garfo_backup.txt" in response
    assert brain._pending_dangerous_action["path"] == str(video_file)


def test_authorized_unknown_target_never_returns_empty_response():
    class Parser:
        @staticmethod
        def parse(_action):
            return None

    class Tools:
        @staticmethod
        def resolve_for_text(_action):
            return None

    brain = VegaBrain.__new__(VegaBrain)
    brain.commands_parser = Parser()
    brain.tool_manager = Tools()
    response = brain._execute_authorized_action("apague o arquivo garfo")
    assert response == "Autorização validada, mas não encontrei um comando executável para esse alvo. Nada foi apagado."


def test_authorized_file_deletion_removes_confirmed_path(tmp_path, file_changes_enabled):
    target = tmp_path / "garfo.txt"
    target.write_text("teste", encoding="utf-8")

    class Security:
        @staticmethod
        def capture_face_validation():
            return False, None, None

        @staticmethod
        def authorize_dangerous_action(*_args, **_kwargs):
            return True

        @staticmethod
        def _issue_token(action):
            return VegaSecurity._issue_global_token(action)

    brain = VegaBrain.__new__(VegaBrain)
    brain.security = Security()
    brain._pending_dangerous_action = {"kind": "delete_file", "path": str(target), "query": "garfo"}
    response = brain._authorize_pending_action("confirmo, senha 1624", "1624")

    assert response == "Apaguei o arquivo garfo.txt."
    assert not target.exists()


def test_file_search_reports_no_match(tmp_path):
    assert FileSecurity.find_files("nao-existe.xyz", roots=[Path(tmp_path)]) == []


def test_stream_announces_file_search_before_scanning(monkeypatch, tmp_path, file_changes_enabled):
    brain = VegaBrain.__new__(VegaBrain)
    brain._pending_dangerous_action = None
    target = tmp_path / "garfo.txt"
    target.write_text("teste", encoding="utf-8")
    monkeypatch.setattr(FileSecurity, "find_files", lambda _query, **_kwargs: [target])

    responses = list(brain.process_stream("vega apaga o arquivo chamado garfo"))

    assert responses[0] == "Vou procurar esse arquivo agora."
    assert "garfo.txt" in responses[1]


def test_recycle_bin_requires_confirmation_and_pin(monkeypatch, file_changes_enabled):
    brain = VegaBrain.__new__(VegaBrain)
    brain._pending_dangerous_action = None
    response = brain._process_command("vega esvaziar a lixeira")
    assert "Lixeira" in response
    assert brain._pending_dangerous_action == {"kind": "empty_recycle_bin"}

    class Security:
        @staticmethod
        def capture_face_validation():
            return False, None, None

        @staticmethod
        def authorize_dangerous_action(*_args, **_kwargs):
            return True

        @staticmethod
        def _issue_token(action):
            return VegaSecurity._issue_global_token(action)

    brain.security = Security()
    monkeypatch.setattr(FileSecurity, "empty_recycle_bin", staticmethod(lambda **_kwargs: True))
    result = brain._process_pending_security_response("confirmo, senha 1624")
    assert result == "A Lixeira foi esvaziada."


def test_esvazia_lixeira_is_never_sent_to_ai(file_changes_enabled):
    brain = VegaBrain.__new__(VegaBrain)
    brain._pending_dangerous_action = None
    response = brain._process_command("esvazia a lixeira")
    assert response == "Você quer esvaziar a Lixeira? Confirme e informe o PIN para continuar."
    assert brain._pending_dangerous_action == {"kind": "empty_recycle_bin"}


def test_voice_command_typos_are_corrected_centrally():
    assert normalize_text("apgue o arquivo garfo") == "apague o arquivo garfo"
    assert normalize_text("abri o chrome") == "abrir o chrome"
    assert normalize_text("fechi o spotify") == "feche o spotify"


def test_face_camera_sequence_requires_majority(monkeypatch, tmp_path):
    from core.security.face_security import LocalFaceValidator

    class Camera:
        def isOpened(self):
            return True

        def read(self):
            return True, object()

        def release(self):
            return None

    monkeypatch.setitem(__import__("sys").modules, "cv2", SimpleNamespace(CAP_DSHOW=0, VideoCapture=lambda *_args: Camera()))
    validator = LocalFaceValidator(tmp_path / "missing.json")
    validator._reference = [0.0]
    monkeypatch.setattr(validator, "_distance", lambda frame: 0.2 if frame is not None else None)
    supported, valid, frame = validator.validate_camera_sequence(0, samples=5)
    assert supported and valid and frame is not None

    monkeypatch.setattr(validator, "_distance", lambda _frame: 0.8)
    supported, valid, _frame = validator.validate_camera_sequence(0, samples=1)
    assert supported and not valid
