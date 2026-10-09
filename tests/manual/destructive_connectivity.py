# -*- coding: utf-8 -*-
"""Testes destrutivos de conectividade: força erros e verifica resposta.

Cenários que O USUÁRIO VAI FALAR UM DIA e a VEGA precisa tratar sem travar,
sem fingir e sem executar nada perigoso. NENHUMA ação real é executada:
o parser roda via `interpret` (dry-run) e os conectores com runner falso.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8")

from core.brain.brain import VegaBrain
from core.connectivity.manager import ConnectivityManager
from core.connectivity.registry import DeviceRegistry

brain = VegaBrain()
parser = brain.commands_parser

print("=" * 76)
print("PARTE 1 — falas ambíguas / malformadas (o que a VEGA responde?)")
print("=" * 76)

DESTRUTIVAS = [
    # device sem conteúdo
    ("abre na tv", "TV sem app"),
    ("abre o  na tv", "TV sem app (espaço)"),
    ("toca na tv", "TV sem mídia"),
    ("manda mensagem no whatsapp", "celular sem contato"),
    ("manda mensagem pro  no whatsapp", "celular com contato vazio"),
    ("liga para  no celular", "celular sem contato"),
    ("coloca o volume da tv em", "TV volume sem número"),
    ("coloca o volume da tv em 300%", "TV volume >100%"),
    ("aumenta o volume da tv em 50 por cento", "TV volume + percentual"),
    # dispositivo não citado
    ("desliga", "sem dispositivo"),
    ("pausa", "sem dispositivo"),
    ("espelha a tela", "sem dispositivo"),
    # ruído
    ("vega", "só o nome"),
    ("tv", "só 'tv'"),
    ("celular", "só 'celular'"),
]

for fala, nota in DESTRUTIVAS:
    matched, resposta = parser.interpret(fala)
    if matched:
        print(f"[{nota:26s}] {fala!r:42s} -> {str(resposta)[:56]!r}")
    else:
        print(f"[{nota:26s}] {fala!r:42s} -> (IA / fluxo normal)")

print()
print("=" * 76)
print("PARTE 2 — conectores offline / indisponíveis (falha graciosa?)")
print("=" * 76)

registry = DeviceRegistry(path=os.path.join(os.environ.get("TEMP", "."), "vega_destrutivo.json"))
manager = ConnectivityManager(registry=registry)

# Celular: nenhum conectado.
OFFLINE = [
    ("phone", "send_message", {"contact": "joao", "text": "oi"}),
    ("phone", "send_whatsapp", {"contact": "joao"}),
    ("phone", "call", {"contact": "rita"}),
    ("phone", "screenshot", {}),
    ("phone", "mirror", {}),
    ("phone", "battery", {}),
    ("tv", "play_youtube", {"query": "lofi"}),
    ("tv", "pause", {}),
    ("tv", "volume", {"level": 0.4}),
    ("tv", "open_app", {"app": "youtube"}),
    ("tv", "turn_off", {}),
    ("tv", "play_url", {"url": "https://x.com/v.mp4"}),
]

falha_graciosa = 0
for kind, action, kwargs in OFFLINE:
    try:
        result = manager.route(action, spoken_text=f"{kind} {action}", kind=kind, **kwargs)
        ok = bool(result.message)  # sempre devolve mensagem, nunca levanta
    except Exception as error:
        ok = False
        result = error
    if ok:
        falha_graciosa += 1
    print(f"[{'OK' if ok else 'LANÇOU/VAZIO'}] {kind}.{action:14s} -> {str(result)[:64]!r}")

print()
print(f"RESULTADO: {falha_graciosa}/{len(OFFLINE)} respostas graciosas (nenhuma exceção, nenhuma mensagem vazia)")
