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

### Modo cognitivo (experimental)

Por padrão, `VEGA_COGNITIVE_MODE=legacy`: comandos locais, respostas curtas,
segurança, PIN e consultas rápidas continuam no caminho determinístico atual,
sem chamar o modelo só para responder frases prontas.

Para habilitar respostas mais contextuais depois que um comando local conclui,
defina no `environment/.env`:

```env
VEGA_COGNITIVE_MODE=generated
```

Nesse modo, a VEGA executa o comando uma vez no fluxo local e pode fazer uma
chamada adicional ao provedor para narrar o resultado verificado. Isso consome
tokens; não é necessário para usar a VEGA e o padrão continua sendo `legacy`.
Saudações curtas isoladas permanecem locais e gratuitas; com histórico de
conversa, podem receber uma resposta contextual gerada. Hora/data, desafios de
segurança, PIN e confirmações de fechar aplicativo não passam por essa narração.
Se o modelo estiver indisponível, uma ação já executada conserva seu resultado
factual local.

A configuração é lida em runtime. Reiniciar a VEGA também garante que todos os
componentes iniciem com o mesmo modo.

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

## Celular: ligar, responder e ler mensagens

Com o celular conectado por ADB, a VEGA liga, manda mensagem, responde e lê
conversas. O comando não precisa dizer "celular":

```text
vega, liga para rita cassia pelo meu celular
vega, liga para a rita no whatsapp pelo celular
vega, chama o miguel no whatsapp
vega, responde o miguel                       -> lê a última mensagem e propõe a resposta
vega, responde o miguel dizendo que eu vou chegar tarde
vega, o que o miguel me mandou?
vega, le as mensagens do miguel
```

Detalhes de como funciona:

- **Ler mensagens** usa a notificação do contato (o WhatsApp mostra o conteúdo
  na tela de bloqueio). Quando o `dumpsys` só traz uma referência
  (`String [length=34]`), a VEGA abre o app e lê a conversa da tela.
- **Responder sem desbloquear** abre a conversa e toca no botão de mensagem de
  voz. Por isso `wa.me` não é usado: ele exige o aparelho desbloqueado.
- **Ligar pelo WhatsApp** abre a conversa e toca no botão de chamada; se o
  botão não aparecer, a VEGA avisa em vez de afirmar que ligou.
- Todas essas ações passam pelo mesmo gate de segurança do controle do celular:
  PIN da VEGA + reconhecimento facial, uma vez por sessão.

## TV: tocar vídeo, volume e reprodução

A TV é descoberta automaticamente na rede via Chromecast/Google Cast (testada
com uma Samsung Crystal UHD 50", que tem Cast embutido). Depois de descoberta,
ela fica registrada em `data/memory/devices.json` e os comandos são:

```text
vega, joga um vídeo de gatos na tv            -> busca no YouTube e toca no app da TV
vega, toca a música Evidencias na tv
vega, pesquisa lofi no youtube da tv          -> busca no app do YouTube da TV
vega, abre o youtube na tv / abre o spotify na tv
vega, joga https://exemplo.com/video.mp4 na tv
vega, pausa a tv / retoma a tv / para a tv
vega, coloca o volume da tv em 40%
vega, aumenta o volume da tv / abaixa o volume da tv
vega, muta a tv
vega, desliga a tv                            -> KEY_POWER via WebSocket (Samsung)
vega, quais dispositivos
```

Notas de funcionamento:

- **YouTube na TV toca de verdade**: a VEGA busca o vídeo, resolve o ID e
  envia para o app do YouTube da TV (`YouTubeController`), não para uma
  página HTML (que só exibia "transmitindo" sem tocar nada).
- **Apps**: YouTube e Spotify abrem via Cast (são apps Cast nativos).
  Apps nativos Tizen (Netflix, Prime) não podem ser iniciados pelo
  protocolo Cast — a VEGA avisa em vez de fingir que abriu.
- **Desligar (Samsung)**: usa o protocolo do controle remoto (WebSocket,
  porta 8001) para enviar a tecla KEY_POWER — desligamento físico real.
  Na primeira vez a TV mostra um popup pedindo permissão: aceite "Permitir"
  na tela da TV. O token fica salvo em `data/security/samsung_tv_token.txt`
  e as próximas vezes não pedem autorização.
- O volume da TV é controlado pelo Cast: mudar para 0% equivale a mutar.
- A conexão com a TV fica em cache por alguns segundos entre comandos; é
  isso que mantém a sessão de mídia viva e faz pausar/retomar/parar
  funcionarem de verdade (sem o cache, a sessão sumia e o comando falhava).
- Sem mídia tocando, pausa/retoma avisam que não conseguiram, em vez de
  fingir que pausaram.
- Se a TV não for encontrada, a VEGA pede para verificar se ela está ligada
  e na mesma rede Wi-Fi.

### Regra de ouro: qual dispositivo?

A VEGA nunca confunde os comandos. Quem manda é a fala:

- "aumenta o volume **da tv**" → TV. Sempre.
- "aumenta o volume" (sem menção) → computador.
- "deixa eu ver a tela **do celular**" → celular.
- "pesquisa lofi no youtube **da tv**" → TV; "pesquisa lofi no youtube" → PC.

Testes: `tests/test_device_routing.py` e `tests/test_tv_voice_commands.py`
(verificam o roteamento SEM executar nada, via `parser.interpret`).

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
