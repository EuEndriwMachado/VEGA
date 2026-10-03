# Arquitetura

## Fluxo principal

`vega.py` inicializa a aplicação, conecta `InterfaceVEGA`, `VegaBrain`, voz e
visão. O cérebro recebe texto, normaliza a entrada e tenta resolver intenções
antes de chamar o backend de IA.

```text
Interface -> VegaBrain -> comandos / memória / ferramentas / IA
     |           |              |
   voz       contexto       JSON ou MySQL
     |
 câmera -> captura e análise visual
```

## Áreas

- `config/`: configurações e interface PySide6.
- `core/logic/`: cérebro, memória, comandos, modos e interpretação.
- `core/audio/`: captura, ativação e síntese de voz.
- `core/vision/`: captura da câmera, OCR e análise de faces/objetos.
- `tools/`: APIs e ferramentas externas.
- `database/`: schema, migração e visualizador.
- `data/memory/`: armazenamento JSON gerado em runtime.
- `tests/`: testes automatizados.

## Fluxo de memória

1. `VegaMemory` grava eventos diários e categorias de memória.
2. `build_memory_context` inclui memórias permanentes importantes.
3. `retrieve_relevant` busca categorias relacionadas à pergunta.
4. `memory_context.py` injeta o resultado como contexto interno da IA.
5. `DailyQuery` consulta eventos quando a pergunta contém uma data ou período.

Consulte [[Memoria e contexto]] e [[Testes e validacao]].
