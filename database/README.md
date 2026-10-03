# Banco da Vega

O banco recomendado e MySQL 8+. O schema separa memoria, conversas e eventos
 diarios:

- `memories`: permanente, seletiva, semantica, episodica, preferencias,
  procedural e erros.
- `conversation_messages`: historico de sessoes.
- `daily_events`: registros diarios importados dos JSONs atuais.
- `schema_metadata`: versao do schema.

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
