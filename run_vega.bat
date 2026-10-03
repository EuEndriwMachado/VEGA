@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    call ".venv\Scripts\activate.bat"
    python vega.py %*
) else (
    echo [VEGA] Ambiente virtual nao encontrado em .venv\Scripts\python.exe
    echo [VEGA] Execute setup_vega.bat uma vez para instalar as dependencias.
    pause
)
