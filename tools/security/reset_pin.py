# -*- coding: utf-8 -*-
"""Redefine o PIN da VEGA com segurança.

Uso:
    python tools/security/reset_pin.py

O script pede o PIN novo duas vezes, confirma que o usuário tem acesso
local ao computador (exigindo um comando explícito) e grava o hash.
Nunca imprime o PIN na tela nem o guarda em texto puro.
"""

import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.security.security import VegaSecurity  # noqa: E402


def main():
    security = VegaSecurity()

    print("=" * 58)
    print("  VEGA — Redefinir PIN")
    print("=" * 58)
    print()

    if security.has_pin():
        print("A VEGA ja tem um PIN configurado.")
        print("Para trocar, informe o PIN atual.")
        print("Se voce o esqueceu, use --force (exige acesso local ao PC).")
        print()

        force = "--force" in sys.argv
        current = ""

        if not force:
            current = getpass.getpass("PIN atual: ").strip()
            if not security.verify(current):
                print("\nPIN atual incorreto. Nada foi alterado.")
                print("Se esqueceu o PIN, rode:  python tools/security/reset_pin.py --force")
                return
        else:
            confirm = input("Digite REDEFINIR para continuar: ").strip()
            if confirm != "REDEFINIR":
                print("\nCancelado. Nada foi alterado.")
                return

        print()
    else:
        current = ""

    while True:
        pin = getpass.getpass("Novo PIN (4 a 64 digitos): ").strip()
        if not pin.isdigit() or not (4 <= len(pin) <= 64):
            print("  O PIN precisa ter de 4 a 64 digitos numericos.")
            continue

        again = getpass.getpass("Repita o PIN: ").strip()
        if pin != again:
            print("  Os PINs nao coincidem. Tente de novo.")
            continue

        break

    try:
        if not security.set_pin(pin, current_pin=current if not force else None):
            print("\nNao consegui salvar o PIN. Verifique as permissoes do arquivo.")
            return
    except ValueError as error:
        print(f"\nPIN invalido: {error}")
        return
    except PermissionError as error:
        print(f"\nTroca bloqueada: {error}")
        return

    print()
    print("PIN redefinido com sucesso.")
    print("Ele sera exigido para acoes sensiveis (controle do celular,")
    print("apagar arquivos, esvaziar a lixeira).")
    print()
    print("Dica: para o controle do celular, a VEGA tambem pede")
    print("confirmacao facial quando a camera esta disponivel.")


if __name__ == "__main__":
    main()