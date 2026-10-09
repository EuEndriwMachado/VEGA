# -*- coding: utf-8 -*-
"""Testes de dia a dia da conectividade — NENHUMA ação real é executada.

Tudo passa por `parser.interpret` (dry-run) ou por conectores com runner
falso. O objetivo é simular um dia normal de uso e achar onde a VEGA
trava, responde errado ou confunde dispositivo.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8")

from core.brain.brain import VegaBrain

brain = VegaBrain()
parser = brain.commands_parser

DIA = [
    # --- manhã: TV ---
    ("bom dia vega", "diálogo"),
    ("abre o youtube na tv", "TV: app"),
    ("pesquisa lofi no youtube da tv", "TV: busca"),
    ("coloca o volume da tv em 30%", "TV: volume"),
    ("pausa a tv", "TV: pause"),
    ("retoma a tv", "TV: resume"),
    # --- tarde: celular ---
    ("manda mensagem pro joao no whatsapp", "celular: msg"),
    ("espelha a tela do celular", "celular: mirror"),
    ("tira um print do celular", "celular: print"),
    ("onde esta meu celular", "celular: locate"),
    # --- noite: TV de novo ---
    ("joga um documentario de oceanos na tv", "TV: youtube"),
    ("aumenta o volume da tv", "TV: volume+"),
    ("desliga a tv", "TV: off"),
    # --- PC: nunca pode ir para TV/celular ---
    ("abre o chrome", "PC: app"),
    ("aumenta o volume", "PC: volume"),
    ("desliga o pc", "PC: shutdown (DEVE ser só interpretado!)"),
]

print("=" * 72)
for fala, esperado in DIA:
    matched, resposta = parser.interpret(fala)
    rota = parser._resolve_connectivity_request(parser.normalize(fala))
    if not matched:
        print(f"[SEM COMANDO ] {fala!r:46s} -> vai para a IA / fluxo normal")
    else:
        print(f"[{esperado:38s}] {fala!r:34s} -> {str(resposta)[:60]!r}")
print("=" * 72)
print("Nenhuma ação real foi executada (dry-run).")
