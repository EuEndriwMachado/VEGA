# -*- coding: utf-8 -*-
"""Gate de segurança para o controle do celular pela VEGA.

Controlar o celular é uma ação sensível: dá acesso a apps, arquivos,
mensagens e à tela do aparelho. Este módulo exige, na primeira vez:

    1. reconhecimento facial (quando a câmera está disponível);
    2. PIN da VEGA.

Depois de autorizado uma vez, o acesso fica liberado **enquanto o celular
estiver conectado**. Se o aparelho for desconectado, a autorização é
revogada e o próximo comando pede validação de novo.

Reutiliza o `VegaSecurity` existente (mesmo PIN, mesma validação facial,
mesmas evidências) — não cria um segundo sistema de segurança.
"""

from __future__ import annotations

import threading
import time


class PhoneAccessGuard:
    """Controla se o usuário já se autenticou para usar o celular."""

    def __init__(self, security, session_ttl_seconds=6 * 60 * 60):
        self.security = security
        self.session_ttl = session_ttl_seconds
        self._lock = threading.RLock()

        self._granted = False
        self._granted_at = 0.0
        self._device_serial = ""

        # Ação aguardando autorização (o comando que o usuário pediu).
        self._pending_action = None

    # ==========================================================
    # ESTADO
    # ==========================================================

    def is_granted(self, device_serial="") -> bool:
        """True quando o usuário já autorizou e o aparelho continua o mesmo."""
        with self._lock:
            if not self._granted:
                return False

            # Expirado por tempo?
            if self.session_ttl and (time.time() - self._granted_at) > self.session_ttl:
                self._revoke_locked()
                return False

            # Trocou de aparelho? Exige nova autorização — mas só quando
            # ambos os seriais são conhecidos (evita revogar à toa quando
            # o aparelho ainda não foi identificado).
            if device_serial and self._device_serial and device_serial != self._device_serial:
                self._revoke_locked()
                return False

            return True

    def revoke(self):
        """Revoga a autorização (chamado quando o celular desconecta)."""
        with self._lock:
            self._revoke_locked()

    def _revoke_locked(self):
        self._granted = False
        self._granted_at = 0.0
        self._device_serial = ""

    def grant(self, device_serial=""):
        """Marca o acesso como autorizado para a sessão atual."""
        with self._lock:
            self._granted = True
            self._granted_at = time.time()
            self._device_serial = device_serial or self._device_serial

    # ==========================================================
    # AÇÃO PENDENTE
    # ==========================================================

    def set_pending(self, action, device_serial=""):
        """Guarda o comando que aguarda autenticação."""
        with self._lock:
            self._pending_action = {
                "action": action,
                "device_serial": device_serial,
                "created_at": time.time(),
            }

    def take_pending(self):
        """Retira (e limpa) a ação pendente. Expira após 3 minutos."""
        with self._lock:
            pending = self._pending_action
            self._pending_action = None
            if not pending:
                return None
            if (time.time() - pending.get("created_at", 0)) > 180:
                return None
            return pending

    def has_pending(self) -> bool:
        with self._lock:
            return self._pending_action is not None

    # ==========================================================
    # AUTENTICAÇÃO
    # ==========================================================

    def challenge_message(self, what="usar o celular") -> str:
        """Mensagem falada pedindo a autenticação."""
        return (
            f"Para {what}, preciso confirmar que é você: olhe para a câmera "
            "e diga seu PIN da VEGA."
        )

    def authenticate(self, pin) -> tuple:
        """Valida PIN e rosto. Retorna (autorizado, mensagem).

        A ordem importa: o PIN é sempre exigido. O rosto é checado quando a
        câmera está disponível e serve como confirmação adicional — se a
        câmera existir e o rosto não bater, a ação é bloqueada.
        """
        if not self.security.has_pin():
            return False, (
                "Nenhum PIN está configurado na VEGA. Configure um PIN antes "
                "de liberar o controle do celular."
            )

        if not self.security.verify(pin):
            return False, "PIN incorreto. O controle do celular continua bloqueado."

        face_supported, face_valid, evidence_path = self.security.capture_face_validation()

        if face_supported and face_valid is False:
            return False, (
                "A validação facial não confirmou você. O controle do celular "
                "continua bloqueado."
            )

        self.grant()

        if face_supported and face_valid:
            return True, "Identidade confirmada com PIN e reconhecimento facial."
        return True, "PIN confirmado. Controle do celular liberado nesta sessão."