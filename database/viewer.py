import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

from config.environment import load_vega_environment
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen


MEMORY_FILES = {
    "Permanente": ("permanent.json", "permanent"),
    "Seletiva": ("selective.json", "selective"),
    "Episódica": ("episodic.json", "episodic"),
    "Semântica": ("semantic.json", "semantic"),
    "Preferências": ("preferences.json", "preference"),
    "Procedural": ("procedural.json", "procedural"),
    "Erros": ("errors.json", "error"),
}


class DatabaseReader:
    def __init__(self, root, source="auto"):
        self.root = Path(root)
        self.source = str(source or "auto").lower()
        self.memory_root = self.root / "data" / "memory"
        self.connection = None

    def close(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except Exception:
                pass
            self.connection = None

    def _json(self, filename, default):
        try:
            with (self.memory_root / filename).open(encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default

    def _mysql(self):
        if self.connection is None and self.source in {"mysql", "auto"}:
            try:
                import mysql.connector

                load_vega_environment(self.root)
                self.connection = mysql.connector.connect(
                    host=os.getenv("VEGA_DB_HOST", "127.0.0.1"),
                    port=int(os.getenv("VEGA_DB_PORT", "3306")),
                    database=os.getenv("VEGA_DB_NAME", "vega"),
                    user=os.getenv("VEGA_DB_USER", "vega"),
                    password=os.getenv("VEGA_DB_PASSWORD", ""),
                    connect_timeout=2,
                )
                self.source = "mysql"
            except Exception as error:
                # Servidor MySQL indisponível: chaveia transparentemente para JSON local
                self.source = "json"
                self.connection = None
                return None
        return self.connection

    def memory_rows(self, memory_type=None):
        if self.source in {"mysql", "auto"}:
            connection = self._mysql()
            if connection is not None:
                try:
                    query = (
                        "SELECT memory_type, memory_key, value_text, category, importance, "
                        "confidence, source, updated_at FROM memories"
                    )
                    params = []
                    if memory_type:
                        query += " WHERE memory_type = %s"
                        params.append(memory_type)
                    query += " ORDER BY updated_at DESC"
                    cursor = connection.cursor()
                    cursor.execute(query, params)
                    rows = cursor.fetchall()
                    cursor.close()
                    return rows
                except Exception as error:
                    print(f"[DatabaseReader] Erro ao consultar MySQL: {error}. Usando dados JSON.")
                    self.source = "json"

        rows = []
        selected = [memory_type] if memory_type else [value[1] for value in MEMORY_FILES.values()]
        for label, (filename, stored_type) in MEMORY_FILES.items():
            if stored_type not in selected:
                continue
            payload = self._json(filename, {} if stored_type == "permanent" else [])
            records = payload.items() if isinstance(payload, dict) else enumerate(payload)
            for key, record in records:
                record = record if isinstance(record, dict) else {"value": record}
                rows.append(
                    (
                        stored_type,
                        str(key),
                        str(record.get("value", record.get("content", ""))),
                        str(record.get("category", "geral")),
                        record.get("importance", ""),
                        record.get("confidence", ""),
                        record.get("source", "json"),
                        record.get("updated_at", ""),
                    )
                )
        return rows

    def daily_rows(self):
        if self.source in {"mysql", "auto"}:
            connection = self._mysql()
            if connection is not None:
                try:
                    cursor = connection.cursor()
                    cursor.execute(
                        "SELECT event_date, role, content, created_at "
                        "FROM daily_events ORDER BY event_date DESC, created_at DESC"
                    )
                    rows = cursor.fetchall()
                    cursor.close()
                    return rows
                except Exception as error:
                    print(f"[DatabaseReader] Erro ao consultar eventos MySQL: {error}. Usando dados JSON.")
                    self.source = "json"

        rows = []
        daily_path = self.memory_root / "daily"
        for path in sorted(daily_path.glob("*.json"), reverse=True):
            payload = self._json(Path("daily") / path.name, [])
            for record in payload if isinstance(payload, list) else []:
                if isinstance(record, dict):
                    rows.append((path.stem, record.get("role", ""), record.get("content", ""), ""))
        return rows

    # ==========================================================
    # AUDITORIA DE SEGURANÇA
    # ==========================================================

    def security_rows(self):
        """Registros de segurança: quem pediu ações perigosas e o resultado.

        Lê o mesmo `data/security_audit.json` que a VEGA escreve, então a
        tela mostra pedidos, acertos, PIN errado e rosto não reconhecido.
        """
        log_path = self.root / "data" / "security_audit.json"
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(entries, list):
            return []

        rows = []
        for entry in reversed(entries):
            if not isinstance(entry, dict):
                continue
            event = str(entry.get("event") or "dangerous_action")
            labels = {
                "requested": "Pedido registrado",
                "denied": "Negado",
                "authorized": "Autorizado",
                "dangerous_action": "Ação perigosa",
            }
            rows.append((
                str(entry.get("timestamp", "")),
                labels.get(event, event),
                str(entry.get("action_summary") or entry.get("action") or ""),
                "sim" if entry.get("pin_valid") else "não",
                self._face_label(entry),
                str(entry.get("source") or ""),
                "sim" if entry.get("authorized") else "não",
                str(entry.get("evidence_path") or ""),
            ))
        return rows

    @staticmethod
    def _face_label(entry):
        """Descreve o resultado facial de um registro de segurança."""
        if entry.get("face_valid") is True:
            return "reconhecido"
        if entry.get("face_valid") is False:
            return "não reconhecido"
        if entry.get("face_supported"):
            return "foto tirada"
        return "sem foto"

    def counts(self):
        memory_count = len(self.memory_rows())
        daily_count = len(self.daily_rows())
        if self.source == "mysql":
            connection = self._mysql()
            if connection is not None:
                try:
                    cursor = connection.cursor()
                    cursor.execute("SELECT COUNT(*) FROM conversation_messages")
                    conversation_count = cursor.fetchone()[0]
                    cursor.close()
                    return memory_count, daily_count, conversation_count
                except Exception:
                    pass
        conversation_count = daily_count
        return memory_count, daily_count, conversation_count

    def summary(self):
        memories = self.memory_rows()
        events = self.daily_rows()
        by_type = {}
        importance = []
        for row in memories:
            by_type[row[0]] = by_type.get(row[0], 0) + 1
            try:
                importance.append(float(row[4]))
            except (TypeError, ValueError):
                pass
        dates = {str(row[0]) for row in events}
        return {
            "memories": len(memories),
            "events": len(events),
            "days": len(dates),
            "average_importance": sum(importance) / len(importance) if importance else 0,
            "memory_types": by_type,
            "last_event": str(events[0][0]) if events else "nenhum",
        }

    def save_memory(self, memory_type, key, value, category, importance, confidence, source):
        if self.source == "mysql":
            connection = self._mysql()
            if connection is not None:
                try:
                    cursor = connection.cursor()
                    cursor.execute(
                        """
                        INSERT INTO memories
                            (memory_type, memory_key, value_text, category, importance,
                             confidence, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            value_text = VALUES(value_text), category = VALUES(category),
                            importance = VALUES(importance), confidence = VALUES(confidence),
                            source = VALUES(source)
                        """,
                        (memory_type, key, value, category, importance, confidence, source),
                    )
                    connection.commit()
                    cursor.close()
                    return
                except Exception as error:
                    print(f"[DatabaseReader] Erro ao gravar MySQL: {error}. Gravando no JSON local.")

        filename = next((f for f, stored_type in MEMORY_FILES.values() if stored_type == memory_type), "permanent.json")
        path = self.memory_root / filename
        payload = self._json(filename, {} if memory_type == "permanent" else [])
        record = {
            "value": value,
            "category": category,
            "importance": importance,
            "confidence": confidence,
            "source": source,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        if isinstance(payload, dict):
            payload[key] = record
        else:
            index = int(key) if key.isdigit() and int(key) < len(payload) else None
            if index is None:
                payload.append(record)
            else:
                payload[index] = record
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=4)

    def delete_memory(self, memory_type, key):
        if self.source == "mysql":
            connection = self._mysql()
            if connection is not None:
                try:
                    cursor = connection.cursor()
                    cursor.execute(
                        "DELETE FROM memories WHERE memory_type = %s AND memory_key = %s",
                        (memory_type, key),
                    )
                    connection.commit()
                    cursor.close()
                    return
                except Exception as error:
                    print(f"[DatabaseReader] Erro ao excluir do MySQL: {error}. Excluindo do JSON local.")

        filename = next((f for f, stored_type in MEMORY_FILES.values() if stored_type == memory_type), "permanent.json")
        path = self.memory_root / filename
        payload = self._json(filename, {} if memory_type == "permanent" else [])
        if isinstance(payload, dict):
            payload.pop(key, None)
        elif key.isdigit() and int(key) < len(payload):
            payload.pop(int(key))
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=4)

    def add_daily_event(self, event_date, role, content):
        if self.source == "mysql":
            connection = self._mysql()
            if connection is not None:
                try:
                    cursor = connection.cursor()
                    cursor.execute(
                        "INSERT INTO daily_events (event_date, role, content) VALUES (%s, %s, %s)",
                        (event_date, role, content),
                    )
                    connection.commit()
                    cursor.close()
                    return
                except Exception as error:
                    print(f"[DatabaseReader] Erro ao gravar evento no MySQL: {error}. Gravando no JSON local.")

        path = self.memory_root / "daily" / f"{event_date}.json"
        payload = self._json(Path("daily") / path.name, [])
        if not isinstance(payload, list):
            payload = []
        payload.append({"role": role, "content": content})
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=4)


