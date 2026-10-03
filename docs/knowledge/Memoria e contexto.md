# Memória e contexto

A memória externa da VEGA é diferente dos pesos do modelo de IA. Ela guarda
informações consultáveis sem treinar novamente o modelo.

## Categorias

- **Permanente:** fatos importantes sobre o usuário ou a identidade da VEGA.
- **Seletiva:** candidata a promoção para permanente.
- **Episódica:** experiências e acontecimentos específicos.
- **Semântica:** conceitos e fatos gerais.
- **Preferência:** como o usuário gosta que a VEGA responda ou aja.
- **Procedural:** passos para executar uma tarefa.
- **Erro:** falhas conhecidas e estratégias para evitá-las.

## Armazenamento

O runtime atual usa JSON em `data/memory/`. O schema MySQL em `database/schema.sql`
organiza memórias, mensagens e eventos nas tabelas `memories`,
`conversation_messages` e `daily_events`.

## Consulta

Memórias relevantes são recuperadas por correspondência textual, importância e
confiança. Eventos diários podem ser consultados com formas naturais como
“o que aconteceu ontem?” ou “o que fizemos em 18/09/2026?”.

A manutenção visual está em [[Interface e segundo cerebro]]. A arquitetura do
fluxo está em [[Arquitetura]].
