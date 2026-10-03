@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [VEGA] Python nao encontrado no PATH.
    echo [VEGA] Instale Python 3.11+ em https://www.python.org/downloads/
    pause
    exit /b 1
)

python --version

python setup_vega.py
if errorlevel 1 (
    echo [VEGA] Falha ao preparar o ambiente.
    pause
    exit /b 1
)

echo [VEGA] Ambiente pronto.
echo [VEGA] Para iniciar normalmente: run_vega.bat
echo [VEGA] Sem camera/microfone: run_vega.bat --text
pause
