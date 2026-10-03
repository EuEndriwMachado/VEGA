# Estrutura do projeto VEGA

Mapa das pastas e onde cada coisa vive. Mantenha código, dados e saídas de
execução nos seus domínios; a raiz fica reservada à inicialização e configuração.

```
VEGA/
├── vega.py, run_vega.bat                  # Aplicação e inicialização
├── setup_vega.py, setup_vega.bat           # Preparação do ambiente
│
├── config/              # Configuração, personalidade e interface
├── environment/         # .env, modelo, dependências e regras locais do Git
├── core/
│   ├── brain/           # Cérebro, IA, conversa e orquestração de agentes
│   ├── memory/          # Memória e consultas a registros
│   ├── security/        # PIN, proteção de arquivos e validação facial
│   ├── actions/         # Comandos, tarefas, modos e integração com Windows
│   ├── audio/           # Voz, ativação e detecção sonora
│   └── vision/          # Câmera e percepção visual
│
├── data/                # Memórias, segurança e evidências persistentes
├── database/            # Schema, visualizador e migração para MySQL
├── docs/
│   ├── project/         # README, segurança e este mapa
│   ├── knowledge/       # Base de conhecimento técnica
│   ├── exemplos/        # Código e materiais de referência
│   ├── ollama/          # Configuração do modelo local
│   └── model_limits.md
├── launchers/           # Atalhos do Windows
├── logs/                # Registros de inicialização e execução
├── models/              # Cascades Haar usados pela visão
├── tests/               # Testes automatizados
│   └── manual/          # Diagnósticos que exigem hardware ou execução manual
├── tools/               # Integrações e ferramentas disponíveis para o cérebro
│   └── diagnostics/     # Diagnósticos locais
└── Uploads/             # Imagens e áudio usados pela interface
```

## Regras rápidas

- **Raiz enxuta**: mantenha nela apenas `vega.py`, `run_vega.bat`,
  `setup_vega.py` e `setup_vega.bat`.
- **Ambiente**: segredos e dependências ficam em `environment/`.
- **Segredos só no `.env`** (ou referenciados via `${{ secrets.* }}`). Nunca em
  arquivos versionados nem em `.txt` de backup.
- **Backups (`.bak`) e cópias (`copia`, `copy`)** não ficam no projeto — use Git.
- **Diagnósticos** ficam em `tools/diagnostics/`; testes que exigem execução
  manual ou hardware ficam em `tests/manual/`.
- **Código de exemplo/referência** vai para `docs/exemplos/`.
- **Um domínio por pacote** dentro de `core/`; use os grupos existentes antes
  de criar uma nova pasta.

## Rodar

Execute os comandos a partir da raiz do projeto:

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
