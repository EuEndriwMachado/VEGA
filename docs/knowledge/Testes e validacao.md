# Testes e validação

## Comando principal

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

## Teste focado

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_brain_view.py tests/test_daily_query.py -q
```

## Sintaxe

```powershell
python -m py_compile core/logic/brain.py core/logic/daily_query.py database/viewer.py
```

## Princípio

Uma mudança deve ter um teste que possa falhar caso a hipótese esteja errada.
Para persistência, prefira cópias temporárias dos JSONs. Para interface Qt,
use inicialização `offscreen` quando a validação não precisar de uma janela real.

## Captura da câmera

Para testar captura de frames sem depender de hardware:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_camera.py -q
```

Relacionadas: [[Arquitetura]] e [[Guia do Claude Code]].
