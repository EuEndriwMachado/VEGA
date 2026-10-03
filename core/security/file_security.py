"""Explicit file lookup and deletion workflow for authorized actions."""

from __future__ import annotations

import ctypes
import difflib
import os
import re
import unicodedata
from pathlib import Path

from config import file_access


class FileSecurity:
    """Find files across accessible Windows drives and delete one exact file."""

    _SKIP_DIRECTORIES = {
        "$recycle.bin",
        "system volume information",
        "windows.old",
    }
    _FOLDER_ALIASES = {
        "downloads": ("Downloads",),
        "documentos": ("Documentos", "Documents"),
        "documents": ("Documents", "Documentos"),
        "imagens": ("Imagens", "Pictures"),
        "pictures": ("Pictures", "Imagens"),
        "videos": ("Vídeos", "Videos"),
        "musicas": ("Músicas", "Musicas", "Music"),
        "music": ("Music", "Músicas", "Musicas"),
        "desktop": ("Desktop", "Área de Trabalho", "Area de Trabalho"),
        "area de trabalho": ("Área de Trabalho", "Area de Trabalho", "Desktop"),
    }

    @staticmethod
    def _normalize(value):
        value = unicodedata.normalize("NFKD", str(value or ""))
        return "".join(char for char in value if not unicodedata.combining(char)).lower().strip()

    @classmethod
    def _tokens(cls, value):
        return re.findall(r"[a-z0-9]+", cls._normalize(value))

    @staticmethod
    def _word_forms(word):
        forms = {word}
        if len(word) > 3 and word.endswith("s"):
            forms.add(word[:-1])
        if len(word) > 5 and word.endswith("es"):
            forms.add(word[:-2])
        return forms

    @classmethod
    def _token_score(cls, query_token, candidate_token):
        if cls._word_forms(query_token) & cls._word_forms(candidate_token):
            return 1.0
        if len(query_token) >= 4 and (query_token.startswith(candidate_token) or candidate_token.startswith(query_token)):
            return 0.9
        return difflib.SequenceMatcher(None, query_token, candidate_token).ratio()

    @classmethod
    def _file_score(cls, query, filename):
        query_tokens = cls._tokens(Path(query).stem)
        candidate_tokens = cls._tokens(Path(filename).stem)
        if not query_tokens or not candidate_tokens:
            return 0.0, 0.0
        scores = [max(cls._token_score(query_token, candidate_token) for candidate_token in candidate_tokens) for query_token in query_tokens]
        coverage = sum(score >= 0.72 for score in scores) / len(scores)
        return max(scores), coverage

    @staticmethod
    def roots():
        if os.name != "nt":
            return [Path.home()]
        try:
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            drives = [Path(f"{chr(65 + index)}:\\") for index in range(26) if mask & (1 << index)]
            home = Path.home()
            personal_names = (
                "Downloads", "Documentos", "Documents", "Imagens", "Pictures",
                "Vídeos", "Videos", "Músicas", "Musicas", "Music", "Desktop",
                "Área de Trabalho", "Area de Trabalho",
            )
            personal = [home / name for name in personal_names if (home / name).is_dir()]
            home_drive = home.anchor.lower()
            ordered_drives = sorted(drives, key=lambda root: 0 if str(root).lower() == home_drive else 1)
            return personal + ordered_drives or [home]
        except (AttributeError, OSError):
            return [Path.home()]

    @classmethod
    def _folder_roots(cls, folder):
        normalized = cls._normalize(folder)
        names = cls._FOLDER_ALIASES.get(normalized, (folder,))
        home = Path.home()
        candidates = []
        for name in names:
            candidate = home / name
            if candidate.is_dir():
                candidates.append(candidate)
        return candidates

    @classmethod
    def find_files(cls, query: str, roots=None, limit=25, folder=""):
        query = Path(str(query or "").strip().strip('"')).name.lower()
        normalized_query = cls._normalize(query)
        normalized_folder = cls._normalize(folder)
        folder_aliases = {
            cls._normalize(name)
            for name in cls._FOLDER_ALIASES.get(normalized_folder, (folder,))
        }
        if not query:
            return []
        scored_matches = []
        if roots is not None:
            search_roots = roots
        elif folder:
            search_roots = cls._folder_roots(folder)
        else:
            search_roots = cls.roots()
        seen = set()
        for root in search_roots:
            root = Path(root)
            if not root.is_dir():
                continue
            try:
                for current, directories, filenames in os.walk(root, topdown=True, onerror=lambda _error: None):
                    directories[:] = [
                        directory for directory in directories
                        if directory.lower() not in cls._SKIP_DIRECTORIES
                    ]
                    for filename in filenames:
                        current_folder = cls._normalize(Path(current).name)
                        folder_match = not normalized_folder or current_folder in folder_aliases
                        best_score, coverage = cls._file_score(query, filename)
                        if folder_match and best_score >= 0.72 and coverage > 0:
                            target = (Path(current) / filename).resolve()
                            if target in seen:
                                continue
                            seen.add(target)
                            scored_matches.append((coverage, best_score, target))
            except (OSError, PermissionError):
                continue
        scored_matches.sort(key=lambda item: (
            -item[0],
            -item[1],
            0 if cls._normalize(item[2].stem) == normalized_query else 1,
            0 if normalized_folder and cls._normalize(item[2].parent.name) == normalized_folder else 1,
            len(item[2].parts),
            str(item[2]).lower(),
        ))
        matches = [item[2] for item in scored_matches]
        return matches[:limit]

    @staticmethod
    def delete_file(path: str | Path, *, authorization_token: str = ""):
        if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
            raise PermissionError(
                "A exclusão de arquivos pessoais está desativada na configuração local."
            )
        if not authorization_token:
            raise PermissionError(
                "A exclusão de arquivos exige um token de autorização emitido "
                "pelo gate de segurança da VEGA."
            )
        from core.security.security import VegaSecurity
        if not VegaSecurity._validate_token_globally(authorization_token):
            raise PermissionError(
                "Token de autorização inválido, expirado ou já utilizado. "
                "Nada foi apagado."
            )
        target = Path(path).expanduser().resolve()
        if not target.is_file():
            raise FileNotFoundError(str(target))
        target.unlink()
        return target

    @staticmethod
    def empty_recycle_bin(*, authorization_token: str = ""):
        if not file_access.ALLOW_PERSONAL_FILE_CHANGES:
            raise PermissionError(
                "A exclusão de arquivos pessoais está desativada na configuração local."
            )
        if not authorization_token:
            raise PermissionError(
                "O esvaziamento da Lixeira exige um token de autorização emitido "
                "pelo gate de segurança da VEGA."
            )
        from core.security.security import VegaSecurity
        if not VegaSecurity._validate_token_globally(authorization_token):
            raise PermissionError(
                "Token de autorização inválido, expirado ou já utilizado. "
                "Nada foi apagado."
            )
        if os.name != "nt":
            raise OSError("A lixeira automática só está disponível no Windows.")
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x00000007)
        if result != 0:
            raise OSError(result, "Não foi possível esvaziar a Lixeira.")
        return True
