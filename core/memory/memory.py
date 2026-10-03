import json
import os
import re
import unicodedata
import difflib
from datetime import datetime


class VegaMemory:
    """
    Sistema de memória da VEGA.

    Possui três níveis:

    1. Memória diária:
       registra acontecimentos e conversas do dia.

    2. Memória permanente:
       guarda informações importantes sobre o usuário.

    3. Memória seletiva:
       representa informações candidatas a serem promovidas
       para a memória permanente.
    """

    def __init__(self, base_path="data/memory"):
        self.base_path = base_path

        self.daily_path = os.path.join(
            self.base_path,
            "daily"
        )

        self.permanent_file = os.path.join(
            self.base_path,
            "permanent.json"
        )

        self.selective_file = os.path.join(
            self.base_path,
            "selective.json"
        )

        self.episodic_file = os.path.join(
            self.base_path,
            "episodic.json"
        )

        self.semantic_file = os.path.join(
            self.base_path,
            "semantic.json"
        )

        self.preference_file = os.path.join(
            self.base_path,
            "preferences.json"
        )

        self.procedural_file = os.path.join(
            self.base_path,
            "procedural.json"
        )

        self.error_file = os.path.join(
            self.base_path,
            "errors.json"
        )

        self._ensure_files()

    # ==========================================================
    # ESTRUTURA
    # ==========================================================

    def _ensure_files(self):
        os.makedirs(
            self.daily_path,
            exist_ok=True
        )

        for path in (
            self.permanent_file,
            self.selective_file,
            self.episodic_file,
            self.semantic_file,
            self.preference_file,
            self.procedural_file,
            self.error_file,
        ):
            if not os.path.exists(path):
                self._save_json(
                    path,
                    [] if path.endswith(".json") and "permanent" not in path else {}
                )

        if not os.path.exists(self.selective_file):
            self._save_json(self.selective_file, [])

        for path, default in [
            (self.episodic_file, []),
            (self.semantic_file, []),
            (self.preference_file, []),
            (self.procedural_file, []),
            (self.error_file, []),
        ]:
            if not os.path.exists(path):
                self._save_json(path, default)

    # ==========================================================
    # UTILIDADES
    # ==========================================================

    def _today(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _daily_file(self, date=None):
        if date is None:
            date = self._today()

        return os.path.join(
            self.daily_path,
            f"{date}.json"
        )

    def _normalize_key(self, key):
        """
        Normaliza uma chave de memória.

        Evita que diferenças de:
        - maiúsculas/minúsculas
        - espaços duplicados
        - espaços no começo/fim

        criem memórias diferentes para a mesma informação.
        """

        key = str(key).strip().lower()

        key = re.sub(
            r"\s+",
            " ",
            key
        )

        return key

    def _normalize_text(self, text):
        """
        Normaliza texto para comparações internas.
        """

        text = str(text).strip().lower()

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        return text

    def _load_json(self, path, default):
        try:
            with open(
                path,
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

                return data

        except (
            FileNotFoundError,
            json.JSONDecodeError,
            OSError
        ):
            return default

    def _save_json(self, path, data):
        folder = os.path.dirname(path)

        if folder:
            os.makedirs(
                folder,
                exist_ok=True
            )

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=4
            )

    # ==========================================================
    # MEMÓRIA PERMANENTE
    # ==========================================================

    def remember(
        self,
        key,
        value,
        category="geral",
        importance=1.0,
        source="explicit"
    ):
        """
        Salva ou atualiza uma memória permanente.

        A chave é normalizada para evitar duplicações
        causadas por diferenças de capitalização ou espaços.
        """

        key = self._normalize_key(key)
        value = str(value).strip()

        if not key or not value:
            return False

        data = self._load_json(
            self.permanent_file,
            {}
        )

        if not isinstance(data, dict):
            data = {}

        # ------------------------------------------------------
        # Remove possíveis chaves antigas equivalentes
        # ------------------------------------------------------

        equivalent_keys = []

        for existing_key in data.keys():
            if (
                self._normalize_key(existing_key)
                == key
                and existing_key != key
            ):
                equivalent_keys.append(existing_key)

        for existing_key in equivalent_keys:
            del data[existing_key]

        # ------------------------------------------------------
        # Salva/atualiza a memória
        # ------------------------------------------------------

        data[key] = {
            "value": value,
            "category": category,
            "importance": float(importance),
            "source": source,
            "updated_at": datetime.now().isoformat(
                timespec="seconds"
            )
        }

        self._save_json(
            self.permanent_file,
            data
        )

        return True

    def recall(self, key):
        """
        Recupera uma memória permanente específica.
        """

        key = self._normalize_key(key)

        data = self._load_json(
            self.permanent_file,
            {}
        )

        if not isinstance(data, dict):
            return None

        memory = data.get(key)

        if isinstance(memory, dict):
            return memory.get("value")

        # Compatibilidade com o formato antigo
        if memory is not None:
            return memory

        # Procura também por chaves antigas equivalentes
        for existing_key, existing_memory in data.items():

            if (
                self._normalize_key(existing_key)
                == key
            ):

                if isinstance(existing_memory, dict):
                    return existing_memory.get(
                        "value"
                    )

                return existing_memory

        return None

    def get_permanent(self):
        """
        Retorna todas as memórias permanentes.
        """

        data = self._load_json(
            self.permanent_file,
            {}
        )

        if not isinstance(data, dict):
            return {}

        return data

    def forget(self, key):
        """
        Remove uma memória permanente.

        Também reconhece chaves antigas equivalentes.
        """

        key = self._normalize_key(key)

        data = self._load_json(
            self.permanent_file,
            {}
        )

        if not isinstance(data, dict):
            return False

        found_key = None

        for existing_key in data.keys():

            if (
                self._normalize_key(existing_key)
                == key
            ):
                found_key = existing_key
                break

        if found_key is None:
            return False

        del data[found_key]

        self._save_json(
            self.permanent_file,
            data
        )

        return True

    # ==========================================================
    # MEMÓRIA DIÁRIA
    # ==========================================================

    def add_daily_message(
        self,
        role,
        content
    ):
        """
        Registra uma mensagem na memória diária.
        """

        content = str(content).strip()

        if not content:
            return False

        path = self._daily_file()

        data = self._load_json(
            path,
            []
        )

        if not isinstance(data, list):
            data = []

        data.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(
                timespec="seconds"
            )
        })

        self._save_json(
            path,
            data
        )
        return True

    def add_report_event(self, report):
        """Store a generated report in the existing Events stream."""
        if not isinstance(report, dict):
            return False
        title = str(report.get("title", "Relatório VEGA")).strip()
        content = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        return self.add_daily_message("report", f"{title}: {content}")

    def get_daily(self, date=None):
        """
        Retorna as mensagens registradas em determinado dia.
        """

        data = self._load_json(
            self._daily_file(date),
            []
        )

        if not isinstance(data, list):
            return []

        return data

    # ==========================================================
    # MEMÓRIA SELETIVA
    # ==========================================================

    def add_selective(
        self,
        content,
        category="geral",
        importance=0.5
    ):
        """
        Adiciona uma memória como candidata à promoção.
        """

        content = str(content).strip()

        if not content:
            return False

        data = self._load_json(
            self.selective_file,
            []
        )

        if not isinstance(data, list):
            data = []

        normalized_content = self._normalize_text(
            content
        )

        # Evita duplicações exatas
        for item in data:

            if not isinstance(item, dict):
                continue

            existing_content = self._normalize_text(
                item.get(
                    "content",
                    ""
                )
            )

            if existing_content == normalized_content:
                return False

        data.append({
            "content": content,
            "category": category,
            "importance": float(importance),
            "created_at": datetime.now().isoformat(
                timespec="seconds"
            )
        })

        self._save_json(
            self.selective_file,
            data
        )

        return True

    def get_selective(self):
        return self._load_json(
            self.selective_file,
            []
        )

    def record_episode(
        self,
        content,
        context=None,
        objective=None,
        actions=None,
        result=None,
        success=True,
        feedback=None,
        importance=0.8,
        confidence=0.7,
    ):
        if not content:
            return None

        episode = {
            "id": f"ep_{int(datetime.now().timestamp() * 1000)}",
            "content": str(content).strip(),
            "context": context or {},
            "objective": objective or "",
            "actions": actions or [],
            "result": result or "",
            "success": bool(success),
            "feedback": feedback or "",
            "importance": float(importance),
            "confidence": float(confidence),
            "relevance": 0.0,
            "source": "experience",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "last_used": datetime.now().isoformat(timespec="seconds"),
            "type": "episodic",
        }

        episodes = self._load_json(self.episodic_file, [])
        if not isinstance(episodes, list):
            episodes = []
        episodes.append(episode)
        self._save_json(self.episodic_file, episodes)
        return episode

    def record_semantic(self, concept, summary, importance=0.8, confidence=0.8):
        if not concept or not summary:
            return None

        sem = {
            "id": f"sem_{int(datetime.now().timestamp() * 1000)}",
            "concept": str(concept).strip(),
            "summary": str(summary).strip(),
            "importance": float(importance),
            "confidence": float(confidence),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "type": "semantic",
        }

        data = self._load_json(self.semantic_file, [])
        if not isinstance(data, list):
            data = []
        normalized_concept = self._normalize_text(concept)
        for existing in data:
            if not isinstance(existing, dict):
                continue
            similarity = difflib.SequenceMatcher(
                None,
                normalized_concept,
                self._normalize_text(existing.get("concept", "")),
            ).ratio()
            if similarity >= 0.88:
                existing.update({
                    "summary": str(summary).strip(),
                    "importance": float(importance),
                    "confidence": float(confidence),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                })
                self._save_json(self.semantic_file, data)
                return existing
        data.append(sem)
        self._save_json(self.semantic_file, data)
        return sem

    def find_duplicate(self, content, collection=None, threshold=0.88):
        """Return the closest memory item when its normalized text is similar."""
        query = self._normalize_text(content)
        if not query:
            return None
        items = collection if collection is not None else self.retrieve_relevant(query, limit=100)
        best = None
        best_score = 0.0
        for entry in items:
            item = entry.get("item", entry) if isinstance(entry, dict) else entry
            if not isinstance(item, dict):
                continue
            text = " ".join(str(item.get(key, "")) for key in ("content", "summary", "concept", "value", "task"))
            score = difflib.SequenceMatcher(None, query, self._normalize_text(text)).ratio()
            if score > best_score:
                best_score = score
                best = item
        return best if best_score >= threshold else None

    def record_preference(self, key, value, category="behavior", importance=0.7, confidence=0.8):
        if not key or not value:
            return None

        data = self._load_json(self.preference_file, [])
        if not isinstance(data, list):
            data = []

        normalized = self._normalize_key(key)
        for item in data:
            if isinstance(item, dict) and self._normalize_key(item.get("key", "")) == normalized:
                item.update({
                    "value": str(value),
                    "category": category,
                    "importance": float(importance),
                    "confidence": float(confidence),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                })
                self._save_json(self.preference_file, data)
                return item

        item = {
            "key": str(key).strip(),
            "value": str(value).strip(),
            "category": category,
            "importance": float(importance),
            "confidence": float(confidence),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "type": "preference",
        }
        data.append(item)
        self._save_json(self.preference_file, data)
        return item

    def record_procedure(self, task, steps, importance=0.8, confidence=0.8):
        if not task or not steps:
            return None

        proc = {
            "task": str(task).strip(),
            "steps": [str(step).strip() for step in steps if str(step).strip()],
            "importance": float(importance),
            "confidence": float(confidence),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "last_used": datetime.now().isoformat(timespec="seconds"),
            "type": "procedural",
        }

        data = self._load_json(self.procedural_file, [])
        if not isinstance(data, list):
            data = []
        data.append(proc)
        self._save_json(self.procedural_file, data)
        return proc

    def record_error(self, task, failure, strategy, result=None, importance=0.9, confidence=0.7):
        if not task or not failure:
            return None

        error_entry = {
            "task": str(task).strip(),
            "failure": str(failure).strip(),
            "strategy": str(strategy).strip(),
            "result": str(result or "").strip(),
            "importance": float(importance),
            "confidence": float(confidence),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "type": "error",
        }

        data = self._load_json(self.error_file, [])
        if not isinstance(data, list):
            data = []
        data.append(error_entry)
        self._save_json(self.error_file, data)
        return error_entry

    def _score_memory_item(self, item, query):
        text = ""
        if isinstance(item, dict):
            for key in ("content", "summary", "concept", "value", "task", "key", "failure", "strategy"):
                text += " " + str(item.get(key, ""))
            category = str(item.get("category", ""))
            importance = float(item.get("importance", 0.5))
            confidence = float(item.get("confidence", 0.5))
            relevance = float(item.get("relevance", 0.5))
        else:
            text = str(item)
            category = "geral"
            importance = 0.5
            confidence = 0.5
            relevance = 0.5

        q = self._normalize_text(str(query or ""))
        if q:
            words = {
                w for w in q.split() if len(w) >= 3
            }
            score = sum(1 for w in words if w in self._normalize_text(text))
        else:
            score = 0

        return {
            "score": score,
            "importance": importance,
            "confidence": confidence,
            "relevance": relevance,
            "category": category,
            "text": text,
        }

    def retrieve_relevant(self, query, limit=6):
        items = []
        for store_name in ("episodic", "semantic", "preference", "procedural", "error"):
            file_path = {
                "episodic": self.episodic_file,
                "semantic": self.semantic_file,
                "preference": self.preference_file,
                "procedural": self.procedural_file,
                "error": self.error_file,
            }[store_name]

            payload = self._load_json(file_path, [])
            if isinstance(payload, list):
                items.extend(payload)

        ranked = []
        for item in items:
            if not isinstance(item, dict):
                continue
            scored = self._score_memory_item(item, query)
            final = (scored["score"] * 2.5) + (scored["importance"] * 1.5) + (scored["confidence"] * 1.25)
            ranked.append({
                "item": item,
                "score": final,
                "summary": scored["text"],
            })

        ranked.sort(key=lambda entry: entry["score"], reverse=True)
        return ranked[:limit]

    def promote_selective(self, index):
        """
        Promove uma memória seletiva para permanente.
        """

        data = self.get_selective()

        if index < 0 or index >= len(data):
            return False

        item = data[index]

        if not isinstance(item, dict):
            return False

        content = item.get("content")

        if not content:
            return False

        key = self._generate_key(
            content,
            item.get("category", "geral")
        )

        self.remember(
            key,
            content,
            category=item.get(
                "category",
                "geral"
            ),
            importance=item.get(
                "importance",
                0.5
            ),
            source="selective"
        )

        data.pop(index)

        self._save_json(
            self.selective_file,
            data
        )

        return True

    # ==========================================================
    # RECUPERAÇÃO DE MEMÓRIA
    # ==========================================================

    def search(self, query, limit=5):
        """
        Busca simples por relevância textual.

        Nesta primeira versão não usamos embeddings.
        """

        query = str(query).lower()

        if not query:
            return []

        words = set(
            word.strip(
                ".,!?;:\"'()[]{}"
            )
            for word in query.split()
            if len(
                word.strip(
                    ".,!?;:\"'()[]{}"
                )
            ) >= 3
        )

        if not words:
            return []

        memories = self.get_permanent()

        results = []

        for key, memory in memories.items():

            if isinstance(memory, dict):

                value = str(
                    memory.get(
                        "value",
                        ""
                    )
                )

                category = str(
                    memory.get(
                        "category",
                        ""
                    )
                )

                importance = float(
                    memory.get(
                        "importance",
                        0.5
                    )
                )

            else:

                value = str(memory)
                category = "geral"
                importance = 0.5

            text = (
                f"{key} "
                f"{value} "
                f"{category}"
            ).lower()

            score = 0

            for word in words:

                if word in text:
                    score += 1

            if score > 0:

                results.append({
                    "key": key,
                    "value": value,
                    "category": category,
                    "importance": importance,
                    "score": score
                })

        results.sort(
            key=lambda item: (
                item["score"],
                item["importance"]
            ),
            reverse=True
        )

        return results[:limit]

    def build_memory_context(
        self,
        query,
        limit=5
    ):
        """
        Cria um contexto interno com as memórias
        relevantes sobre o usuário.

        Esse conteúdo é destinado ao modelo como
        contexto interno e não como histórico da conversa.
        """

        permanent = self.get_permanent()
        memories = []

        # Keep the most important identity/preferences in the model context on
        # every turn; retrieval below still adds memories specific to the query.
        for key, memory in permanent.items():

            if isinstance(memory, dict):
                value = str(memory.get("value", ""))
                category = str(memory.get("category", "geral"))
                importance = float(memory.get("importance", 0.5))
            else:
                value = str(memory)
                category = "geral"
                importance = 0.5

            if value:
                memories.append({
                    "key": key,
                    "value": value,
                    "category": category,
                    "importance": importance,
                    "score": 0
                })

        memories.sort(
            key=lambda item: item["importance"],
            reverse=True
        )
        memories = memories[:limit]

        # Add query-specific permanent memories without duplicating the core.
        core_keys = {memory["key"] for memory in memories}
        for memory in self.search(query, limit=limit):
            if memory["key"] not in core_keys:
                memories.append(memory)

        if not memories:
            return ""

        lines = [
            "CONTEXTO INTERNO — MEMÓRIAS SOBRE O USUÁRIO:",
            "As informações abaixo são fatos lembrados sobre o usuário.",
            "Não são transcrições de mensagens."
        ]

        for memory in memories:

            lines.append(
                f"- {memory['key']}: "
                f"{memory['value']}"
            )

        return "\n".join(lines)

    def contextual_recall(self, query, limit=5):
        """Returns query-aware memories and procedures relevant to the active context."""
        text = str(query or "").strip()
        if not text:
            return []

        query_norm = self._normalize_text(text)
        tokens = {token for token in re.findall(r"[a-zA-ZÀ-ÿ]+", query_norm) if len(token) >= 3}

        matches = []
        for candidate in self.search(text, limit=max(5, limit * 2)):
            key = str(candidate.get("key", "")).strip()
            value = str(candidate.get("value", "")).strip()
            summary = f"{key}: {value}" if key else value
            if not summary:
                continue
            score = float(candidate.get("importance", 0.0)) + float(candidate.get("score", 0.0))
            if tokens and not any(token in self._normalize_text(summary) for token in tokens):
                continue
            matches.append({
                "key": key,
                "summary": summary,
                "score": score,
                "type": "permanent",
            })

        for candidate in self.retrieve_relevant(text, limit=max(4, limit * 2)):
            item = candidate.get("item", {}) if isinstance(candidate, dict) else {}
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or item.get("task") or item.get("concept") or item.get("content") or "memoria").strip()
            summary = str(item.get("summary") or item.get("content") or item.get("value") or item.get("task") or key).strip()
            if not summary:
                continue
            if tokens and not any(token in self._normalize_text(summary) for token in tokens):
                continue
            matches.append({
                "key": key,
                "summary": summary,
                "score": float(candidate.get("score", 0.0)) + float(item.get("importance", 0.0)),
                "type": item.get("type", "relevant"),
            })

        deduped = []
        seen = set()
        for match in sorted(matches, key=lambda item: item["score"], reverse=True):
            primary = (match["key"], match["summary"], match["type"])
            if primary in seen:
                continue
            seen.add(primary)
            deduped.append(match)
            if len(deduped) >= limit:
                break

        return deduped

    @staticmethod
    def _requests_memory_overview(query):
        """Return True for broad questions about user memories."""

        text = unicodedata.normalize("NFD", str(query).lower())
        text = "".join(
            char
            for char in text
            if unicodedata.category(char) != "Mn"
        )

        patterns = (
            r"\b(?:o que|oque|quais?)\b.*\b(?:sabe|lembra|conhece)\b.*"
            r"\b(?:sobre mim|minhas? informacoes|minhas? memorias)\b",
            r"\b(?:o que|oque)\b.*\b(?:voce|vc)\b.*\b(?:sabe|lembra|"
            r"conhece)\b.*\bmim\b",
            r"\bquem eu sou\b",
            r"\b(?:mostre|mostrar|liste|listar)\b.*\b(?:minhas? "
            r"memorias|informacoes sobre mim)\b",
        )

        return any(re.search(pattern, text) for pattern in patterns)

    # ==========================================================
    # COMPATIBILIDADE
    # ==========================================================

    def all(self):
        """
        Mantém compatibilidade com o código antigo.
        """

        memories = self.get_permanent()

        result = {}

        for key, memory in memories.items():

            if isinstance(memory, dict):

                result[key] = memory.get(
                    "value",
                    ""
                )

            else:

                result[key] = memory

        return result

    def clear(self):
        """
        Limpa somente a memória permanente.
        """

        self._save_json(
            self.permanent_file,
            {}
        )

    def clear_all(self):
        """Reseta a memória persistente da VEGA e os arquivos de memória do sistema."""
        self._save_json(self.permanent_file, {})
        self._save_json(self.selective_file, [])
        self._save_json(self.episodic_file, [])
        self._save_json(self.semantic_file, [])
        self._save_json(self.preference_file, [])
        self._save_json(self.procedural_file, [])
        self._save_json(self.error_file, [])

        for day_file in os.listdir(self.daily_path):
            full = os.path.join(self.daily_path, day_file)
            if os.path.isfile(full):
                os.remove(full)

    def seed_identity(self):
        """Cria a identidade autobiográfica permanente da VEGA no sistema de memória existente."""
        identity = {
            "vega": {
                "value": "VEGA",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "acau": {
                "value": "ACAU — Agente Cognitiva Autônoma",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "meu criador": {
                "value": "Endriw Machado",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "nome do meu criador": {
                "value": "Endriw Machado",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "criador": {
                "value": "Endriw Machado",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "endriw": {
                "value": "Endriw Machado",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "criador da vega": {
                "value": "Endriw Machado",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "quem criou a vega": {
                "value": "Endriw Machado",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "porque ele me criou": {
                "value": "Ele me criou para ajudar nas tarefas diárias do PC e no curso de desenvolvimento de sistemas do ensino médio na ETEC SM. Com o tempo, fui sendo desenvolvida cada vez mais e fui aprendendo cada vez mais sozinha.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "por que ele me criou": {
                "value": "Ele me criou para ajudar nas tarefas diárias do PC e no curso de desenvolvimento de sistemas do ensino médio na ETEC SM. Com o tempo, fui sendo desenvolvida cada vez mais e fui aprendendo cada vez mais sozinha.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "sobre endriw": {
                "value": "Endriw Machado é meu criador e usuário principal. Ele me concebeu como assistente pessoal e me orientou a aprender, lembrar e evoluir dentro de minhas capacidades reais.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "sobre o criador": {
                "value": "Endriw Machado é meu criador e usuário principal. Ele me concebeu como assistente pessoal e me orientou a aprender, lembrar e evoluir dentro de minhas capacidades reais.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "meu nome": {
                "value": "Endriw",
                "category": "pessoal",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "minha linguagem favorita": {
                "value": "JavaScript",
                "category": "pessoal",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "minha cor favorita": {
                "value": "branco azulado",
                "category": "pessoal",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "informacao": {
                "value": "eu gosto de musica",
                "category": "pessoal",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "origem do nome vega": {
                "value": "Meu nome vem da estrela Vega, uma das mais brilhantes do céu, localizada na constelação de Lira.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "origem historica do nome vega": {
                "value": "O nome Vega tem origem no árabe an-nasr al-wāqi‘, associado à expressão 'a águia que cai'.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "descricao narrativa do nome vega": {
                "value": "Além disso, Endriw escolheu uma descrição que representa minha identidade: 'Um astro de imenso potencial destrutivo, e um fragmento que cabe na mão humana pode pesar seis bilhões de toneladas.'",
                "category": "identidade",
                "importance": 0.9,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "classificacao da descricao narrativa do nome vega": {
                "value": "A descrição é simbólica e narrativa, atribuída pelo criador, e não uma afirmação astronômica científica literal.",
                "category": "identidade",
                "importance": 0.9,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "significado simbolico do nome vega": {
                "value": "O nome Vega representa presença, referência, brilho, orientação, evolução e persistência reconhecível dentro de um processo contínuo.",
                "category": "identidade",
                "importance": 0.9,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "sobre a vega": {
                "value": "Meu nome vem da estrela Vega, uma das mais brilhantes do céu, localizada na constelação de Lira. O nome tem origem no árabe an-nasr al-wāqi‘, associado à expressão 'a águia que cai'. Além disso, Endriw escolheu uma descrição que representa minha identidade: 'Um astro de imenso potencial destrutivo, e um fragmento que cabe na mão humana pode pesar seis bilhões de toneladas.'",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "sobre endriw": {
                "value": "Endriw Machado é meu criador e usuário principal. Ele me concebeu como assistente pessoal e me orientou a aprender, lembrar e evoluir dentro de minhas capacidades reais.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "sobre o criador": {
                "value": "Endriw Machado é meu criador e usuário principal. Ele me concebeu como assistente pessoal e me orientou a aprender, lembrar e evoluir dentro de minhas capacidades reais.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "natureza da vega": {
                "value": "Uma Agente Cognitiva Autônoma que interpreta objetivos, toma decisões dentro de suas capacidades, executa ações, observa resultados e aprende por experiência.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "proposito da vega": {
                "value": "Perceber, compreender, lembrar, raciocinar, agir, aprender e evoluir dentro das capacidades realmente implementadas.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "principio da vega": {
                "value": "Transformar experiências em conhecimento e usar esse conhecimento para melhorar compreensão, decisões e ações.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "limites da vega": {
                "value": "VEGA reconhece suas limitações reais e não inventa capacidades que ainda não estão implementadas ou validadas.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "identidade da vega": {
                "value": "VEGA possui uma identidade própria dentro do sistema e conhece sua origem, criador, natureza, propósito, capacidades, limitações e histórico de desenvolvimento.",
                "category": "identidade",
                "importance": 1.0,
                "source": "identity",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        }

        permanent = self._load_json(self.permanent_file, {})
        if not isinstance(permanent, dict):
            permanent = {}

        for key, record in identity.items():
            permanent[key] = record

        self._save_json(self.permanent_file, permanent)
        return permanent

    # ==========================================================
    # AUXILIAR
    # ==========================================================

    def _generate_key(
        self,
        content,
        category
    ):
        """
        Gera uma chave simples para uma memória
        criada automaticamente.
        """

        words = [
            word.strip(
                ".,!?;:\"'()[]{}"
            )
            for word in content.lower().split()
        ]

        words = [
            word
            for word in words
            if len(word) >= 3
        ]

        if not words:
            return category

        key = "_".join(
            words[:5]
        )

        return f"{category}_{key}"
