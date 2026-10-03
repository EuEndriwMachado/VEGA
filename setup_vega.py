from pathlib import Path
import shutil
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
ENVIRONMENT_DIR = PROJECT_ROOT / "environment"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
REQUIREMENTS_FILE = ENVIRONMENT_DIR / "requirements.txt"
ENV_FILE = ENVIRONMENT_DIR / ".env"
ENV_EXAMPLE = ENVIRONMENT_DIR / ".env.example"


def main():
    if not REQUIREMENTS_FILE.is_file():
        print(f"[VEGA] Arquivo de dependencias nao encontrado: {REQUIREMENTS_FILE}")
        return 1

    if not VENV_PYTHON.is_file():
        print("[VEGA] Criando ambiente virtual...")
        subprocess.run(
            [sys.executable, "-m", "venv", str(PROJECT_ROOT / ".venv")],
            cwd=PROJECT_ROOT,
            check=True,
        )

    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=PROJECT_ROOT,
        check=True,
    )
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
        cwd=PROJECT_ROOT,
        check=True,
    )

    if not ENV_FILE.exists() and ENV_EXAMPLE.is_file():
        shutil.copy2(ENV_EXAMPLE, ENV_FILE)
        print("[VEGA] Criado environment/.env a partir do modelo.")
        print("[VEGA] Preencha a chave e o modelo do provedor de IA antes de iniciar.")

    print("[VEGA] Ambiente pronto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())