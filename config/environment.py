from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_vega_environment(project_root=None):
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    environment_file = root / "environment" / ".env"
    if not environment_file.is_file():
        environment_file = root / ".env"
    load_dotenv(environment_file)