import json
import os
from datetime import datetime
from pathlib import Path

from config.environment import load_vega_environment

try:
    import mysql.connector
except ImportError as error:
    raise SystemExit(
        "Instale mysql-connector-python antes da migracao."
    ) from error


ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = ROOT / "data" / "memory"


def load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def insert_memory(cursor, memory_type, key, value, record):
    record = record if isinstance(record, dict) else {}
    cursor.execute(
        """
        INSERT INTO memories
            (memory_type, memory_key, value_text, category, importance,
             confidence, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            value_text = VALUES(value_text),
            category = VALUES(category),
            importance = VALUES(importance),
            confidence = VALUES(confidence),
            source = VALUES(source)
        """,
        (
            memory_type,
            str(key)[:191],
            str(value),
            str(record.get("category", "geral"))[:100],
            float(record.get("importance", 0.5)),
            float(record.get("confidence", 0.5)),
            str(record.get("source", "migration"))[:100],
        ),
    )


def migrate():
    load_vega_environment(ROOT)
    connection = mysql.connector.connect(
        host=os.getenv("VEGA_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("VEGA_DB_PORT", "3306")),
        user=os.getenv("VEGA_DB_USER", "vega"),
        password=os.getenv("VEGA_DB_PASSWORD", ""),
        database=os.getenv("VEGA_DB_NAME", "vega"),
    )
    cursor = connection.cursor()

    permanent = load_json(MEMORY_ROOT / "permanent.json", {})
    for key, record in permanent.items():
        value = record.get("value", "") if isinstance(record, dict) else record
        insert_memory(cursor, "permanent", key, value, record)

    for memory_type in ("selective", "episodic", "semantic", "preferences", "procedural", "errors"):
        filename = "preferences.json" if memory_type == "preferences" else f"{memory_type}.json"
        payload = load_json(MEMORY_ROOT / filename, [])
        if isinstance(payload, list):
            for index, record in enumerate(payload):
                if isinstance(record, dict):
                    value = record.get("content", record.get("value", record))
                else:
                    value = record
                insert_memory(cursor, memory_type.rstrip("s"), f"{filename}:{index}", value, record)

    daily_dir = MEMORY_ROOT / "daily"
    for path in sorted(daily_dir.glob("*.json")):
        payload = load_json(path, [])
        if not isinstance(payload, list):
            continue
        event_date = path.stem
        for record in payload:
            if not isinstance(record, dict):
                continue
            cursor.execute(
                "INSERT INTO daily_events (event_date, role, content) VALUES (%s, %s, %s)",
                (event_date, str(record.get("role", "system"))[:30], str(record.get("content", ""))),
            )

    connection.commit()
    cursor.close()
    connection.close()
    print("Migracao concluida sem remover os arquivos JSON.")


if __name__ == "__main__":
    migrate()
