"""Local productivity tools with conservative, explicit filesystem behavior."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from datetime import date, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable
from pypdf import PdfReader

from config import file_access


class ProductivityTools:
    def __init__(self, root: str | None = None):
        self.root = Path(root or Path.home()).expanduser().resolve()

    def _path(self, value: str) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve()

    def _creation_path(self, value: str) -> Path:
        """Returns a new sibling name when creation-only mode finds a collision."""
        target = self._path(value)
        if file_access.ALLOW_PERSONAL_FILE_CHANGES or not target.exists():
            return target

        index = 2
        while True:
            candidate = target.with_name(f"{target.stem}_{index}{target.suffix}")
            if not candidate.exists():
                return candidate
            index += 1

    def find_files(self, query: str, path: str = "") -> list[dict[str, str]]:
        base = self._path(path or str(self.root))
        if not base.is_dir():
            return []
        pattern = str(query or "*").lower()
        results = []
        for item in base.rglob("*"):
            if len(results) >= 100:
                break
            if item.is_file() and (pattern in item.name.lower() or pattern in str(item).lower()):
                results.append({"name": item.name, "path": str(item)})
        return results

    def read_text(self, path: str, max_chars: int = 20000) -> dict[str, Any]:
        target = self._path(path)
        if not target.is_file():
            raise FileNotFoundError(str(target))
        if target.suffix.lower() == ".pdf":
            reader = PdfReader(str(target))
            remaining = max(0, int(max_chars))
            pages = []
            for index, page in enumerate(reader.pages, 1):
                if remaining <= 0:
                    break
                extracted = (page.extract_text() or "").strip()
                if not extracted:
                    continue
                page_text = f"[Página {index}]\n{extracted}\n"
                page_text = page_text[:remaining]
                pages.append(page_text)
                remaining -= len(page_text)
            return {
                "path": str(target),
                "content": "\n".join(pages),
                "pages": len(reader.pages),
            }
        return {"path": str(target), "content": target.read_text(encoding="utf-8", errors="replace")[:max_chars]}

    def write_text(self, path: str, content: str) -> dict[str, str]:
        target = self._path(path)
        if target.exists() and not file_access.ALLOW_PERSONAL_FILE_CHANGES:
            raise PermissionError(
                "A edição de arquivos pessoais está desativada na configuração local."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        return {"path": str(target), "status": "written"}

    def summarize_document(self, path: str, max_chars: int = 12000) -> dict[str, Any]:
        result = self.read_text(path, max_chars=max_chars)
        text = re.sub(r"\s+", " ", result["content"]).strip()
        summary = {"path": result["path"], "characters": len(text), "preview": text[:2000]}
        if "pages" in result:
            summary["pages"] = result["pages"]
        return summary

    def read_csv(self, path: str, limit: int = 100) -> dict[str, Any]:
        target = self._path(path)
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))[:limit]
        return {"path": str(target), "rows": rows, "count": len(rows)}

    def read_json(self, path: str) -> dict[str, Any]:
        target = self._path(path)
        return {"path": str(target), "data": json.loads(target.read_text(encoding="utf-8"))}

    def create_markdown(self, path: str, title: str, sections: Iterable[str] = ()) -> dict[str, str]:
        body = "# " + str(title).strip() + "\n\n" + "\n\n".join(str(section) for section in sections)
        return self.write_text(str(self._creation_path(path)), body)

    def create_calendar_event(self, title: str, start: str, end: str = "", description: str = ""):
        event = "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
        event += f"SUMMARY:{title}\nDTSTART:{start}\n"
        if end:
            event += f"DTEND:{end}\n"
        event += f"DESCRIPTION:{description}\nEND:VEVENT\nEND:VCALENDAR\n"
        result = self.write_text(str(self._creation_path("VEGA_calendar.ics")), event)
        result["status"] = "created"
        return result

    def list_calendar_events(self, on_date: date | None = None) -> list[dict[str, str]]:
        target_date = on_date or datetime.now().date()
        events = []
        for calendar_path in sorted(self.root.glob("VEGA_calendar*.ics")):
            try:
                lines = calendar_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            unfolded = []
            for line in lines:
                if line.startswith((" ", "\t")) and unfolded:
                    unfolded[-1] += line[1:]
                else:
                    unfolded.append(line)

            current = None
            for line in unfolded:
                if line == "BEGIN:VEVENT":
                    current = {"path": str(calendar_path)}
                    continue
                if line == "END:VEVENT":
                    if current and current.get("start_date") == target_date:
                        current.pop("start_date", None)
                        events.append(current)
                    current = None
                    continue
                if current is None or ":" not in line:
                    continue

                property_name, value = line.split(":", 1)
                property_name = property_name.split(";", 1)[0].upper()
                if property_name == "SUMMARY":
                    current["title"] = value.replace("\\,", ",").replace("\\;", ";")
                elif property_name in {"DTSTART", "DTEND"}:
                    try:
                        if "T" not in value:
                            event_date = datetime.strptime(value, "%Y%m%d").date()
                            event_time = "Dia inteiro"
                        else:
                            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                            event_date = parsed.astimezone().date() if parsed.tzinfo else parsed.date()
                            event_time = parsed.astimezone().strftime("%H:%M") if parsed.tzinfo else parsed.strftime("%H:%M")
                        current["start_date"] = event_date
                        current["start_time" if property_name == "DTSTART" else "end_time"] = event_time
                    except ValueError:
                        continue

        return sorted(events, key=lambda event: event.get("start_time", ""))

    def compose_email(self, recipient: str, subject: str = "", body: str = ""):
        url = "mailto:" + urllib.parse.quote(str(recipient))
        params = urllib.parse.urlencode({"subject": subject, "body": body})
        return {"mailto": f"{url}?{params}", "status": "ready"}

    def compose_whatsapp(self, recipient: str, message: str = ""):
        number = re.sub(r"\D", "", str(recipient))
        return {"url": f"https://wa.me/{number}?text={urllib.parse.quote(str(message))}", "status": "ready"}

    def open_in_vscode(self, path: str):
        target = self._path(path)
        command = [self._vscode_executable(), "--new-window", str(target)]
        process = subprocess.Popen(command)
        return {"command": command, "path": str(target), "pid": process.pid, "status": "opened"}

    def _open_project_file_in_vscode(self, path: Path, *, new_window: bool):
        window_option = "--new-window" if new_window else "--reuse-window"
        command = [self._vscode_executable(), window_option, str(path)]
        process = subprocess.Popen(command)
        return {"command": command, "path": str(path), "pid": process.pid, "status": "opened"}

    @staticmethod
    def _vscode_executable() -> str:
        command = shutil.which("code")
        if os.name != "nt":
            if command:
                return command
            raise FileNotFoundError("O comando do Visual Studio Code não está no PATH.")

        candidates = []
        if command:
            command_path = Path(command)
            candidates.extend((command_path.parent.parent / "Code.exe", command_path.with_suffix(".exe")))
        for variable in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base:
                candidates.append(Path(base) / "Programs" / "Microsoft VS Code" / "Code.exe")
                candidates.append(Path(base) / "Microsoft VS Code" / "Code.exe")

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        raise FileNotFoundError("Não encontrei o executável do Visual Studio Code.")

    @staticmethod
    def _project_file_path(project_dir: Path, filename: str) -> Path:
        value = str(filename or "").strip().replace("\\", "/")
        relative = PurePosixPath(value)
        windows_relative = PureWindowsPath(str(filename or ""))
        if (
            not value
            or relative.is_absolute()
            or windows_relative.is_absolute()
            or windows_relative.drive
            or ".." in relative.parts
        ):
            raise ValueError(f"Caminho de arquivo inválido no projeto: {filename!r}")

        target = (project_dir / Path(*relative.parts)).resolve()
        try:
            target.relative_to(project_dir)
        except ValueError as error:
            raise ValueError(f"Caminho fora da pasta do projeto: {filename!r}") from error
        return target

    @staticmethod
    def _project_file_priority(path: Path) -> tuple[int, str]:
        priority = {
            "index.html": 0,
            "main.py": 1,
            "app.py": 2,
            "main.js": 3,
            "index.js": 4,
            "app.jsx": 5,
            "app.tsx": 6,
        }
        return priority.get(path.name.lower(), len(priority)), str(path).lower()

    def create_project(self, path: str, files: dict[str, str], open_in_editor: bool = True) -> dict[str, Any]:
        if not str(path or "").strip():
            raise ValueError("Informe o nome ou caminho do projeto.")
        if not isinstance(files, dict) or not files:
            raise ValueError("O projeto precisa conter ao menos um arquivo.")
        if len(files) > 200:
            raise ValueError("O projeto excede o limite de 200 arquivos.")

        total_size = sum(len(str(content).encode("utf-8")) for content in files.values())
        if total_size > 5 * 1024 * 1024:
            raise ValueError("O projeto excede o limite de 5 MB de conteúdo.")

        requested_dir = self._path(path)
        project_dir = requested_dir
        suffix = 2
        while project_dir.exists():
            project_dir = requested_dir.with_name(f"{requested_dir.name}_{suffix}")
            suffix += 1

        file_targets = {
            self._project_file_path(project_dir, filename): str(content)
            for filename, content in files.items()
        }
        if len(file_targets) != len(files):
            raise ValueError("O projeto contém nomes de arquivo repetidos.")
        file_targets = dict(sorted(file_targets.items(), key=lambda item: self._project_file_priority(item[0])))

        project_dir.mkdir(parents=True, exist_ok=False)
        editor_result = None
        editor_available = bool(open_in_editor)
        chunk_size = max(32, (sum(len(content) for content in file_targets.values()) + 79) // 80)

        for index, (target, content) in enumerate(file_targets.items()):
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="") as handle:
                if editor_available:
                    try:
                        editor_result = self._open_project_file_in_vscode(target, new_window=index == 0)
                    except OSError as error:
                        editor_available = False
                        editor_result = {"status": "unavailable", "error": str(error)}

                if editor_available:
                    for offset in range(0, len(content), chunk_size):
                        handle.write(content[offset:offset + chunk_size])
                        handle.flush()
                        time.sleep(0.04)
                else:
                    handle.write(content)

        if editor_available:
            try:
                editor_result = self._open_project_file_in_vscode(next(iter(file_targets)), new_window=False)
            except OSError as error:
                editor_result = {"status": "unavailable", "error": str(error)}

        return {
            "path": str(project_dir),
            "files": [str(target.relative_to(project_dir)) for target in file_targets],
            "count": len(file_targets),
            "status": "created",
            "editor": editor_result,
        }

    def create_document(self, path: str, title: str, content: str = ""):
        return self.write_text(
            str(self._creation_path(path)),
            f"# {title}\n\n{content}",
        )

    def create_presentation_outline(self, path: str, title: str, slides: Iterable[str] = ()):
        content = "# " + title + "\n\n" + "\n\n".join(f"## Slide {index}\n{slide}" for index, slide in enumerate(slides, 1))
        return self.write_text(str(self._creation_path(path)), content)
