# Cérebro de Conhecimento da VEGA

Este diretório é a base de contexto humano e técnico da VEGA. As notas usam
links no formato `[[Nome da nota]]`, compatível com Obsidian e fácil de ler pelo
Claude Code.

## Mapa

- [[Visao geral]]: propósito, limites e vocabulário do projeto.
- [[Arquitetura]]: como os módulos se relacionam.
- [[Regras de desenvolvimento]]: convenções para alterar o código.
- [[Memoria e contexto]]: como a VEGA guarda e recupera informações.
- [[Interface e segundo cerebro]]: visualizador, grafo e comandos associados.
- [[Testes e validacao]]: como verificar mudanças.
- [[Guia do Claude Code]]: como ingerir e usar este cérebro.

## Como atualizar

1. Crie uma nota quando surgir um conceito estável do projeto.
2. Adicione pelo menos um link para uma nota relacionada.
3. Atualize este índice quando criar uma nova área.
4. Não coloque segredos, chaves de API ou dados pessoais aqui.

## Estado

- Última revisão: 2026-09-19.
- Fonte principal do comportamento: código em `core/`, `config/`, `tools/` e `database/`.
- Memórias runtime ficam em `data/memory/`, não neste diretório.
