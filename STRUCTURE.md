# Estrutura do projeto VEGA

Mapa das pastas e onde cada coisa vive. Mantenha código, dados e saídas de
execução em seus domínios; a raiz fica reservada a inicialização e documentação.

```
VEGA/
├── vega.py, run_vega.bat, setup_vega.bat  # Inicialização e instalação
├── .env, .env.example                     # Configuração local e modelo
├── README.md, STRUCTURE.md, SECURITY.md   # Guias do projeto
├── requirements.txt, .gitignore, VEGA.lnk # Dependências e arquivos locais
│
├── config/              # Configuração, personalidade e interface
│   ├── ai.py, settings.py, tooling.py
│   ├── file_access.py, personality.py
│   └── interface.py     # Janela PySide6 e orbe estelar
│
├── core/                # Núcleo funcional da Vega
│   ├── agent_framework.py, conversation.py
│   ├── logic/           # Cérebro, comandos, memória, segurança e modos
│   ├── audio/           # Voz, microfone, wake word e detecção sonora
│   └── vision/          # Câmera e percepção visual
│
├── tools/               # Integrações e ações disponíveis para o cérebro
│   ├── api_*.py, available.py, registry.py
│   ├── monitoring.py, productivity.py, system.py
│   └── diagnostics/     # Diagnósticos de interface e créditos de áudio
│
├── tests/               # Testes automatizados executados pelo pytest
│   └── manual/          # Diagnósticos que exigem hardware ou execução manual
│
├── logs/                # Registros de inicialização e execução
├── data/                # Memórias, segurança e evidências persistentes
│   ├── memory/           # Memória por tipo e registros diários
│   └── security_evidence/
├── database/             # Schema, visualizador e migração para MySQL
├── models/               # Cascades Haar usados pela visão
├── Uploads/              # Imagens e áudio da interface
└── docs/                 # Referências, exemplos e conhecimento do projeto
  ├── exemplos/, ollama/, knowledge/
  └── model_limits.md
```

O Aider pode manter `.aider.chat.history.md` e `.aider.input.history` na raiz.
São históricos locais ignorados pelo Git, não código da Vega; preserve-os se
quiser manter as conversas do Aider.

## Regras rápidas

- **Raiz enxuta**: mantenha nela apenas os arquivos de inicialização,
  configuração, documentação e atalhos listados na árvore.
- **Segredos só no `.env`** (ou referenciados via `${{ secrets.* }}`). Nunca em
  arquivos versionados nem em `.txt` de backup.
- **Backups (`.bak`) e cópias (`copia`, `copy`)** não ficam no projeto — use Git.
- **Diagnósticos** ficam em `tools/diagnostics/`; testes que exigem execução
  manual ou hardware ficam em `tests/manual/`.
- **Código de exemplo/referência** vai para `docs/exemplos/`, não na raiz.
- **Um domínio por pasta** dentro de `core/` (logic, audio, vision). Novo
  domínio = nova subpasta.

## Rodar

```powershell
.\run_vega.bat
```

Ou, manualmente:

```powershell
.\.venv\Scripts\python.exe vega.py
```

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```
