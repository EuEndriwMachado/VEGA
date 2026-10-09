"""Local authorization helpers for sensitive VEGA actions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import math
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.security.face_security import LocalFaceValidator


class VegaSecurity:
    MAX_ATTEMPTS = 5
    LOCKOUT_SECONDS = 300
    _lock = threading.RLock()

    def __init__(self, path=None):
        # Do not select a different credential store when the working directory changes.
        self.path = Path(path) if path is not None else Path(__file__).resolve().parents[2] / "data" / "security.json"
        self._face_reference = self.path.parent / "face_reference.json"
        self._salt = os.urandom(16)
        self._digest = ""
        self._failed_attempts = 0
        self._locked_until = 0.0
        self._state_valid = True
        self._load()
        self.face_validator = LocalFaceValidator(self._face_reference)

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            salt = bytes.fromhex(data["salt"])
            digest = data["digest"]
            failures = data.get("failed_attempts", 0)
            locked_until = data.get("locked_until", 0.0)
            if (
                len(salt) != 16
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or type(failures) is not int
                or not 0 <= failures < self.MAX_ATTEMPTS
                or type(locked_until) not in (int, float)
                or not math.isfinite(locked_until)
                or locked_until < 0
            ):
                raise ValueError("Invalid security state")
            self._salt, self._digest = salt, digest
            self._failed_attempts, self._locked_until = failures, locked_until
            self._state_valid = True
        except FileNotFoundError:
            # A store disappearing during this session is not a first-time setup.
            self._state_valid = self._state_valid and not bool(self._digest)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            self._state_valid = False
    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "salt": self._salt.hex(), "digest": self._digest,
            "failed_attempts": self._failed_attempts, "locked_until": self._locked_until,
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(data, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def set_pin(self, pin: str, *, current_pin: Optional[str] = None):
        # Local setup/rotation only; never expose this method as an AI tool.
        pin = str(pin).strip()
        if not re.fullmatch(r"[0-9]{4,64}", pin):
            raise ValueError("O PIN deve ter de 4 a 64 dígitos de 0 a 9.")
        with self._lock:
            self._load()
            if not self._state_valid:
                raise PermissionError("Configuração de segurança inválida. Troca bloqueada.")
            if self._digest and not self.verify(current_pin or ""):
                raise PermissionError("PIN atual inválido ou tentativas temporariamente bloqueadas.")
            self._salt = os.urandom(16)
            self._digest = self._hash(pin)
            self._failed_attempts = 0
            self._locked_until = 0.0
            self._save()

    def has_pin(self):
        return self._state_valid and bool(self._digest)

    def verify(self, pin: str):
        with self._lock:
            self._load()
            if not self.has_pin() or time.time() < self._locked_until:
                return False
            candidate = str(pin).strip()
            valid = bool(re.fullmatch(r"[0-9]{4,64}", candidate)) and hmac.compare_digest(self._digest, self._hash(candidate))
            self._failed_attempts = 0 if valid else self._failed_attempts + 1
            self._locked_until = 0.0
            if self._failed_attempts >= self.MAX_ATTEMPTS:
                self._failed_attempts = 0
                self._locked_until = time.time() + self.LOCKOUT_SECONDS
            try:
                self._save()
            except OSError:
                # Never authorize if attempt accounting cannot be persisted.
                self._state_valid = False
                return False
            return valid

    def enroll_face_reference(self, images_dir="Uploads"):
        result = LocalFaceValidator.enroll_from_directory(images_dir, self._face_reference)
        self.face_validator = LocalFaceValidator(self._face_reference)
        return result

    def _face_camera_candidates(self, camera_index=None):
        """Câmeras a tentar na validação facial, em ordem de preferência.

        A PRIMEIRA é sempre a mais confiável para reconhecer o dono:

        1. `camera_index` explícito (quando o chamador sabe qual usar);
        2. a câmera que o usuário escolheu no menu (é a que ele vê funcionar);
        3. a WEBCAM DO PC, resolvida por NOME — a Iriun (celular) fica de fora
           da escolha automática;
        4. demais índices, apenas como último recurso.

        Devolver uma lista (e não um número) é o que permite tentar outra
        câmera quando a primeira está ocupada por outro programa — mas a
        ordem garante que a câmera ERRADA nunca seja a primeira tentativa.
        """
        if camera_index is not None:
            try:
                return [int(camera_index)]
            except (TypeError, ValueError):
                pass

        candidates = []

        def _add(value):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return
            if value >= 0 and value not in candidates:
                candidates.append(value)

        # 1. A escolha explícita do usuário no menu de câmera.
        try:
            from config.camera_menu import load_preferred_camera

            _add(load_preferred_camera())
        except Exception:
            pass

        # 2. A webcam física, resolvida por nome (nunca a virtual do celular).
        try:
            from core.vision.camera import VegaCamera

            _add(VegaCamera.select_camera())
        except Exception:
            pass

        # 3. Demais índices, como último recurso.
        try:
            from core.vision.camera import VegaCamera

            for index in VegaCamera.list_cameras(4):
                _add(index)
        except Exception:
            pass

        _add(0)
        return candidates

    def capture_face_validation(self, camera_index=None):
        """Captura e valida o rosto para um pedido sensível.

        REGRA DE SEGURANÇA (a câmera da validação facial):

        1. usa a câmera ESCOLHIDA pelo usuário no menu, quando houver;
        2. senão, usa a WEBCAM DO PC — resolvida pelo NOME do dispositivo,
           nunca por um índice fixo;
        3. só tenta outra câmera se a primeira NÃO ABRIR — jamais aceita uma
           câmera que abriu sem conseguir avaliar o rosto.

        Antes, a lista era `[0, 1]` fixa: na máquina com a Iriun no índice 0, a
        validação facial usava a CÂMERA DO CELULAR. Pior: o laço parava na
        primeira câmera que simplesmente ABRISSE (`supported=True`), mesmo sem
        rosto algum — então nem chegava a testar a webcam do PC.
        """
        if not self.face_validator.ready:
            return False, None, None

        camera_indices = self._face_camera_candidates(camera_index)
        supported = False
        frame = None
        face_valid = False
        for index in camera_indices:
            supported, face_valid, frame = self.face_validator.validate_camera_sequence(index)
            if supported:
                break
        if not supported:
            return False, None, None

        evidence_dir = self.path.parent / "security_evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / f"command_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jpg"
        try:
            import cv2
            cv2.imwrite(str(evidence_path), frame)
            self._prune_evidence(evidence_dir)
            return True, bool(face_valid), str(evidence_path)
        except Exception:
            return True, bool(face_valid), None

    # Manter apenas as últimas N fotos: pasta de "últimos registros",
    # sem acumular dezenas de imagens praticamente iguais.
    EVIDENCE_KEEP = 30

    @classmethod
    def _prune_evidence(cls, evidence_dir, keep=None):
        """Exclui as fotos mais antigas, mantendo só as últimas `keep`."""
        keep = keep or cls.EVIDENCE_KEEP
        try:
            photos = sorted(
                p for p in evidence_dir.iterdir()
                if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
            )
            for old in photos[:-keep] if len(photos) > keep else []:
                try:
                    old.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    @staticmethod
    def _summarize_action(action) -> str:
        """Resumo seguro da ação para o log (sem risco de gravar o PIN).

        Remove qualquer sequência longa de dígitos, que é como um PIN aparece
        numa fala ("apaga o arquivo garfo 4321").
        """
        if isinstance(action, dict):
            action = action.get("kind") or str(action)
        text = str(action or "").strip()
        text = re.sub(r"\b\d{4,}\b", "[pin removido]", text)
        return text[:200]

    def _hash(self, value):
        return hashlib.pbkdf2_hmac("sha256", value.encode(), self._salt, 120_000).hex()

    @staticmethod
    def _normalize_action(action: str) -> str:
        return (action or "").lower().strip()

    @staticmethod
    def _is_dangerous_action(action: str) -> bool:
        normalized = VegaSecurity._normalize_action(action)
        if "cancela" in normalized or "cancelar" in normalized:
            return False
        dangerous_keywords = (
            "apagar",
            "apague",
            "deletar",
            "delete",
            "remover",
            "remova",
            "formatar",
            "formate",
            "desinstalar",
            "desinstale",
            "limpar tudo",
            "apaga",
            "deletar arquivos",
            "excluir",
            "exclua",
            "remover arquivos",
            "apagar pasta",
            "lixeira",
            "reciclagem",
            "desligar pc",
            "desliga o pc",
            "desligar computador",
            "desliga o computador",
            "desliga a maquina",
            "reiniciar pc",
            "reinicia o pc",
            "reiniciar computador",
            "reinicia o computador",
            "reinicia a maquina",
            "destruir",
            "destrua",
        )
        return any(keyword in normalized for keyword in dangerous_keywords)

    def register_security_request(self, action, source="voz"):
        """Registra o PEDIDO de uma ação sensível e tira a foto na hora.

        Este é o registro de auditoria no momento em que o usuário PEDE algo
        perigoso — antes de qualquer senha. É o que responde "quem pediu
        isso?" mesmo quando a pessoa erra o PIN e a ação nunca acontece.

        Devolve o caminho da evidência (foto) capturada, ou None.
        """
        evidence_path = None
        face_supported = False
        try:
            face_supported, _face_valid, evidence_path = self.capture_face_validation()
        except Exception:
            # Falha de câmera nunca pode impedir o registro do pedido.
            face_supported, evidence_path = False, None

        self._write_audit(
            action,
            confirmed=False,
            pin_valid=False,
            face_valid=None,
            face_supported=face_supported,
            evidence_path=evidence_path,
            event="requested",
            source=source,
        )
        return evidence_path

    def register_auth_attempt(self, action, pin_valid, face_valid=None,
                              face_supported=False, evidence_path=None,
                              authorized=False, source="celular"):
        """Registra uma TENTATIVA de autenticação (certa ou errada).

        Assim, um PIN errado fica no log com data, ação e resultado — antes,
        a tentativa falha simplesmente não era registrada em lugar nenhum.
        """
        self._write_audit(
            action,
            confirmed=True,
            pin_valid=bool(pin_valid),
            face_valid=face_valid,
            face_supported=bool(face_supported),
            evidence_path=evidence_path,
            event="authorized" if authorized else "denied",
            source=source,
        )

    def _write_audit(self, action: str, confirmed: bool, pin_valid: bool, face_valid: Optional[bool], face_supported: bool, evidence_path=None, event=None, source=""):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        log_path = self.path.parent / "security_audit.json"
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []

        # O texto da ação NUNCA é gravado cru: ele pode conter o PIN ditado
        # ("apaga o arquivo 1234"). Guardamos um resumo seguro e o evento.
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event or "dangerous_action",
            "action": "dangerous_action",
            "action_summary": self._summarize_action(action),
            "source": source or "voz",
            "confirmed": bool(confirmed),
            "pin_valid": bool(pin_valid),
            "face_supported": bool(face_supported),
            "face_valid": face_valid,
            "evidence_path": evidence_path,
            "authorized": bool(confirmed and pin_valid and (not face_supported or face_valid is True)),
        }
        # Compatibilidade: quem já lê o log antigo continua achando a ação.
        if event in (None, "dangerous_action"):
            entry["action"] = self._summarize_action(action)
        existing.append(entry)
        log_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    def authorize_dangerous_action(self, action: str, confirmed: bool = False, pin: str = "", face_supported: bool = False, face_valid: Optional[bool] = None, evidence_path=None):
        action_text = self._normalize_action(action)
        if not self._is_dangerous_action(action_text):
            return True

        if not confirmed:
            self._write_audit(action_text, False, False, None, face_supported, evidence_path)
            return False

        if not self.has_pin():
            self._write_audit(action_text, True, False, None, face_supported, evidence_path)
            return False

        pin_ok = bool(pin) and self.verify(str(pin))
        if not pin_ok:
            self._write_audit(action_text, True, False, None, face_supported, evidence_path)
            return False

        if face_supported:
            if face_valid is None:
                self._write_audit(action_text, True, True, None, True, evidence_path)
                return False
            if face_valid is not True:
                self._write_audit(action_text, True, True, False, True, evidence_path)
                return False

        self._write_audit(action_text, True, True, face_valid, face_supported, evidence_path)
        return self._issue_token(action_text)

    # ==========================================================
    # TOKEN DE AUTORIZAÇÃO DE USO ÚNICO
    # ==========================================================
    # Operações destrutivas (FileSecurity.delete_file, empty_recycle_bin)
    # exigem um token emitido AQUI, logo após a validação de PIN + face.
    # Isso garante defesa em profundidade: mesmo que outro módulo chame
    # FileSecurity direto, sem token ele é bloqueado.
    # ==========================================================
    _TOKEN_TTL_SECONDS = 60

    def _issue_token(self, action: str) -> str:
        if not hasattr(self, "_active_tokens"):
            self._active_tokens = {}
        token = secrets.token_urlsafe(32)
        self._active_tokens[token] = {
            "action": action,
            "expires_at": time.time() + self._TOKEN_TTL_SECONDS,
            "used": False,
        }
        return token

    def consume_token(self, token: str) -> bool:
        # Valida e invalida um token em uma única chamada (uso único).
        if not hasattr(self, "_active_tokens") or not token:
            return False
        entry = self._active_tokens.pop(token, None)
        if entry is None or entry["used"] or time.time() > entry["expires_at"]:
            return False
        return True

    # Registro global para que FileSecurity possa validar tokens
    # sem manter referência à instância da VegaSecurity do cérebro.
    _global_tokens: dict = {}
    _global_lock = threading.Lock()

    @classmethod
    def _issue_global_token(cls, action: str) -> str:
        token = secrets.token_urlsafe(32)
        with cls._global_lock:
            cls._global_tokens[token] = {
                "action": action,
                "expires_at": time.time() + cls._TOKEN_TTL_SECONDS,
            }
        return token

    @classmethod
    def _validate_token_globally(cls, token: str) -> bool:
        with cls._global_lock:
            entry = cls._global_tokens.pop(token, None)
        if entry is None or time.time() > entry["expires_at"]:
            return False
        return True

    def _issue_token(self, action: str) -> str:
        token = self._issue_global_token(action)
        if not hasattr(self, "_active_tokens"):
            self._active_tokens = {}
        self._active_tokens[token] = {
            "action": action,
            "expires_at": time.time() + self._TOKEN_TTL_SECONDS,
            "used": False,
        }
        return token

