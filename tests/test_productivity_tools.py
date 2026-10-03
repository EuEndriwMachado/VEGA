from tools.productivity import ProductivityTools


def test_read_text_keeps_plain_text_behavior(tmp_path):
    (tmp_path / "notes.txt").write_text("Minha anotação", encoding="utf-8")
    result = ProductivityTools(tmp_path).summarize_document("notes.txt")

    assert result["preview"] == "Minha anotação"
    assert "pages" not in result


def test_summarize_document_extracts_pdf_pages(monkeypatch, tmp_path):
    pdf_path = tmp_path / "atividade.pdf"
    pdf_path.write_bytes(b"fake pdf payload")

    class FakePage:
        @staticmethod
        def extract_text():
            return "Equações do segundo grau"

    class FakePdfReader:
        pages = [FakePage(), FakePage()]

        def __init__(self, _path):
            pass

    monkeypatch.setattr("tools.productivity.PdfReader", FakePdfReader)
    result = ProductivityTools(tmp_path).summarize_document("atividade.pdf")

    assert result["pages"] == 2
    assert "[Página 1]" in result["preview"]
    assert "Equações do segundo grau" in result["preview"]