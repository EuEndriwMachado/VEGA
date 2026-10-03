# Regras de desenvolvimento

## Mudanças

- Preserve APIs públicas e o estilo local antes de criar abstrações novas.
- Prefira um módulo responsável por uma intenção a muitos `if` espalhados.
- Normalize texto e aceite variações naturais de linguagem.
- Use APIs estruturadas para JSON, SQL e configuração.
- Não misture credenciais com código ou documentação versionada.
- Não altere dados runtime para testar: use diretórios temporários quando possível.

## Interface

- Controles importantes devem ter nomes claros.
- Tooltips devem explicar campos e ações sem poluir a tela.
- Métricas devem aparecer como números quando ajudarem a entender o estado.
- Use componentes e padrões já existentes no PySide6.

## Memória

- Diferencie fato permanente, preferência, evento, conceito e procedimento.
- Não trate uma resposta gerada pela própria VEGA como fato do usuário.
- Só use memórias quando forem relevantes para a conversa.
- Nunca exponha o conteúdo bruto da memória sem necessidade.

Relacionadas: [[Arquitetura]], [[Memoria e contexto]] e [[Interface e segundo cerebro]].
