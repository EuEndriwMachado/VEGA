# Banco da Vega

O banco recomendado e MySQL 8+. O schema separa memoria, conversas e eventos
 diarios:

- `memories`: permanente, seletiva, semantica, episodica, preferencias,
  procedural e erros.
- `conversation_messages`: historico de sessoes.
- `daily_events`: registros diarios importados dos JSONs atuais.
- `schema_metadata`: versao do schema.

## Aba "Seguranca"

O visualizador (`database/viewer.py`) tem uma aba **Seguranca** com a
auditoria das acoes perigosas. Ela le `data/security_audit.json` e mostra,
para cada registro:

| Coluna | Significado |
| --- | --- |
| Quando | Data e hora do evento |
| Evento | `Pedido registrado`, `Negado`, `Autorizado` |
| Acao pedida | O que foi pedido (com o PIN removido) |
| PIN ok | Se o PIN informado estava correto |
| Rosto | `reconhecido`, `nao reconhecido`, `foto tirada` ou `sem foto` |
| Origem | `voz` ou `celular` |
| Autorizado | Se a acao foi executada |
| Foto | Caminho da evidencia (duplo clique abre a imagem) |

Pontos importantes do funcionamento:

- A foto e o registro sao feitos no **momento do pedido**, antes de qualquer
  senha. Assim, mesmo quem erra o PIN deixa rastro.
- O texto gravado nunca contem o PIN: sequencias de 4+ digitos viram
  `[pin removido]`.
- Acoes no celular e em arquivos usam o mesmo log.

## Configuracao

Defina no `.env` local:

```env
VEGA_DB_HOST=127.0.0.1
VEGA_DB_PORT=3306
VEGA_DB_NAME=vega
VEGA_DB_USER=vega
VEGA_DB_PASSWORD=senha_local
```

Nao coloque senha no GitHub. Execute `schema.sql` no MySQL e depois:

```powershell
python database/migrate_json_to_mysql.py
```

A migracao e idempotente e nao apaga os JSONs. O runtime atual continua usando
JSON ate a migracao ser validada e o servidor MySQL ser configurado.
