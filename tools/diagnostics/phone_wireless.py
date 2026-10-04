# -*- coding: utf-8 -*-
"""Diagnóstico e pareamento do celular sem fio (ADB over Wi-Fi).

Uso:
    python tools/diagnostics/phone_wireless.py            # mostra o estado
    python tools/diagnostics/phone_wireless.py pair       # guia o pareamento
    python tools/diagnostics/phone_wireless.py connect    # reconecta salvos

O modo `pair` conduz o pareamento passo a passo: pede o IP:porta e o
código de 6 dígitos que aparecem na tela do celular.
"""

import os
import sys

# Permite rodar direto de tools/diagnostics/ sem instalar o pacote.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.connectivity import DeviceRegistry, PhoneConnector, WirelessADB  # noqa: E402


def show_status():
    connector = PhoneConnector()
    wireless = WirelessADB(registry=DeviceRegistry())

    print("=" * 58)
    print("  VEGA — Diagnóstico do celular sem fio")
    print("=" * 58)

    adb = connector._find_adb()
    print(f"\nADB instalado ....... {'sim' if adb else 'NAO'}")
    if adb:
        print(f"  caminho ........... {adb}")

    scrcpy = connector._find_scrcpy()
    print(f"scrcpy instalado .... {'sim' if scrcpy else 'NAO'}")
    if scrcpy:
        print(f"  caminho ........... {scrcpy}")

    devices = wireless.connected_devices()
    print(f"\nDispositivos conectados: {len(devices)}")
    for device in devices:
        modo = "sem fio" if ":" in device else "USB"
        print(f"  - {device}  ({modo})")

    registry = DeviceRegistry()
    known = registry.by_kind("phone")
    print(f"\nCelulares salvos: {len(known)}")
    for device in known:
        wireless_flag = "sem fio" if device.metadata.get("wireless") else "USB"
        print(f"  - {device.name}  [{wireless_flag}]  {device.address}")

    print("\n" + "-" * 58)
    if devices:
        print("Pronto! O celular esta conectado.")
    else:
        print("Nenhum celular conectado agora.")
        print("Para parear sem fio:  python tools/diagnostics/phone_wireless.py pair")
    print("-" * 58)


def do_pair():
    wireless = WirelessADB(registry=DeviceRegistry())

    print("=" * 58)
    print("  Pareamento sem fio")
    print("=" * 58)
    print()
    print("No celular, siga estes passos:")
    print("  1. Configuracoes > Opcoes do desenvolvedor")
    print("  2. Depuracao sem fio")
    print("  3. 'Parear dispositivo com codigo de pareamento'")
    print()
    print("A tela vai mostrar algo como:")
    print("   IP:porta:  192.168.15.4:37000")
    print("   Codigo:    123456")
    print()

    address = input("Digite o IP:porta do celular: ").strip()
    if not address:
        print("\nCancelado.")
        return

    code = input("Digite o codigo de 6 digitos: ").strip()
    if not code:
        print("\nCancelado.")
        return

    print("\nPareando...")
    ok, message = wireless.pair(address, code)
    print(("OK: " if ok else "FALHOU: ") + message)

    if not ok:
        print("\nDicas:")
        print("  - O IP:porta muda a cada vez que a tela de pareamento abre.")
        print("  - O celular precisa estar na mesma rede Wi-Fi do PC.")
        print("  - Feche e reabra a tela de pareamento para ver o valor atual.")
        return

    host = address.split(":")[0]
    print(f"\nConectando em {host}...")
    ok, message = wireless.connect(host)
    print(("OK: " if ok else "FALHOU: ") + message)

    if ok:
        print("\nPronto! Agora voce pode desconectar o cabo USB.")
        print("Da proxima vez, use: python tools/diagnostics/phone_wireless.py connect")


def do_connect():
    wireless = WirelessADB(registry=DeviceRegistry())
    print("Reconectando celulares sem fio salvos...")
    messages = wireless.reconnect_known()
    if not messages:
        print("Nenhum celular sem fio salvo. Rode 'pair' primeiro.")
        return
    for message in messages:
        print(" - " + message)


def main():
    command = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if command == "pair":
        do_pair()
    elif command == "connect":
        do_connect()
    else:
        show_status()


if __name__ == "__main__":
    main()