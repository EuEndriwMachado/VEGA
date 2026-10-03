import pytest
from unittest.mock import patch

from config import file_access
from core.security.file_security import FileSecurity
from tools.productivity import ProductivityTools


def test_personal_file_changes_start_disabled():
    assert file_access.ALLOW_PERSONAL_FILE_CHANGES is False


def test_creation_is_allowed_but_overwriting_is_blocked(tmp_path):
    tools = ProductivityTools(root=tmp_path)

    created = tools.write_text("novo.txt", "conteúdo inicial")

    assert created["status"] == "written"
    assert (tmp_path / "novo.txt").read_text(encoding="utf-8") == "conteúdo inicial"

    with pytest.raises(PermissionError, match="edição de arquivos pessoais"):
        tools.write_text("novo.txt", "conteúdo alterado")

    duplicate = tools.create_document("novo.txt", "Outro conteúdo")
    assert duplicate["path"].endswith("novo_2.txt")


def test_deletion_and_recycle_bin_are_blocked_by_default(tmp_path):
    target = tmp_path / "pessoal.txt"
    target.write_text("não apagar", encoding="utf-8")

    with pytest.raises(PermissionError, match="exclusão de arquivos pessoais"):
        FileSecurity.delete_file(target)

    with pytest.raises(PermissionError, match="exclusão de arquivos pessoais"):
        FileSecurity.empty_recycle_bin()


def test_create_project_writes_files_without_overwriting_and_opens_vscode(tmp_path):
    tools = ProductivityTools(root=tmp_path)
    files = {"index.html": "<!doctype html>\n" + "<p>VEGA</p>\n" * 12, "src/main.js": "console.log('ok');"}
    visible_content = []

    def capture_editor_update(_delay):
        source = tmp_path / "meu_site" / "index.html"
        if source.exists():
            visible_content.append(source.read_text(encoding="utf-8"))

    with patch.object(ProductivityTools, "_vscode_executable", return_value="code"), patch("tools.productivity.subprocess.Popen") as popen, patch("tools.productivity.time.sleep", side_effect=capture_editor_update):
        popen.return_value.pid = 42
        result = tools.create_project("meu_site", files)

    project = tmp_path / "meu_site"
    assert result["status"] == "created"
    assert result["count"] == 2
    assert (project / "index.html").read_text(encoding="utf-8") == files["index.html"]
    assert (project / "src/main.js").read_text(encoding="utf-8") == files["src/main.js"]
    assert len(set(visible_content)) > 1
    assert visible_content[-1] == files["index.html"]
    commands = [call.args[0] for call in popen.call_args_list]
    assert commands == [
        ["code", "--new-window", str(project / "index.html")],
        ["code", "--reuse-window", str(project / "src/main.js")],
        ["code", "--reuse-window", str(project / "index.html")],
    ]


def test_create_project_uses_new_directory_when_project_already_exists(tmp_path):
    tools = ProductivityTools(root=tmp_path)
    existing = tmp_path / "meu_site"
    existing.mkdir()
    (existing / "keep.txt").write_text("preservado", encoding="utf-8")

    result = tools.create_project("meu_site", {"index.html": "novo"}, open_in_editor=False)

    assert result["path"] == str(tmp_path / "meu_site_2")
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "preservado"
    assert (tmp_path / "meu_site_2" / "index.html").read_text(encoding="utf-8") == "novo"


@pytest.mark.parametrize("filename", ["../fora.txt", r"..\fora.txt", r"C:\fora.txt", "/fora.txt"])
def test_create_project_rejects_paths_outside_project(tmp_path, filename):
    tools = ProductivityTools(root=tmp_path)

    with pytest.raises(ValueError, match="Caminho"):
        tools.create_project("meu_site", {filename: "inseguro"}, open_in_editor=False)

    assert not (tmp_path / "meu_site").exists()
