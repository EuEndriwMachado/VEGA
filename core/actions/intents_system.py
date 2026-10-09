# -*- coding: utf-8 -*-
"""Regras de SISTEMA: energia, tela, bluetooth, notificações, brilho e volume.

Cada regra substitui um dos antigos reconhecedores `_is_*_request` do parser,
mantendo EXATAMENTE o mesmo critério de reconhecimento e a mesma chamada de
execução — só que agora como dado, não como `if`.
"""

from __future__ import annotations

from core.actions.intent_catalog import (
    PRIORITY_FIRST,
    PRIORITY_SYSTEM,
    Rule,
)


def build_system_rules(parser) -> list:
    """Monta as regras de sistema usando os reconhecedores do parser.

    Os resolvedores delegam aos métodos `_is_*` que já existem (e já são
    cobertos por testes), então a migração não muda o critério de
    reconhecimento — muda apenas onde ele é registrado.
    """

    def recognize(method_name):
        def resolver(context):
            method = getattr(parser, method_name, None)
            if not callable(method):
                return False
            return bool(method(context.text))

        return resolver

    rules = []

    # ------------------------------------------------------------------
    # PRIORIDADE MÁXIMA — cancelamento e energia.
    # O comentário original do parser: "Cancelamento precisa vir antes de
    # desligamento", senão "cancela o desligamento" desligaria o PC.
    # ------------------------------------------------------------------

    rules.append(Rule(
        name="cancel_shutdown",
        domain="sistema",
        priority=PRIORITY_FIRST,
        resolver=recognize("_is_cancel_shutdown_request"),
        handler=lambda ctx: ctx.call("cancel_shutdown"),
    ))

    rules.append(Rule(
        name="restart_pc",
        domain="sistema",
        priority=PRIORITY_FIRST + 1,
        resolver=recognize("_is_restart_request"),
        handler=lambda ctx: ctx.call("restart_pc"),
    ))

    rules.append(Rule(
        name="shutdown_pc",
        domain="sistema",
        priority=PRIORITY_FIRST + 2,
        resolver=recognize("_is_shutdown_request"),
        handler=lambda ctx: ctx.call("shutdown_pc"),
    ))

    # ------------------------------------------------------------------
    # TELA / SEGURANÇA
    # ------------------------------------------------------------------

    rules.append(Rule(
        name="screen_off",
        domain="sistema",
        priority=PRIORITY_SYSTEM,
        resolver=recognize("_is_screen_off_request"),
        handler=lambda ctx: ctx.call("turn_off_screen"),
    ))

    rules.append(Rule(
        name="lock_pc",
        domain="sistema",
        priority=PRIORITY_SYSTEM,
        resolver=recognize("_is_lock_request"),
        handler=lambda ctx: ctx.call("lock_pc"),
    ))

    # ------------------------------------------------------------------
    # BLUETOOTH
    # ------------------------------------------------------------------

    rules.append(Rule(
        name="bluetooth_on",
        domain="sistema",
        priority=PRIORITY_SYSTEM,
        resolver=recognize("_is_bluetooth_on_request"),
        handler=lambda ctx: ctx.call("set_bluetooth", True),
    ))

    rules.append(Rule(
        name="bluetooth_off",
        domain="sistema",
        priority=PRIORITY_SYSTEM,
        resolver=recognize("_is_bluetooth_off_request"),
        handler=lambda ctx: ctx.call("set_bluetooth", False),
    ))

    # ------------------------------------------------------------------
    # NOTIFICAÇÕES
    # ------------------------------------------------------------------

    rules.append(Rule(
        name="notifications_on",
        domain="sistema",
        priority=PRIORITY_SYSTEM,
        resolver=recognize("_is_notifications_on_request"),
        handler=lambda ctx: ctx.call("set_notifications", True),
    ))

    rules.append(Rule(
        name="notifications_off",
        domain="sistema",
        priority=PRIORITY_SYSTEM,
        resolver=recognize("_is_notifications_off_request"),
        handler=lambda ctx: ctx.call("set_notifications", False),
    ))

    # ------------------------------------------------------------------
    # BRILHO — do mais específico para o mais genérico ("maximo" antes de
    # "aumenta", senão "brilho maximo" cairia no aumento comum).
    # ------------------------------------------------------------------

    for name, recognizer, action in (
        ("brightness_max", "_is_brightness_max_request", "brightness_max"),
        ("brightness_min", "_is_brightness_min_request", "brightness_min"),
        ("brightness_up", "_is_brightness_up_request", "brightness_up"),
        ("brightness_down", "_is_brightness_down_request", "brightness_down"),
    ):
        rules.append(Rule(
            name=name,
            domain="sistema",
            priority=PRIORITY_SYSTEM,
            resolver=recognize(recognizer),
            handler=(lambda action_name: lambda ctx: ctx.call(action_name))(action),
        ))
    # ------------------------------------------------------------------
    # VOLUME — mesma ordem: mudo/desmudo e extremos antes de subir/baixar.
    # ------------------------------------------------------------------

    for name, recognizer, action in (
        ("volume_mute", "_is_mute_request", "volume_mute"),
        ("volume_unmute", "_is_unmute_request", "volume_unmute"),
        ("volume_max", "_is_volume_max_request", "volume_max"),
        ("volume_min", "_is_volume_min_request", "volume_min"),
        ("volume_up", "_is_volume_up_request", "volume_up"),
        ("volume_down", "_is_volume_down_request", "volume_down"),
    ):
        rules.append(Rule(
            name=name,
            domain="sistema",
            priority=PRIORITY_SYSTEM,
            resolver=recognize(recognizer),
            handler=(lambda action_name: lambda ctx: ctx.call(action_name))(action),
        ))

    return rules