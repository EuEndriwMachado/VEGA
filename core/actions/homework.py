import os
import re
import shutil
import threading
import datetime
import unicodedata
from pathlib import Path
import pypdf


class VegaHomework:
    """
    Gerenciador de resolução automática e interativa de listas de exercícios
    (PDF -> código na linguagem escolhida ou inferida).
    """

    pending_state = None  # None, "waiting_confirmation", "waiting_keyword"
    pending_pdf = None
    pending_language = None
    background_tasks = {}
    status_callback = None

    LANGUAGE_PROFILES = {
        "java": {
            "extension": "java",
            "label": "Java",
            "instruction": "Escreva Java iniciante com main, Scanner, if/else, laços e arrays simples. Use classe pública com nome exerNN.",
            "format": "java",
        },
        "python": {
            "extension": "py",
            "label": "Python",
            "instruction": "Escreva Python simples e didático, usando entrada, variáveis, condicionais, laços, listas e funções quando necessário.",
            "format": "python",
        },
        "javascript": {
            "extension": "html",
            "label": "JavaScript (HTML interno)",
            "instruction": "Escreva um arquivo HTML5 simples e didático, com JavaScript interno dentro de <script> no head ou body. Use prompt(), alert(), if/else e variáveis sem bibliotecas externas; o código deve ser de iniciante. Use sempre let em vez de var e não adicione comentários no código.",
            "format": "html",
        },
        "html": {
            "extension": "html",
            "label": "HTML",
            "instruction": "Escreva um documento HTML5 completo, sem bibliotecas externas. Inclua CSS interno apenas quando for necessário para a atividade.",
            "format": "html",
        },
        "css": {
            "extension": "css",
            "label": "CSS",
            "instruction": "Escreva CSS organizado e compatível com HTML5. Inclua apenas estilos necessários e comentários curtos quando ajudarem.",
            "format": "css",
        },
        "mysql": {
            "extension": "sql",
            "label": "MySQL",
            "instruction": "Escreva SQL compatível com MySQL, incluindo CREATE, INSERT, SELECT, UPDATE ou JOIN quando o exercício pedir. Use comentários SQL curtos.",
            "format": "sql",
        },
    }

    @classmethod
    def reset_state(cls):
        cls.pending_state = None
        cls.pending_pdf = None
        cls.pending_language = None

    @classmethod
    def has_pending_action(cls):
        return cls.pending_state is not None

    @staticmethod
    def _normalize_str(text):
        if not text:
            return ""
        t = unicodedata.normalize("NFD", str(text).lower().strip())
        return "".join(c for c in t if unicodedata.category(c) != "Mn")

    @staticmethod
    def _is_positive_confirmation(text):
        if not text:
            return False

        norm = VegaHomework._normalize_str(text)
        if not norm:
            return False

        negative_markers = (
            "nao",
            "não",
            "outra",
            "errada",
            "incorreta",
            "negativo",
            "nao e essa",
            "nao eh essa",
            "nao e essa",
        )
        if any(marker in norm for marker in negative_markers):
            return False

        positive_phrases = {
            "sim",
            "isso",
            "essa",
            "sim essa",
            "isso essa",
            "isso essa mesmo",
            "essa mesma",
            "essa mesmo",
            "sim isso",
            "pode fazer",
            "faz essa",
            "faz",
            "pode ser",
            "claro",
            "com certeza",
            "bora",
            "manda ver",
            "positivo",
            "e essa",
            "é essa",
            "e essa mesma",
            "isso mesmo",
            "mesmo",
            "ok",
        }

        if norm in positive_phrases:
            return True

        if any(phrase in norm for phrase in ("isso mesmo", "essa mesma", "essa mesmo", "sim essa", "isso essa", "é essa", "e essa")):
            return True

        if re.search(r"\b(?:sim|isso|essa|mesmo)\b", norm):
            return True

        return False

    @classmethod
    def normalize_language(cls, language):
        if not language:
            return None
        value = cls._normalize_str(language)
        aliases = {
            "js": "javascript",
            "java script": "javascript",
            "py": "python",
            "sql": "mysql",
            "html5": "html",
        }
        value = aliases.get(value, value)
        return value if value in cls.LANGUAGE_PROFILES else None

    @classmethod
    def detect_language(cls, text, default="java"):
        normalized = cls._normalize_str(text)
        if not normalized:
            return cls.normalize_language(default) or "java"

        def score_for(marker_set):
            score = 0
            for marker in marker_set:
                pattern = rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])"
                if re.search(pattern, normalized):
                    score += 3
                elif marker in normalized:
                    score += 1
            return score

        checks = (
            ("javascript", ("javascript", "java script", "node js", "nodejs", " js ", "console.log", "document.write", "let ", "const ", "prompt(", "alert(")),
            ("python", ("python", " py ", "pandas", "print(", "input(")),
            ("mysql", ("mysql", "sql", "banco de dados", "select", "insert into", "create table")),
            ("html", ("html", "html5", "pagina web", "tag", "div", "formulario")),
            ("css", ("css", "stylesheet", "folha de estilo", "seletor")),
            ("java", ("java", "scanner", "public static void", "arraylist", "system.out.println")),
        )
        scores = {name: score_for(markers) for name, markers in checks}
        best = max(scores, key=scores.get)
        return best if scores[best] else cls.normalize_language(default) or "java"

    @staticmethod
    def _get_user_dirs():
        home = Path.home()
        return {
            "downloads": home / "Downloads",
            "desktop": home / "Desktop",
            "area de trabalho": home / "Desktop",
            "documentos": home / "Documents",
            "documents": home / "Documents",
        }

    @classmethod
    def find_all_pdfs(cls, search_dirs=None):
        if search_dirs is None:
            user_dirs = cls._get_user_dirs()
            search_dirs = [
                user_dirs["downloads"],
                user_dirs["desktop"],
                user_dirs["documentos"],
            ]

        all_pdfs = []
        for sdir in search_dirs:
            if not sdir.exists():
                continue
            try:
                for item in sdir.glob("*.pdf"):
                    if item.is_file():
                        all_pdfs.append(item)
            except Exception:
                continue
        return all_pdfs

    @classmethod
    def _extract_search_words(cls, keyword):
        if not keyword:
            return []

        normalized = cls._normalize_str(keyword)
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return []

        stop_words = {
            "a", "e", "eh", "é", "de", "da", "do", "dos", "das",
            "para", "por", "com", "como", "me", "mim", "meu", "minha",
            "quer", "quero", "queria", "fale", "fala", "diga", "digame",
            "qual", "nome", "arquivo", "lista", "exercicio", "exercicios",
            "trabalho", "pdf", "essa", "esse", "isso", "esta", "este",
            "o", "os", "as", "na", "no", "em", "nao", "não"
        }
        words = [w for w in normalized.split() if len(w) > 1 and w not in stop_words]
        if not words:
            words = [w for w in normalized.split() if len(w) > 1]
        return words

    @classmethod
    def find_pdf_by_keyword(cls, keyword, search_dirs=None):
        if not keyword:
            return None

        words = cls._extract_search_words(keyword)
        if not words:
            return None

        norm_kw = cls._normalize_str(keyword)
        norm_kw = re.sub(r"[^a-z0-9\s]", " ", norm_kw)
        norm_kw = re.sub(r"\s+", " ", norm_kw).strip()

        all_pdfs = cls.find_all_pdfs(search_dirs)
        if not all_pdfs:
            return None

        scored = []
        for pdf in all_pdfs:
            stem_norm = cls._normalize_str(pdf.stem)
            stem_norm = re.sub(r"[^a-z0-9\s]", " ", stem_norm)
            stem_norm = re.sub(r"\s+", " ", stem_norm).strip()
            score = 0
            if norm_kw and norm_kw in stem_norm:
                score += 50
            for w in words:
                if w in stem_norm:
                    score += 20

            if score > 0:
                try:
                    mtime = pdf.stat().st_mtime
                except Exception:
                    mtime = 0
                scored.append((score, mtime, pdf))

        if not scored:
            return None

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return scored[0][2]

    @classmethod
    def find_pdf(cls, folder_or_path=None):
        user_dirs = cls._get_user_dirs()

        if folder_or_path:
            clean_hint = folder_or_path.strip().strip('\"\'')
            candidate_path = Path(clean_hint)

            if candidate_path.is_file() and candidate_path.suffix.lower() == ".pdf":
                return candidate_path

            normalized_hint = cls._normalize_str(clean_hint)
            if normalized_hint in user_dirs and user_dirs[normalized_hint].is_dir():
                search_dirs = [user_dirs[normalized_hint]]
            elif candidate_path.is_dir():
                search_dirs = [candidate_path]
            else:
                matched_dir = None
                for key, udir in user_dirs.items():
                    if key in normalized_hint and udir.is_dir():
                        matched_dir = udir
                        break
                search_dirs = [matched_dir] if matched_dir else list(user_dirs.values())
        else:
            search_dirs = [
                user_dirs["downloads"],
                user_dirs["desktop"],
                user_dirs["documentos"],
            ]

        all_pdfs = cls.find_all_pdfs(search_dirs)
        if not all_pdfs:
            return None

        keywords = ["lista", "exerc", "trabalho", "java", "jr", "atividade", "tarefa", "vetor", "matriz"]

        def score_pdf(p):
            name = cls._normalize_str(p.name)
            score = 0
            for kw in keywords:
                if kw in name:
                    score += 10
            try:
                mtime = p.stat().st_mtime
            except Exception:
                mtime = 0
            return (score, mtime)

        sorted_pdfs = sorted(all_pdfs, key=score_pdf, reverse=True)
        return sorted_pdfs[0]

    @staticmethod
    def _read_pdf_text(pdf_path):
        try:
            reader = pypdf.PdfReader(str(pdf_path))
        except Exception as e:
            print(f"[VegaHomework] Erro ao abrir PDF {pdf_path}: {e}")
            return ""

        full_text = ""
        for i, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text() or ""
                full_text += f"\n{page_text}\n"
            except Exception as e:
                print(f"[VegaHomework] Erro na página {i+1}: {e}")

        return full_text.strip()

    @classmethod
    def extract_exercises_from_text(cls, full_text):
        if not full_text:
            return []

        cleaned = re.sub(r"Vetores\s*\|\s*JAVA\s*-\s*Lista\s*de\s*Exerc[íi]cios\s*\d+", "", full_text, flags=re.IGNORECASE)
        cleaned = re.sub(r"java\s*do\s*b[áa]sico\s*ao\s*avan[çc]ado\s*\[\d+\]", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"[\uf0b7\u2022\u25cf]", "*", cleaned)
        cleaned = re.sub(r"\r+", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        exercises = []
        current = None

        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            marker = re.match(
                r"^(?:.*?(?:exerc[íi]cio|quest[ãa]o|atividade|atv|item)\s*(?:n[ºo°]\s*)?)?"
                r"0*(\d{1,3})\s*(?:[\.\-\)\:\º\ª]\s*|\s+)"
                r"(.*)$",
                line,
                flags=re.IGNORECASE,
            )

            if marker:
                if current is not None:
                    exercises.append(current)

                number = int(marker.group(1))
                content = marker.group(2).strip()
                current = {"number": number, "text": content}
                continue

            if current is not None:
                current["text"] = f"{current['text']}\n{line}".strip()

        if current is not None:
            exercises.append(current)

        if not exercises:
            paragraphs = [p.strip() for p in cleaned.split("\n\n") if len(p.strip()) > 30]
            for idx, paragraph in enumerate(paragraphs, start=1):
                exercises.append({"number": idx, "text": paragraph})

        return exercises

    @staticmethod
    def extract_exercises(pdf_path):
        full_text = VegaHomework._read_pdf_text(pdf_path)
        if not full_text:
            return []
        return VegaHomework.extract_exercises_from_text(full_text)

    @classmethod
    def _clean_java_code(cls, raw_response, exercise_num):
        """
        Extrai o código Java, ajusta o nome da classe e garante 100% dos imports necessários
        (especialmente java.util.Scanner).
        """
        text = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"^Thinking\.\.\..*?(?:\.\.\.done thinking\.|$)", "", text, flags=re.DOTALL | re.IGNORECASE)

        text = re.sub(r"```\s*(?:java|javascript|js)\s*", "```java\n", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*", "```\n", text)

        code_match = re.search(r"```(?:java)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if code_match:
            code = code_match.group(1).strip()
        else:
            code = text.strip()

        class_target = f"exer{exercise_num:02d}"

        # Ajusta o nome da classe para coincidir com o arquivo
        if not re.search(rf"\bclass\s+{class_target}\b", code, re.IGNORECASE):
            code = re.sub(
                r"\bpublic\s+class\s+\w+",
                f"public class {class_target}",
                code,
                count=1
            )
            if not re.search(rf"\bclass\s+{class_target}\b", code, re.IGNORECASE):
                code = re.sub(
                    r"\bclass\s+\w+",
                    f"public class {class_target}",
                    code,
                    count=1
                )

        # GARANTIA OBRIGATÓRIA DE IMPORTS
        needed_imports = []

        # 1. Scanner (Crítico - sem isso o código não compila)
        if re.search(r"\bScanner\b", code):
            if not re.search(r"import\s+java\.util\.Scanner\s*;", code):
                needed_imports.append("import java.util.Scanner;")

        # 2. Arrays
        if re.search(r"\bArrays\b", code):
            if not re.search(r"import\s+java\.util\.Arrays\s*;", code):
                needed_imports.append("import java.util.Arrays;")

        # 3. Random
        if re.search(r"\bRandom\b", code):
            if not re.search(r"import\s+java\.util\.Random\s*;", code):
                needed_imports.append("import java.util.Random;")

        # 4. ArrayList
        if re.search(r"\bArrayList\b", code):
            if not re.search(r"import\s+java\.util\.ArrayList\s*;", code):
                needed_imports.append("import java.util.ArrayList;")

        if needed_imports:
            code = "\n".join(needed_imports) + "\n\n" + code

        return code

    @classmethod
    def _wrap_javascript_html(cls, code):
        cleaned = (code or "").strip()
        if not cleaned:
            return ""

        lowered = cleaned.lower()
        if "<!doctype html" in lowered or "<html" in lowered:
            return cleaned

        if "<script" not in lowered:
            cleaned = f"<script>\n{cleaned}\n</script>"

        template = (
            "<!DOCTYPE html>\n"
            "<html lang=\"pt-br\">\n"
            "<head>\n"
            "    <meta charset=\"UTF-8\">\n"
            "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
            "    <title>Exercicio</title>\n"
            "</head>\n"
            "<body>\n"
            f"    {cleaned}\n"
            "</body>\n"
            "</html>\n"
        )
        return template

    @classmethod
    def _clean_generated_code(cls, raw_response, exercise_num, language):
        if language == "java":
            return cls._clean_java_code(raw_response, exercise_num)

        text = re.sub(
            r"<think>.*?</think>",
            "",
            str(raw_response or ""),
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()

        if language == "javascript":
            text = re.sub(r"```\s*(?:js|javascript)\s*", "```html\n", text, flags=re.IGNORECASE)
        elif language == "python":
            text = re.sub(r"```\s*(?:py|python)\s*", "```python\n", text, flags=re.IGNORECASE)
        elif language == "java":
            text = re.sub(r"```\s*(?:java)\s*", "```java\n", text, flags=re.IGNORECASE)

        code_match = re.search(r"```(?:[a-zA-Z0-9+#-]+)?\s*(.*?)```", text, re.DOTALL)
        code = code_match.group(1) if code_match else text
        code = code.strip()

        if language == "javascript":
            code = cls._sanitize_javascript_code(code)
            return cls._wrap_javascript_html(code)

        return code

    @classmethod
    def _resolve_output_dir(cls, language, pdf_file=None):
        docs_dir = Path.home() / "Documents"
        docs_dir.mkdir(parents=True, exist_ok=True)

        today_prefix = f"lista_{language}_dia"
        existing_matches = sorted(
            [
                path for path in docs_dir.iterdir()
                if path.name.startswith(today_prefix)
            ],
            key=lambda path: path.name,
        )

        if existing_matches:
            canonical_name = existing_matches[0].name
            if "_" in canonical_name:
                candidate = canonical_name.rsplit("_", 1)[0]
                if candidate.startswith(today_prefix):
                    canonical_name = candidate
            canonical_dir = docs_dir / canonical_name
            duplicate_dirs = [path for path in existing_matches if path.name != canonical_dir.name]
            for duplicate in duplicate_dirs:
                try:
                    if duplicate.is_dir():
                        shutil.rmtree(duplicate)
                    else:
                        duplicate.unlink()
                except Exception:
                    pass
            return canonical_dir

        date_str = datetime.datetime.now().strftime("%d%m")
        base_folder_name = f"lista_{language}_dia{date_str}"
        canonical_dir = docs_dir / base_folder_name
        return canonical_dir

    @classmethod
    def _sanitize_javascript_code(cls, code):
        text = str(code or "")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"(?<!:)//.*", "", text)
        text = re.sub(r"\bvar\b", "let", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @classmethod
    def start_homework_dialog(cls, folder_or_keyword=None, language=None):
        """
        Inicia o diálogo com o usuário confirmando o arquivo encontrado.
        """
        candidate_pdf = None
        if folder_or_keyword:
            candidate_pdf = cls.find_pdf_by_keyword(folder_or_keyword)
            if not candidate_pdf:
                candidate_pdf = cls.find_pdf(folder_or_keyword)

        if not candidate_pdf:
            candidate_pdf = cls.find_pdf()

        if candidate_pdf and candidate_pdf.is_file():
            cls.pending_pdf = candidate_pdf
            cls.pending_language = cls.normalize_language(language)
            cls.pending_state = "waiting_confirmation"
            language_hint = cls.pending_language or "a linguagem identificada no conteúdo"
            return f"Achei um arquivo seu chamado {candidate_pdf.stem}. Vou resolver em {language_hint}; é essa lista que eu tenho que fazer?"
        else:
            cls.pending_language = cls.normalize_language(language)
            cls.pending_state = "waiting_keyword"
            return "Qual lista você quer que eu faça? Me diga uma palavra-chave do nome dela ou em qual pasta ela está."

    @classmethod
    def handle_dialog(cls, ai, user_text):
        """
        Gerencia as respostas do usuário no diálogo de confirmação.
        """
        if not cls.has_pending_action():
            return None

        norm = cls._normalize_str(user_text)

        # Cancelamento
        cancel_words = ("cancela", "cancelar", "esquece", "deixa pra la", "sair", "parar")
        if any(w in norm for w in cancel_words):
            cls.reset_state()
            return "Resolução da lista cancelada."

        if cls.pending_state == "waiting_confirmation":
            # Resposta Negativa
            no_words = ("nao", "outra", "errada", "negativo", "incorreta")
            negative_tokens = set(re.findall(r"[a-záàãâéêíóôúç]+", norm))
            has_negative = bool(negative_tokens.intersection(no_words))
            if has_negative:
                # Se o usuário disse apenas negação (ex: "não", "não é essa", "outra")
                exact_negatives = {
                    "nao",
                    "não",
                    "nao e essa",
                    "não é essa",
                    "outra",
                    "outra lista",
                    "errada",
                    "negativo",
                    "outra por favor",
                }
                if norm in exact_negatives or (
                    norm.startswith("nao ")
                    and any(w in norm for w in ("essa", "quero", "essa nao"))
                ):
                    cls.pending_state = "waiting_keyword"
                    return "Qual lista você quer que eu faça? Me diga uma palavra-chave do nome do PDF."

                # Verifica se o usuário já especificou a nova lista (ex: "não, faz a de vetor")
                clean_kw = re.sub(
                    r"^(?:nao|outra|errada|negativo)[\s\,]+(?:e\s+a\s+de\s+|faz\s+a\s+de\s+|procura\s+por\s+|a\s+de\s+)?",
                    "",
                    norm,
                ).strip()

                if len(clean_kw) > 1 and clean_kw not in no_words:
                    new_pdf = cls.find_pdf_by_keyword(clean_kw)
                    if new_pdf:
                        cls.pending_pdf = new_pdf
                        cls.pending_state = "waiting_confirmation"
                        return f"Achei o arquivo {new_pdf.stem}, é essa lista que eu tenho que fazer?"
                    else:
                        cls.pending_state = "waiting_keyword"
                        return f"Não encontrei nenhum arquivo PDF com '{clean_kw}' no nome. Qual o nome ou palavra-chave da lista que você quer fazer?"

                cls.pending_state = "waiting_keyword"
                return "Qual lista você quer que eu faça? Me diga uma palavra-chave do nome do PDF."

            # Resposta Afirmativa: aceita frases como "isso essa mesmo" e
            # outras variações de confirmação sem confundir com negação.
            if cls._is_positive_confirmation(norm):
                pdf_file = cls.pending_pdf
                language = cls.pending_language
                cls.reset_state()
                return cls.solve_homework(ai, pdf_file=pdf_file, language=language)

            # Se o usuário disse um nome/palavra-chave direto
            new_pdf = cls.find_pdf_by_keyword(user_text)
            if new_pdf:
                cls.pending_pdf = new_pdf
                cls.pending_state = "waiting_confirmation"
                return f"Achei o arquivo {new_pdf.stem}, é essa lista que eu tenho que fazer?"

            cls.pending_state = "waiting_keyword"
            return "Não entendi se era essa lista. Responda 'sim' para fazer essa ou me diga a palavra-chave da lista desejada."

        elif cls.pending_state == "waiting_keyword":
            new_pdf = cls.find_pdf_by_keyword(user_text)
            if not new_pdf:
                new_pdf = cls.find_pdf(user_text)

            if new_pdf:
                cls.pending_pdf = new_pdf
                cls.pending_state = "waiting_confirmation"
                return f"Achei o arquivo {new_pdf.stem}, é essa lista que eu tenho que fazer?"
            else:
                return f"Não encontrei nenhum arquivo PDF com '{user_text}' no nome. Quer tentar outra palavra-chave ou me dizer a pasta onde ele está?"

        return None

    @classmethod
    def _solve_homework_sync(cls, ai, pdf_file=None, folder_or_path=None, language=None):
        if not pdf_file:
            pdf_file = cls.find_pdf(folder_or_path)

        if not pdf_file or not pdf_file.is_file():
            target_info = f"'{folder_or_path}'" if folder_or_path else "Downloads, Desktop ou Documentos"
            return (
                f"Não encontrei nenhum arquivo PDF da lista de exercícios em {target_info}. "
                "Por favor, verifique se o PDF está salvo nessa pasta ou me informe o caminho."
            )

        print(f"\n[VEGA Trabalho] PDF localizado: {pdf_file}")
        exercises = cls.extract_exercises(pdf_file)

        if not exercises:
            return (
                f"Encontrei o arquivo '{pdf_file.name}', mas não consegui extrair o texto ou os exercícios dele. "
                "Pode ser que ele seja uma imagem digitalizada sem texto selecionável."
            )

        language = cls.normalize_language(language)
        if language is None:
            pdf_text = cls._read_pdf_text(pdf_file)
            sample = "\n".join(
                part for part in (
                    pdf_file.stem,
                    pdf_text[:2000],
                    *[item["text"] for item in exercises[:3]],
                ) if part
            )
            language = cls.detect_language(sample)

        profile = cls.LANGUAGE_PROFILES[language]

        total = len(exercises)
        print(f"[VEGA Trabalho] Total de exercícios identificados: {total}")

        output_dir = cls._resolve_output_dir(language, pdf_file)
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[VEGA Trabalho] Pasta de saída: {output_dir}")

        system_prompt = (
            "Você é um estudante iniciante do curso técnico de Desenvolvimento de Sistemas.\n"
            f"Escreva a solução em {profile['label']} para o exercício solicitado.\n"
            f"{profile['instruction']}\n\n"
            "REGRAS OBRIGATÓRIAS:\n"
            "1. Responda APENAS com o código, sem explicações antes ou depois.\n"
            "2. Use um bloco markdown com a linguagem correta.\n"
            "3. Para JavaScript, use HTML5 com JavaScript interno em <script> e não gere arquivo .js puro.\n"
            "4. Para JavaScript, nunca use var; use sempre let.\n"
            "5. Para JavaScript, não escreva comentários no código.\n"
            "6. Não invente requisitos que não estejam no enunciado.\n"
            "7. Não adicione comentários curtos em nenhum código da lista."
        )

        solved_count = 0

        for item in exercises:
            num = item["number"]
            ex_text = item["text"]
            file_name = f"exer{num:02d}.{profile['extension']}"
            file_path = output_dir / file_name

            if cls.status_callback and (num == 10 or num == 20 or num == total):
                cls.status_callback(f"Estou no exercício {num} de {total}. Já vou terminar a lista.")
            print(f"[VEGA Trabalho] ({num}/{total}) Resolvendo exercício {num}...")

            user_prompt = (
                f"Exercício {num}:\n{ex_text}\n\n"
                f"Arquivo de saída: exer{num:02d}.{profile['extension']}\n"
                f"Escreva a solução completa em {profile['label']}."
            )

            try:
                raw_answer = ai.chat(
                    [{"role": "user", "content": user_prompt}],
                    system_prompt=system_prompt,
                    num_predict=350
                )
                clean_code = cls._clean_generated_code(
                    raw_answer,
                    num,
                    language,
                )

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(clean_code)

                solved_count += 1
                if cls.status_callback and (num == 10 or num == 20 or num == total):
                    cls.status_callback(f"Exercício {num} resolvido. Ainda faltam {max(0, total - num)}.")
                print(f"[VEGA Trabalho] [OK] Salvo em: {file_path}")

            except Exception as err:
                print(f"[VEGA Trabalho] [ERRO] Falha no exercício {num}: {err}")

        folder_label = "na pasta de documentos"
        lowered = str(output_dir).lower()
        if "downloads" in lowered:
            folder_label = "na pasta de downloads"
        elif "desktop" in lowered or "area de trabalho" in lowered:
            folder_label = "na área de trabalho"
        elif "documents" in lowered or "documentos" in lowered:
            folder_label = "na pasta de documentos"

        return (
            f"Trabalho concluído com sucesso! Encontrei a lista '{pdf_file.name}' e resolvi {solved_count} de {total} exercícios em {profile['label']}.\n"
            f"Criei os arquivos {folder_label}."
        )

    @classmethod
    def solve_homework(cls, ai, pdf_file=None, folder_or_path=None, language=None):
        if not pdf_file:
            pdf_file = cls.find_pdf(folder_or_path)

        if not pdf_file or not pdf_file.is_file():
            target_info = f"'{folder_or_path}'" if folder_or_path else "Downloads, Desktop ou Documentos"
            return (
                f"Não encontrei nenhum arquivo PDF da lista de exercícios em {target_info}. "
                "Por favor, verifique se o PDF está salvo nessa pasta ou me informe o caminho."
            )

        task_key = str(pdf_file.resolve())
        active = cls.background_tasks.get(task_key)
        if active is not None and active.is_alive():
            return (
                f"Já estou resolvendo a lista '{pdf_file.name}' em segundo plano. "
                "Você pode continuar usando a Vega normalmente."
            )

        def runner():
            try:
                result = cls._solve_homework_sync(ai, pdf_file=pdf_file, language=language)
                print(f"[VEGA Trabalho] Finalizado: {result}")
            except Exception as exc:
                print(f"[VEGA Trabalho] Erro na resolução em segundo plano: {exc}")
            finally:
                cls.background_tasks.pop(task_key, None)

        thread = threading.Thread(target=runner, daemon=True)
        cls.background_tasks[task_key] = thread
        thread.start()

        return (
            f"Vou resolver a lista '{pdf_file.name}' em segundo plano. "
            "Eu continuo funcionando normalmente enquanto isso."
        )
