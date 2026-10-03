# VEGA

Assistente pessoal com interface holográfica, captura e análise visual por
câmera e voz. A lógica de conversa, comandos e modos fica em `core/logic`, a
câmera em `core/vision` e a configuração em `config/`.

## Configuração

Toda credencial é lida de variáveis de ambiente — nenhuma chave deve ficar no
código ou em arquivos versionados.

1. Copie o exemplo e preencha os valores:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Edite o `.env` e defina ao menos um provedor de IA completo (chave + modelo),
   por exemplo:

   ```
   GEMINI_API_KEY=...        # https://aistudio.google.com/app/apikey
   GEMINI_MODEL_NAME=gemini-3-pro-preview
   ```

Em uma máquina nova com Python instalado, execute `setup_vega.bat` uma vez.
Ele cria `.venv` e instala `requirements.txt`.

### Variáveis principais

| Variável | Descrição |
| --- | --- |
| `GEMINI_API_KEY` | Chave da API do Google Gemini (obrigatória se usar Gemini). |
| `GEMINI_MODEL_NAME` / `GEMINI_FALLBACK_MODEL` / `GEMINI_LIVE_MODEL` | Modelos Gemini de chat, fallback e modo live. |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` / `OPENROUTER_FALLBACK_MODEL` | Provedor OpenRouter. |
| `OPENAI_API_KEY` / `OPENAI_MODEL_NAME` / `OPENAI_FALLBACK_MODEL` | Provedor OpenAI. |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `OLLAMA_FALLBACK_MODEL` | Ollama local. |
| `GROQ_API_KEY` | Chave da API Groq. |
| `EDGE_TTS_VOICE` | Voz usada pela síntese Microsoft Edge TTS. |

Ordem de preferência dos provedores: OpenRouter → OpenAI → Gemini. Se nenhum
provedor estiver completo, `config.ai.AI_SETTINGS.require_any_provider()` levanta
`ConfigurationError` no startup, deixando o erro explícito.

Para ativar a voz, use `EDGE_TTS_VOICE`. O padrão é
`pt-BR-FranciscaNeural` e não exige chave de API.

### Ativação sem microfone

Em computadores sem microfone ou câmera, inicie com:

```powershell
python vega.py --text
```

Também é possível definir `VEGA_START_ACTIVE=1`. A interface abre ativa para
mensagens escritas; `Ctrl+V` aceita uma imagem copiada quando o backend Gemini
estiver configurado.

O modo Live já contém captura de câmera e captura da tela via `mss`, mas ambos
dependem de hardware/permissão local e de `GEMINI_API_KEY` + `GEMINI_LIVE_MODEL`.
O envio de imagens coladas pela caixa de texto também é suportado pelo Gemini.
OpenAI, OpenRouter e Ollama continuam recebendo texto; a análise de imagem não
é encaminhada automaticamente para esses backends.

A câmera local é independente do modo Live. Ela pode ser ligada pela interface
ou por comando de voz e fornece captura e pré-visualização locais sem exigir
uma sessão Live ou uma chave do Gemini.

### Arquivos de editor (Continue)

Não coloque chaves diretamente em `config.yaml`. Referencie a variável de
ambiente:

```yaml
models:
  - name: Gemini 3 Pro Preview
    provider: gemini
    model: gemini-3-pro-preview
    apiKey: ${{ secrets.GEMINI_API_KEY }}
```

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

## Proteção de arquivos pessoais

Por padrão, a VEGA pode criar arquivos novos, mas não pode sobrescrever nem
excluir arquivos pessoais. Essa trava não tem comando de voz: ela só pode ser
alterada no código, em `config/file_access.py`.

```python
# Modo seguro (padrão): cria, mas não altera nem exclui.
ALLOW_PERSONAL_FILE_CHANGES = False

# Libera alteração e exclusão de arquivos pessoais.
ALLOW_PERSONAL_FILE_CHANGES = True
```

Reinicie a VEGA após mudar a chave. A edição de registros pela interface do
banco de dados é independente desta proteção.

## Visualizar os dados

O contexto técnico interligado do projeto fica em
`docs/knowledge/README.md`. Ele pode ser aberto como um vault no Obsidian ou
fornecido como contexto local ao Claude Code.

Para abrir uma janela com resumo, memórias e eventos diários persistidos:

```powershell
python database/viewer.py
```

O visualizador usa os JSONs atuais por padrão. Depois de configurar e migrar o
MySQL, abra a mesma interface com:

```powershell
python database/viewer.py --source mysql
```

Na aba **Memórias**, é possível criar, editar e excluir registros. Na aba
**Eventos diários**, é possível registrar novos acontecimentos com data, papel
e conteúdo. No modo MySQL, essas ações usam as tabelas `memories` e
`daily_events`; no modo padrão, atualizam os JSONs de `data/memory`.

O resumo também mostra dias registrados, importância média, distribuição por
tipo e o registro mais recente. A aba **Segundo cérebro** transforma os dados
em um grafo visual. Para abri-la pela VEGA, diga uma frase como:

```text
VEGA, deixa eu ver seu cérebro
```

Também são aceitas formas como `mostra a rede neural da Vega` e `quero
visualizar a mente dela`.

Para abrir diretamente o visualizador conectado ao MySQL por voz, use, por
exemplo:

```text
VEGA, abre o banco de dados da VEGA
VEGA, me mostre seu banco de dados
```
