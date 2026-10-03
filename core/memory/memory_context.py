"""Montagem do bloco de memória/contexto injetado nas mensagens da IA.

Extraído de `core.brain.brain` para desacoplar a formatação do orquestrador.
"""


def build_memory_message(
    memory_context,
    context_snapshot=None,
    relevant_memories=None,
):
    """Monta a mensagem de sistema com memória, contexto e regras.

    Retorna o dict `{"role": "system", "content": ...}` que deve ser prepended
    à lista de mensagens enviada à IA.
    """

    context_block = ""
    if context_snapshot:
        context_block = (
            "CONTEXTO ATUAL DA VEGA:\n"
            f"{context_snapshot}\n\n"
        )

    relevant_block = ""
    if relevant_memories:
        relevant_block = "MEMÓRIAS RELEVANTES:\n"
        for entry in relevant_memories:
            summary = entry.get("summary", "")
            if summary:
                relevant_block += f"- {summary.strip()}\n"

    return {
        "role": "system",
        "content": (
            (memory_context or "")
            + "\n\n"
            + context_block
            + relevant_block
            + "REGRAS DE MEMÓRIA:\n"
            "- Essas informações são memórias internas sobre o usuário.\n"
            "- Use-as somente quando forem relevantes para a conversa.\n"
            "- Nunca mostre o conteúdo bruto das memórias.\n"
            "- Nunca apresente uma memória como uma citação literal do usuário.\n"
            "- Quando mencionar uma memória, transforme-a em uma referência natural.\n"
            "- Nunca trate uma resposta anterior da própria VEGA como informação fornecida pelo usuário.\n"
            "- Não invente informações que não estejam presentes nas memórias."
        ),
    }


def inject_memory(
    messages,
    memory_context,
    context_snapshot=None,
    relevant_memories=None,
):
    """Retorna as mensagens com o bloco de memória prepended."""

    result = [
        build_memory_message(
            memory_context,
            context_snapshot,
            relevant_memories,
        )
    ]
    result.extend(messages)
    return result