class MemoryDialog(QDialog):
    def __init__(self, parent, memory_type="permanent", key="", record=None):
        super().__init__(parent)
        record = record or {}
        self.setWindowTitle("Editar memória" if key else "Nova memória")
        layout = QFormLayout(self)
        self.type = QComboBox()
        self.type.setToolTip("Escolha em qual grupo esta informação será guardada.")
        for label, (_, stored_type) in MEMORY_FILES.items():
            self.type.addItem(label, stored_type)
        index = self.type.findData(memory_type)
        if index >= 0:
            self.type.setCurrentIndex(index)
        self.key = QLineEdit(str(key))
        self.key.setToolTip("Nome curto e único para encontrar esta memória depois.")
        self.value = QTextEdit(str(record.get("value", record.get("content", ""))))
        self.value.setToolTip("A informação que a VEGA deve lembrar.")
        self.category = QLineEdit(str(record.get("category", "geral")))
        self.category.setToolTip("Grupo livre para organizar a memória, como trabalho ou família.")
        self.importance = QLineEdit(str(record.get("importance", "0.5")))
        self.importance.setToolTip("De 0 a 1: quanto esta memória é importante.")
        self.confidence = QLineEdit(str(record.get("confidence", "0.5")))
        self.confidence.setToolTip("De 0 a 1: quanto você confia nesta informação.")
        self.source = QLineEdit(str(record.get("source", "manual")))
        self.source.setToolTip("De onde veio a informação, por exemplo manual ou conversa.")
        layout.addRow("Tipo:", self.type)
        layout.addRow("Chave:", self.key)
        layout.addRow("Valor:", self.value)
        layout.addRow("Categoria:", self.category)
        layout.addRow("Importância:", self.importance)
        layout.addRow("Confiança:", self.confidence)
        layout.addRow("Origem:", self.source)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setToolTip("Guardar esta memória")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip("Fechar sem guardar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def data(self):
        return {
            "type": self.type.currentData(),
            "key": self.key.text().strip(),
            "value": self.value.toPlainText().strip(),
            "category": self.category.text().strip() or "geral",
            "importance": float(self.importance.text().replace(",", ".") or 0.5),
            "confidence": float(self.confidence.text().replace(",", ".") or 0.5),
            "source": self.source.text().strip() or "manual",
        }


class EventDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Novo evento diário")
        layout = QFormLayout(self)
        self.date = QLineEdit(datetime.now().strftime("%Y-%m-%d"))
        self.date.setToolTip("Use o formato ano-mês-dia, por exemplo 2026-09-18.")
        self.role = QLineEdit("system")
        self.role.setToolTip("Quem gerou o evento: user, assistant ou system.")
        self.content = QTextEdit()
        self.content.setToolTip("Descreva o que aconteceu neste dia.")
        layout.addRow("Data (AAAA-MM-DD):", self.date)
        layout.addRow("Papel:", self.role)
        layout.addRow("Conteúdo:", self.content)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setToolTip("Guardar este evento")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip("Fechar sem guardar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class BrainGraph(QGraphicsView):
    COLORS = {
        "permanent": "#f5b942",
        "selective": "#8b9cff",
        "episodic": "#65d6a6",
        "semantic": "#5cc8e8",
        "preference": "#e78bd4",
        "procedural": "#f18f6c",
        "error": "#ed6a6a",
        "event": "#a7b7c7",
    }

    def __init__(self, reader):
        self.scene = QGraphicsScene()
        super().__init__(self.scene)
        self.reader = reader
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setStyleSheet("QGraphicsView { border: 0; background: #0c141b; }")
        self.setToolTip("Cada círculo representa uma parte dos dados da VEGA; as linhas mostram suas relações.")
        self.refresh()

    def _node(self, label, x, y, color, radius=62):
        item = self.scene.addEllipse(
            x - radius, y - radius, radius * 2, radius * 2,
            QPen(QColor(color), 2), QBrush(QColor(color).darker(180)),
        )
        text = self.scene.addText(label, QFont("Segoe UI", 9))
        text.setDefaultTextColor(QColor("#e8f1f5"))
        text.setPos(x - text.boundingRect().width() / 2, y - text.boundingRect().height() / 2)
        item.setToolTip(label)
        text.setToolTip(label)
        return x, y

    def _link(self, first, second):
        self.scene.addLine(first[0], first[1], second[0], second[1], QPen(QColor("#385463"), 1))

    def refresh(self):
        self.scene.clear()
        rows = self.reader.memory_rows()
        events = self.reader.daily_rows()
        center = self._node("VEGA\nsegundo cérebro", 510, 300, "#62d4e8", 82)
        type_nodes = {}
        types = sorted({row[0] for row in rows})
        for index, memory_type in enumerate(types):
            angle = (index / max(len(types), 1)) * 6.28318
            point = self._node(
                f"{memory_type}\n{sum(row[0] == memory_type for row in rows)}",
                510 + 245 * math.cos(angle),
                300 + 200 * math.sin(angle),
                self.COLORS.get(memory_type, "#8b9aaa"),
                55,
            )
            type_nodes[memory_type] = point
            self._link(center, point)

        for index, row in enumerate(rows):
            memory_type, key, value = row[:3]
            parent = type_nodes.get(memory_type, center)
            offset = index % 5
            point = self._node(
                str(key)[:24],
                parent[0] + (offset - 2) * 88,
                parent[1] + 82 + (index // 5 % 2) * 68,
                self.COLORS.get(memory_type, "#8b9aaa"),
                34,
            )
            self._link(parent, point)
            self.scene.items()[-1].setToolTip(str(value))

        event_dates = sorted({str(row[0]) for row in events}, reverse=True)[:12]
        for index, event_date in enumerate(event_dates):
            point = self._node(f"evento\n{event_date}", 100 + (index % 6) * 170, 590 + (index // 6) * 100, self.COLORS["event"], 42)
            self._link(center, point)
        self.scene.setSceneRect(0, 0, 1060, 820)


class DatabaseViewer(QMainWindow):
    def __init__(self, root, source):
        super().__init__()
        self.reader = DatabaseReader(root, source)
        self.setWindowTitle("VEGA | Visualizador de dados")
        self.resize(1180, 720)
        self.setStyleSheet(
            "QMainWindow, QWidget { background: #101820; color: #e8f1f5; }"
            "QTableWidget { background: #17232d; gridline-color: #2d4350; }"
            "QHeaderView::section { background: #203440; color: #9de4ed; padding: 6px; }"
            "QPushButton { background: #1d6975; border: 0; padding: 8px 14px; }"
            "QPushButton:hover { background: #278895; }"
            "QComboBox { background: #17232d; padding: 7px; }"
        )
        self._build()
        self.refresh()

    def _build(self):
        container = QWidget()
        root_layout = QVBoxLayout(container)
        toolbar = QHBoxLayout()
        self.status = QLabel()
        self.filter = QComboBox()
        self.filter.setToolTip("Mostra apenas memórias do grupo escolhido.")
        self.filter.addItem("Todas as memórias", "")
        for label, (_, memory_type) in MEMORY_FILES.items():
            self.filter.addItem(label, memory_type)
        self.filter.currentIndexChanged.connect(self.refresh_memories)
        refresh_button = QPushButton("Atualizar dados")
        refresh_button.setToolTip("Ler novamente os arquivos ou o banco MySQL.")
        refresh_button.clicked.connect(self.refresh)
        source_label = QLabel("Fonte de dados:")
        source_label.setToolTip("Indica se a tela está usando JSON ou MySQL.")
        toolbar.addWidget(source_label)
        self.source_display = QLabel(self.reader.source.upper())
        toolbar.addWidget(self.source_display)
        toolbar.addSpacing(20)
        toolbar.addWidget(QLabel("Filtrar memórias:"))
        toolbar.addWidget(self.filter)
        toolbar.addStretch()
        toolbar.addWidget(self.status)
        toolbar.addWidget(refresh_button)
        root_layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.overview = QWidget()
        overview_layout = QFormLayout(self.overview)
        self.memory_count = QLabel()
        self.daily_count = QLabel()
        self.conversation_count = QLabel()
        self.day_count = QLabel()
        self.average_importance = QLabel()
        self.type_counts = QLabel()
        self.last_event = QLabel()
        overview_layout.addRow("Memórias:", self.memory_count)
        overview_layout.addRow("Eventos diários:", self.daily_count)
        overview_layout.addRow("Mensagens/conversas:", self.conversation_count)
        overview_layout.addRow("Dias com registros:", self.day_count)
        overview_layout.addRow("Importância média:", self.average_importance)
        overview_layout.addRow("Memórias por tipo:", self.type_counts)
        overview_layout.addRow("Registro mais recente:", self.last_event)
        overview_layout.addRow("Última leitura:", QLabel(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        self.tabs.addTab(self.overview, "Resumo")
        self.tabs.setTabToolTip(0, "Visão geral da quantidade de dados guardados.")

        self.brain_graph = BrainGraph(self.reader)
        graph_page = QWidget()
        graph_layout = QVBoxLayout(graph_page)
        graph_help = QLabel("Segundo cérebro: círculos maiores são grupos; círculos menores são memórias e dias registrados.")
        graph_help.setToolTip("Passe o mouse sobre um nó de memória para ver seu valor completo.")
        graph_layout.addWidget(graph_help)
        graph_layout.addWidget(self.brain_graph)
        self.tabs.addTab(graph_page, "Segundo cérebro")
        self.tabs.setTabToolTip(1, "Mapa visual das categorias, memórias e eventos da VEGA.")

        self.memories = self._table(
            ["Tipo", "Chave", "Valor", "Categoria", "Importância", "Confiança", "Origem", "Atualizado"]
        )
        memory_page = QWidget()
        memory_layout = QVBoxLayout(memory_page)
        memory_actions = QHBoxLayout()
        new_memory_button = QPushButton("Nova memória")
        new_memory_button.setToolTip("Cadastrar uma informação que a VEGA poderá lembrar.")
        new_memory_button.clicked.connect(self.new_memory)
        edit_memory_button = QPushButton("Editar selecionada")
        edit_memory_button.setToolTip("Alterar a memória atualmente selecionada na tabela.")
        edit_memory_button.clicked.connect(self.edit_memory)
        delete_memory_button = QPushButton("Excluir selecionada")
        delete_memory_button.setToolTip("Apagar a memória selecionada depois de confirmar.")
        delete_memory_button.clicked.connect(self.delete_memory)
        memory_actions.addWidget(new_memory_button)
        memory_actions.addWidget(edit_memory_button)
        memory_actions.addWidget(delete_memory_button)
        memory_actions.addStretch()
        memory_layout.addLayout(memory_actions)
        memory_layout.addWidget(self.memories)
        self.tabs.addTab(memory_page, "Memórias")
        self.tabs.setTabToolTip(2, "Consultar e administrar as memórias da VEGA.")
        self.daily = self._table(["Data", "Papel", "Conteúdo", "Criado em"])
        daily_page = QWidget()
        daily_layout = QVBoxLayout(daily_page)
        new_event_button = QPushButton("Novo evento diário")
        new_event_button.setToolTip("Registrar uma conversa ou acontecimento em uma data.")
        new_event_button.clicked.connect(self.new_event)
        daily_layout.addWidget(new_event_button)
        daily_layout.addWidget(self.daily)
        self.tabs.addTab(daily_page, "Eventos diários")
        self.tabs.setTabToolTip(3, "Consultar e registrar acontecimentos do dia a dia.")

        self.sql_query = QPlainTextEdit(
            "SELECT memory_type, memory_key, value_text, category FROM memories ORDER BY updated_at DESC LIMIT 20;"
        )
        self.sql_query.setPlaceholderText("Digite uma consulta SQL aqui...")
        self.sql_query.setToolTip("Consulta customizada em MySQL. Em modo JSON, as consultas são limitadas ao conjunto local.")
        self.sql_results = self._table(["Coluna", "Valor"])
        sql_page = QWidget()
        sql_layout = QVBoxLayout(sql_page)
        sql_buttons = QHBoxLayout()
        run_sql_button = QPushButton("Executar")
        run_sql_button.clicked.connect(self.run_sql_query)
        clear_sql_button = QPushButton("Limpar")
        clear_sql_button.clicked.connect(self.sql_query.clear)
        sql_buttons.addWidget(run_sql_button)
        sql_buttons.addWidget(clear_sql_button)
        sql_buttons.addStretch()
        sql_layout.addLayout(sql_buttons)
        sql_layout.addWidget(self.sql_query)
        sql_layout.addWidget(self.sql_results)
        self.tabs.addTab(sql_page, "SQL")
        self.tabs.setTabToolTip(4, "Executar consultas SQL personalizadas no banco ativo da VEGA.")

        # ----------------------------------------------------------
        # SEGURANÇA: quem pediu ações perigosas e o que aconteceu
        # ----------------------------------------------------------
        self.security = self._table([
            "Quando", "Evento", "Ação pedida", "PIN ok", "Rosto",
            "Origem", "Autorizado", "Foto",
        ])
        self.security.setToolTip(
            "Cada pedido de ação perigosa fica registrado aqui — inclusive "
            "quando o PIN é errado e nada é executado."
        )
        security_page = QWidget()
        security_layout = QVBoxLayout(security_page)
        security_help = QLabel(
            "Auditoria de segurança: pedidos, tentativas de PIN e se o rosto "
            "foi reconhecido. Duplo clique abre a foto da evidência."
        )
        security_help.setWordWrap(True)
        security_layout.addWidget(security_help)
        security_layout.addWidget(self.security)
        self.security.cellDoubleClicked.connect(self.open_evidence)
        self.tabs.addTab(security_page, "Segurança")
        self.tabs.setTabToolTip(5, "Registro de acessos e tentativas em ações perigosas.")

        root_layout.addWidget(self.tabs)
        self.setCentralWidget(container)

    def _table(self, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _fill(self, table, rows):
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(str(value if value is not None else ""))
                item.setToolTip(str(value if value is not None else ""))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row_index, column_index, item)
        table.resizeColumnsToContents()

    def refresh_memories(self):
        try:
            self._fill(self.memories, self.reader.memory_rows(self.filter.currentData() or None))
        except Exception as error:
            self.status.setText(f"Erro: {error}")

    def _selected_memory(self):
        row = self.memories.currentRow()
        if row < 0:
            QMessageBox.information(self, "Memória", "Selecione uma memória na tabela.")
            return None
        return {
            "type": self.memories.item(row, 0).text(),
            "key": self.memories.item(row, 1).text(),
            "value": self.memories.item(row, 2).text(),
            "category": self.memories.item(row, 3).text(),
            "importance": self.memories.item(row, 4).text(),
            "confidence": self.memories.item(row, 5).text(),
            "source": self.memories.item(row, 6).text(),
        }

    def new_memory(self):
        dialog = MemoryDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            data = dialog.data()
            if not data["key"] or not data["value"]:
                raise ValueError("Chave e valor são obrigatórios.")
            self.reader.save_memory(
                data["type"],
                data["key"],
                data["value"],
                data["category"],
                data["importance"],
                data["confidence"],
                data["source"],
            )
            self.refresh()
            self.status.setText("Memória salva")
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, "Memória inválida", str(error))
        except Exception as error:
            self.status.setText(f"Erro ao salvar: {error}")

    def edit_memory(self):
        selected = self._selected_memory()
        if selected is None:
            return
        dialog = MemoryDialog(self, selected["type"], selected["key"], selected)
        dialog.type.setEnabled(False)
        dialog.key.setEnabled(False)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            data = dialog.data()
            self.reader.save_memory(
                data["type"],
                data["key"],
                data["value"],
                data["category"],
                data["importance"],
                data["confidence"],
                data["source"],
            )
            self.refresh()
            self.status.setText("Memória atualizada")
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, "Memória inválida", str(error))
        except Exception as error:
            self.status.setText(f"Erro ao atualizar: {error}")

    def delete_memory(self):
        selected = self._selected_memory()
        if selected is None:
            return
        answer = QMessageBox.question(
            self,
            "Excluir memória",
            f"Excluir a memória '{selected['key']}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.reader.delete_memory(selected["type"], selected["key"])
            self.refresh()
            self.status.setText("Memória excluída")
        except Exception as error:
            self.status.setText(f"Erro ao excluir: {error}")

    def new_event(self):
        dialog = EventDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            event_date = dialog.date.text().strip()
            role = dialog.role.text().strip() or "system"
            content = dialog.content.toPlainText().strip()
            datetime.strptime(event_date, "%Y-%m-%d")
            if not content:
                raise ValueError("O conteúdo é obrigatório.")
            self.reader.add_daily_event(event_date, role, content)
            self.refresh()
            self.status.setText("Evento salvo")
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, "Evento inválido", str(error))
        except Exception as error:
            self.status.setText(f"Erro ao salvar evento: {error}")

    def run_sql_query(self):
        query = self.sql_query.toPlainText().strip()
        if not query:
            self.status.setText("Digite uma consulta SQL antes de executar.")
            return

        try:
            if self.reader.source in {"mysql", "auto"}:
                connection = self.reader._mysql()
                if connection is None:
                    raise RuntimeError("MySQL indisponível; o visualizador está em modo JSON local.")
                cursor = connection.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                columns = [column[0] for column in cursor.description] if cursor.description else []
                cursor.close()
                if not columns:
                    self._fill(self.sql_results, [["Resultado", "Consulta executada com sucesso"]])
                    self.status.setText("Consulta SQL executada com sucesso.")
                    return
                formatted = [tuple(row) for row in rows]
                table_rows = []
                for row in formatted[:100]:
                    table_rows.append(tuple(str(value) for value in row))
                if not table_rows:
                    table_rows = [["", "Sem linhas retornadas"]]
                self.sql_results.setColumnCount(len(columns))
                self.sql_results.setHorizontalHeaderLabels(columns)
                self.sql_results.setRowCount(len(table_rows))
                for row_index, row in enumerate(table_rows):
                    for column_index, value in enumerate(row):
                        if column_index >= len(columns):
                            continue
                        item = QTableWidgetItem(str(value))
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        self.sql_results.setItem(row_index, column_index, item)
                self.sql_results.resizeColumnsToContents()
                self.status.setText(f"Consulta SQL executada: {len(table_rows)} linha(s) retornadas.")
                return

            self.sql_results.setColumnCount(2)
            self.sql_results.setHorizontalHeaderLabels(["Tabela", "Observação"])
            self.sql_results.setRowCount(1)
            item = QTableWidgetItem("SQL em modo JSON")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sql_results.setItem(0, 0, item)
            self.sql_results.setItem(0, 1, QTableWidgetItem("O modo local usa JSON; conecte um MySQL para consultas SQL livres."))
            self.status.setText("Modo JSON local: use o banco MySQL para consultas SQL customizadas.")
        except Exception as error:
            self.sql_results.setColumnCount(2)
            self.sql_results.setHorizontalHeaderLabels(["Erro", "Detalhe"])
            self.sql_results.setRowCount(1)
            self.sql_results.setItem(0, 0, QTableWidgetItem("Consulta inválida"))
            self.sql_results.setItem(0, 1, QTableWidgetItem(str(error)))
            self.status.setText(f"Erro na consulta SQL: {error}")

    def refresh(self):
        try:
            memory_count, daily_count, conversation_count = self.reader.counts()
            details = self.reader.summary()
            self.memory_count.setText(str(memory_count))
            self.daily_count.setText(str(daily_count))
            self.conversation_count.setText(str(conversation_count))
            self.day_count.setText(str(details["days"]))
            self.average_importance.setText(f"{details['average_importance']:.2f} / 1.00")
            self.type_counts.setText(", ".join(f"{key}: {value}" for key, value in sorted(details["memory_types"].items())) or "nenhuma")
            self.last_event.setText(details["last_event"])
            self._fill(self.memories, self.reader.memory_rows(self.filter.currentData() or None))
            self._fill(self.daily, self.reader.daily_rows())
            self._fill(self.security, self.reader.security_rows())
            self.brain_graph.refresh()
            if hasattr(self, "source_display"):
                self.source_display.setText(self.reader.source.upper())
            self.status.setText("Dados atualizados")
        except Exception as error:
            self.status.setText(f"Não foi possível ler a fonte: {error}")

    def open_evidence(self, row, _column):
        """Abre a foto da evidência daquele registro de segurança."""
        item = self.security.item(row, 7)
        path = item.text() if item is not None else ""
        if not path:
            QMessageBox.information(
                self, "Evidência", "Este registro não tem foto."
            )
            return
        target = Path(path)
        if not target.is_absolute():
            target = self.reader.root / target
        if not target.exists():
            QMessageBox.warning(
                self, "Evidência", f"A foto não foi encontrada em:\n{target}"
            )
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except (OSError, AttributeError) as error:
            QMessageBox.warning(self, "Evidência", f"Não consegui abrir a foto: {error}")

    def closeEvent(self, event):
        self.reader.close()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description="Visualiza os dados persistidos da VEGA")
    parser.add_argument("--source", choices=("json", "mysql", "auto"), default="auto")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    window = DatabaseViewer(Path(__file__).resolve().parents[1], args.source)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
