# -*- coding: utf-8 -*-
"""Shared response path for already-completed local actions.

Intent recognition and safety remain local and deterministic; this module only
asks the conversational model to narrate a verified result. If the feature is
disabled, the caller keeps the legacy response. If the model fails or is
cancelled, the original factual result is returned rather than losing the
outcome of an action that already happened.
"""

import re
from config.cognitive import mode_is_generated
SYSTEM_INSTRUCTION = (
    "Você está respondendo após uma ação local. Responda em português brasileiro, "
    "com naturalidade e personalidade. O resultado verificado é a fonte de verdade; "
    "não altere se a ação teve sucesso ou falhou, não invente efeitos nem afirme "
    "que fez algo além do que esse resultado confirma. Use sua própria formulação, "
    "seja concisa e não termine com uma oferta genérica."
)


_FAILURE_PATTERNS = (
    r"não consegui responder agora",
    r"nao consegui responder agora",
    r"openrouter retornou",
    r"resource_exhausted",
    r"rate limit",
    r"\b(?:sem créditos|cota esgotada|quota exceeded)\b",
    r"\b(?:api key|chave).{0,40}\b(?:inválida|invalida|invalid)\b",
    r"\bmodelo.{0,40}\b(?:não está disponível|indisponível|not found)\b",
)


def _is_provider_failure(text):
    value = str(text or "")
    return any(
        re.search(pattern, value, flags=re.IGNORECASE)
        for pattern in _FAILURE_PATTERNS
    )


def narrate_action_result(ai, user_text, result, *, enabled=None):
    """Gera narração para resultado de ação local; mantém fallback fiel.

    Args:
        ai: Adaptador de IA com ``chat_stream``.
        user_text: Pedido original do usuário, preservado para contexto.
        result: Texto factual retornado pelo executor local.
        enabled: Override opcional, usado pelos testes. None lê o modo global.

    Returns:
        Frase final gerada pela IA, ou ``result`` sem alteração se não aplicável.
    """
    factual_result = str(result or "").strip()
    if not factual_result:
        return factual_result
    if enabled is None:
        enabled = mode_is_generated()
    if not enabled or ai is None:
        return factual_result
    messages = [
        {
            "role": "user",
            "content": (
                f"Pedido original: {str(user_text or '').strip()}\n\n"
                f"Resultado verificado da ação:\n{factual_result}"
            ),
        }
    ]
    try:
        stream = ai.chat_stream(
            messages,
            system_prompt=SYSTEM_INSTRUCTION,
            num_predict=160,
            include_screen=False,
        )
        response = "".join(str(chunk) for chunk in stream if chunk is not None).strip()
    except Exception as error:
        print(f"[VEGA Cognição] Narração indisponível; usando resultado factual: {error}")
        return factual_result
    # A provider error or a hallucinated confirmation is not a narration;
    # preserve a verified successful action receipt unless the result says it failed.
    if _is_provider_failure(response):
        return factual_result
    failure_result = re.search(
        r"(?:erro|falha|nao consegui|nao foi possivel|nao encontrei|acesso negado)",
        factual_result,
        flags=re.IGNORECASE,
    )
    local_failure = any(
        phrase in factual_result.lower()
        for phrase in ("nao foi possivel", "nao consegui", "nao encontrei", "acesso negado", "falhou", "erro", "falha")
    )
    if local_failure:
        return factual_result
    success_claim = re.search(
        r"\b(?:pronto|feito|concluido|concluida|abri|aberto|aberta|criado|criada|enviei|enviado|enviada|pausado|pausada|ligado|ligada|salvo|salva|apagado|apagada|excluido|excluida)\b",
        response,
        flags=re.IGNORECASE,
    )
    if success_claim and not failure_result:
        return response
    return response or factual_result
