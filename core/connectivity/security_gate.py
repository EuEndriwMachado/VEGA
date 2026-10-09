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

    #: Quando True, o rosto PRECISA ser reconhecido para liberar (além do
    #: PIN). Padrão False: o PIN autoriza e o rosto é registrado — foi o
    #: comportamento que o usuário pediu. Ligue na instância para exigir o
    #: rosto de novo, sem mexer no código.
    require_face = False

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

            # O aparelho pedido não foi identificado: a autorização da
            # sessão vale (não há como comparar serials).
            if not device_serial:
                return True

            # A autorização foi dada sem saber o aparelho (ex.: o PIN foi
            # informado quando o celular ainda não tinha sido detectado).
            # Nesse caso ADOTA o aparelho atual em vez de exigir o PIN de
            # novo — era isso que fazia a VEGA pedir a senha em loop, logo
            # depois de o usuário tê-la informado corretamente.
            if not self._device_serial:
                self._device_serial = device_serial
                return True

            # Trocou de aparelho? Exige nova autorização.
            if device_serial != self._device_serial:
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

    def set_pending(self, action, device_serial="", evidence_path=None,
                    face_supported=False):
        """Guarda o comando que aguarda autenticação.

        `evidence_path` é a foto tirada no MOMENTO do pedido: ela entra no
        registro de auditoria da tentativa, tenha o PIN acertado ou não.
        """
        with self._lock:
            self._pending_action = {
                "action": action,
                "device_serial": device_serial,
                "created_at": time.time(),
                "evidence_path": evidence_path,
                "face_supported": bool(face_supported),
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
        """Mensagem falada pedindo a autenticação.

        O PIN é sempre numérico, então a mensagem deixa isso explícito: era
        comum a pessoa falar um PIN com letras e ele nunca ser aceito, sem
        que a VEGA explicasse o motivo.

        O rosto NÃO é pedido aqui: o reconhecimento facial é só o registro de
        quem acessou (feito em silêncio, na captura de auditoria), não uma
        segunda prova de identidade. Antes a VEGA dizia "olhe para a câmera e
        diga seu PIN", tratando o rosto como validação — não é o que o
        usuário quer.
        """
        if not self.security.has_pin():
            return (
                "Não há PIN configurado na VEGA, então não posso liberar "
                f"{what}. Configure um PIN para usar o celular."
            )
        return (
            f"Para {what}, diga seu PIN da VEGA (só números). "
            "A câmera já registrou quem pediu, em segundo plano."
        )

    def authenticate(self, pin) -> tuple:
        """Autoriza a ação: o PIN decide, o rosto registra.

        Separação de papéis (o que cada parte faz):

        - **PIN**: é a autorização. Se está correto, a ação é liberada.
        - **Rosto/foto**: é o REGISTRO de quem acessou. A foto já foi tirada
          no momento do pedido; aqui só confirmamos se o rosto foi
          reconhecido, para o log e para a mensagem.

        Por que o rosto não bloqueia: uma captura sem rosto detectado
        (cabeça baixa, luz ruim, câmera ocupada) negava o acesso do PRÓPRIO
        dono mesmo com o PIN certo — o usuário digitava a senha e "não
        acontecia nada". O reconhecimento facial serve para saber QUEM
        acessou, não para impedir quem sabe a senha.

        Se você quiser que o rosto volte a ser obrigatório, ligue
        `require_face = True` na instância (ou em `VegaBrain`).
        """
        if not self.security.has_pin():
            return False, (
                "Nenhum PIN está configurado na VEGA. Configure um PIN antes "
                "de liberar o controle do celular."
            )

        if not self.security.verify(pin):
            return False, (
                "PIN incorreto. O controle do celular continua bloqueado. "
                "O PIN é numérico, com pelo menos 4 dígitos."
            )

        # PIN correto. A partir daqui nada mais pode negar a ação por causa
        # da câmera — o rosto só entra como registro/confirmação.
        face_supported = False
        face_valid = None
        try:
            face_supported, face_valid, _evidence = self.security.capture_face_validation()
        except Exception:
            face_supported, face_valid = False, None

        if self.require_face and face_supported and face_valid is False:
            return False, (
                "O rosto não foi reconhecido e a confirmação facial está "
                "obrigatória. O controle do celular continua bloqueado."
            )

        self.grant()

        if face_supported and face_valid:
            return True, "Identidade confirmada com PIN e reconhecimento facial."
        return True, (
            "PIN confirmado. Controle do celular liberado nesta sessão. "
            "(O rosto não foi reconhecido agora, mas a foto ficou registrada.)"
        )