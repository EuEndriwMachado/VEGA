from core.actions.homework import VegaHomework


class DummyAI:
    pass


def test_find_pdf_by_keyword_handles_filler_phrases_and_punctuation(tmp_path):
    pdf = tmp_path / "lista_de_vetores.pdf"
    pdf.write_bytes(b"fake pdf")

    result = VegaHomework.find_pdf_by_keyword("é a lista de vetores?", [tmp_path])

    assert result == pdf


def test_positive_confirmation_accepts_essa_mesma_variants():
    assert VegaHomework._is_positive_confirmation("isso essa mesmo")
    assert VegaHomework._is_positive_confirmation("essa mesma")
    assert VegaHomework._is_positive_confirmation("sim essa")
    assert not VegaHomework._is_positive_confirmation("não é essa")


def test_detect_language_prefers_javascript_over_java_when_js_is_in_title():
    sample = "Linguagem JavaScript (Parte I)\nCOMANDOS DE REPETIÇÃO\nfor (let i = 0; i < 10; i++) { console.log(i); }"

    assert VegaHomework.detect_language(sample, default="java") == "javascript"


def test_solve_homework_uses_pdf_header_to_detect_javascript(monkeypatch, tmp_path):
    pdf = tmp_path / "lista_sem_titulo.pdf"
    pdf.write_bytes(b"fake pdf")

    class FakeAI:
        def chat(self, messages, system_prompt=None, num_predict=350):
            return "```javascript\nconsole.log('ok');\n```"

    monkeypatch.setattr(VegaHomework, "extract_exercises", lambda path: [{"number": 1, "text": "for (let i = 0; i < 3; i++) { console.log(i); }"}])
    monkeypatch.setattr(VegaHomework, "_read_pdf_text", lambda path: "Linguagem JavaScript (Parte I)\nCOMANDOS DE REPETIÇÃO")
    monkeypatch.setattr(VegaHomework, "_clean_generated_code", lambda raw, num, lang: "console.log('ok');")

    result = VegaHomework._solve_homework_sync(ai=FakeAI(), pdf_file=pdf, language=None)

    assert "javascript" in str(result).lower()


def test_javascript_solutions_are_written_as_internal_html(monkeypatch):
    sample = "let x = prompt('Digite um numero: ');\nlet y = prompt('Digite o segundo numero: ');\nif (x > y) { alert('O maior numero é: ' + x); }"

    wrapped = VegaHomework._clean_generated_code(sample, 2, "javascript")

    lowered = wrapped.lower()
    assert "<!doctype html>" in lowered
    assert "<script>" in lowered
    assert "prompt(\"digite um numero" in lowered or "prompt('digite um numero" in lowered


def test_javascript_cleaning_replaces_var_and_removes_comments():
    sample = "```javascript\nvar idade = 18; // comentario\nfor (var i = 0; i < 3; i++) {\n  alert('oi');\n}\n```"

    cleaned = VegaHomework._clean_generated_code(sample, 5, "javascript")
    lowered = cleaned.lower()

    assert "var " not in lowered
    assert "let " in lowered
    assert "//" not in lowered
    assert "comentario" not in lowered


def test_solve_homework_starts_background_thread(monkeypatch, tmp_path):
    pdf = tmp_path / "lista_de_vetores.pdf"
    pdf.write_bytes(b"fake pdf")
    created = {}

    class DummyThread:
        def __init__(self, target=None, args=None, kwargs=None, daemon=None):
            created["target"] = target
            created["args"] = args
            created["kwargs"] = kwargs
            created["daemon"] = daemon

        def start(self):
            created["started"] = True

    monkeypatch.setattr("threading.Thread", DummyThread)

    result = VegaHomework.solve_homework(ai=DummyAI(), pdf_file=pdf, language="java")

    assert "segundo plano" in result.lower()
    assert created.get("started") is True


def test_solve_homework_reuses_existing_list_directory(monkeypatch, tmp_path):
    pdf = tmp_path / "lista_de_vetores.pdf"
    pdf.write_bytes(b"fake pdf")

    class FakeAI:
        def chat(self, messages, system_prompt=None, num_predict=350):
            return "```javascript\nlet x = 1;\n```"

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(VegaHomework, "extract_exercises", lambda path: [{"number": 1, "text": "descreva um valor"}])
    monkeypatch.setattr(VegaHomework, "_read_pdf_text", lambda path: "Linguagem JavaScript")

    docs_dir = tmp_path / "Documents"
    docs_dir.mkdir()
    existing = docs_dir / "lista_javascript_dia1809"
    existing.mkdir()
    (existing / "exer01.html").write_text("<h1>antigo</h1>", encoding="utf-8")

    result = VegaHomework._solve_homework_sync(ai=FakeAI(), pdf_file=pdf, language="javascript")

    assert "concluído" in str(result).lower()
    assert len(list(docs_dir.glob("lista_javascript_dia1809*"))) == 1


def test_solve_homework_does_not_create_numbered_duplicates_for_same_day(monkeypatch, tmp_path):
    pdf = tmp_path / "lista_de_vetores.pdf"
    pdf.write_bytes(b"fake pdf")

    class FakeAI:
        def chat(self, messages, system_prompt=None, num_predict=350):
            return "```javascript\nlet x = 1;\n```"

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(VegaHomework, "extract_exercises", lambda path: [{"number": 1, "text": "descreva um valor"}])
    monkeypatch.setattr(VegaHomework, "_read_pdf_text", lambda path: "Linguagem JavaScript")

    docs_dir = tmp_path / "Documents"
    docs_dir.mkdir()
    for index in range(6):
        duplicate = docs_dir / f"lista_javascript_dia1809_{index}"
        duplicate.mkdir()
        (duplicate / "exer01.html").write_text(f"<h1>duplicado {index}</h1>", encoding="utf-8")

    result = VegaHomework._solve_homework_sync(ai=FakeAI(), pdf_file=pdf, language="javascript")

    assert "concluído" in str(result).lower()
    matches = sorted(str(path.name) for path in docs_dir.glob("lista_javascript_dia1809*"))
    assert matches == ["lista_javascript_dia1809"]


def test_extract_exercises_keeps_all_numbered_items_from_pdf_text():
    sample = "\n".join(
        [
            "Lista de exercícios Java",
            "Exercício 1: leia um número.",
            "Exercício 2: calcule a soma.",
            "Exercício 3: verifique se é par.",
            "Exercício 4: leia duas notas.",
            "Exercício 5: calcule a média.",
            "Exercício 6: verifique maioridade.",
            "Exercício 7: some os valores do vetor.",
            "Exercício 8: mostre os pares.",
            "Exercício 9: inverta a sequência.",
            "Exercício 10: procure o maior valor.",
            "Exercício 11: conte vogais.",
            "Exercício 12: multiplique matrizes.",
        ]
    )

    exercises = VegaHomework.extract_exercises_from_text(sample)

    assert len(exercises) == 12
    assert exercises[0]["number"] == 1
    assert exercises[-1]["number"] == 12
    assert all(item["text"] for item in exercises)

