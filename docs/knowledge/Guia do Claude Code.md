# Guia do Claude Code

Este cérebro pode ser ingerido por um agente de código como contexto local.

## Entrada recomendada

Comece por `docs/knowledge/README.md` e siga os links relevantes antes de
alterar o projeto. Para uma tarefa de memória, leia [[Memoria e contexto]] e
[[Arquitetura]]. Para uma tarefa de interface, leia [[Interface e segundo cerebro]]
e [[Regras de desenvolvimento]].

## Comandos úteis

```powershell
Get-ChildItem docs/knowledge -File
Get-Content "docs/knowledge/README.md"
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

## Regra de contexto

Use estas notas como orientação, mas trate o código e os testes atuais como a
fonte final da verdade. Quando encontrar uma decisão nova e estável, documente-a
em uma nota existente ou crie uma nota relacionada.

## Integração futura

Um arquivo de instruções do Claude Code pode apontar para este índice. A base
continua simples de copiar, versionar, pesquisar e abrir como um vault no
Obsidian.

Veja [[Visao geral]], [[Arquitetura]] e [[Testes e validacao]].
