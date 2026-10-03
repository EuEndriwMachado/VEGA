import base64
import json
import re
from html import escape
from urllib.parse import urlparse

from PySide6.QtCore import QByteArray, Qt, QUrl
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)


class VegaResultPanel(QFrame):
    _TITLES = {
        "weather": "Clima",
        "currency": "Câmbio",
        "news": "Notícias",
        "trending_videos": "Vídeos em alta",
        "movie": "Filme ou série",
        "report": "Relatório",
        "geocoding": "Localização",
        "dictionary": "Definição",
        "page_snapshot": "Página monitorada",
    }
    _MAX_IMAGE_BYTES = 4 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("vegaResultPanel")
        self.setStyleSheet(
            "QFrame#vegaResultPanel { background: rgba(4,12,18,0.96);"
            " border: 1px solid rgba(120,190,220,0.48); border-radius: 4px; }"
            "QLabel#resultHeading { color: #e8f8ff; font-size: 16px;"
            " font-weight: 600; background: transparent; border: none; }"
            "QLabel#resultTool { color: rgba(180,220,235,0.65);"
            " font-size: 9px; background: transparent; border: none; }"
            "QPushButton#resultClose { color: #d9f2fa; background: transparent;"
            " border: none; font-size: 20px; min-width: 32px; max-width: 32px;"
            " min-height: 32px; max-height: 32px; }"
            "QPushButton#resultClose:hover { color: #ffffff;"
            " background: rgba(130,210,240,0.18); }"
            "QTextBrowser#resultBody { color: #d4e8ef; background: transparent;"
            " border: none; font-size: 12px; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            "QScrollBar::handle:vertical { background: rgba(130,205,225,0.38);"
            " min-height: 24px; border-radius: 3px; }"
        )
        self.setMinimumSize(260, 180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title_stack = QVBoxLayout()
        title_stack.setContentsMargins(0, 0, 0, 0)
        title_stack.setSpacing(2)
        self.heading = QLabel(self)
        self.heading.setObjectName("resultHeading")
        self.tool_label = QLabel(self)
        self.tool_label.setObjectName("resultTool")
        title_stack.addWidget(self.heading)
        title_stack.addWidget(self.tool_label)
        header.addLayout(title_stack, 1)

        self.close_button = QPushButton("×", self)
        self.close_button.setObjectName("resultClose")
        self.close_button.setToolTip("Fechar resultado")
        self.close_button.clicked.connect(self.hide)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self._layout = layout
        self.video_view = None

        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMaximumHeight(180)
        self.image_label.hide()
        layout.addWidget(self.image_label)

        self.body = QTextBrowser(self)
        self.body.setObjectName("resultBody")
        self.body.setOpenExternalLinks(False)
        self.body.setOpenLinks(False)
        self.body.anchorClicked.connect(self._open_result_link)
        self.body.setReadOnly(True)
        self.body.document().setDefaultFont(QFont("Arial", 9))
        layout.addWidget(self.body, 1)

    @staticmethod
    def _safe_url(value):
        parsed = urlparse(str(value or ""))
        return parsed.geturl() if parsed.scheme in {"http", "https"} and parsed.netloc else ""

    @staticmethod
    def _escape_text(value):
        return escape(str(value))

    @staticmethod
    def _format_value(value):
        if isinstance(value, (dict, list, tuple)):
            text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        else:
            text = str(value)
        return VegaResultPanel._escape_text(text[:1800])

    @classmethod
    def _render_result(cls, tool_name, result):
        if not isinstance(result, dict):
            return f"<p>{escape(str(result))}</p>"

        if tool_name == "news" and isinstance(result.get("items"), list):
            rows = []
            for item in result["items"][:20]:
                if not isinstance(item, dict):
                    continue
                title = cls._escape_text(item.get("title", "Notícia"))
                source = cls._escape_text(item.get("source", ""))
                url = cls._safe_url(item.get("link"))
                if url:
                    title = f'<a href="{escape(url, quote=True)}">{title}</a>'
                rows.append(
                    f'<div class="item"><div class="title">{title}</div>'
                    f'<div class="muted">{source}</div></div>'
                )
            return "".join(rows) or "<p>Nenhuma notícia foi encontrada.</p>"

        if tool_name == "trending_videos" and isinstance(result.get("items"), list):
            rows = []
            for item in result["items"][:1]:
                if not isinstance(item, dict):
                    continue
                video_id = str(item.get("videoId") or "")
                title = cls._escape_text(item.get("title", "Vídeo"))
                author = cls._escape_text(item.get("author", ""))
                views = cls._escape_text(item.get("views", ""))
                if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
                    title = f'<a href="vega-video:/{video_id}">{title}</a>'
                details = " · ".join(value for value in (author, views) if value)
                rows.append(
                    f'<div class="item"><div class="title">{title}</div>'
                    f'<div class="muted">{details}</div></div>'
                )
            return "".join(rows) or "<p>Não encontrei vídeos em alta agora.</p>"

        if tool_name == "weather":
            fields = (
                ("location", "Local"),
                ("condition", "Condição"),
                ("temperature_c", "Temperatura (°C)"),
                ("feels_like_c", "Sensação (°C)"),
                ("humidity", "Umidade (%)"),
                ("wind_kmh", "Vento (km/h)"),
                ("temp_min", "Mínima (°C)"),
                ("temp_max", "Máxima (°C)"),
                ("rain_prob", "Chance de chuva (%)"),
            )
            rows = [
                f"<tr><th>{label}</th><td>{cls._format_value(result[key])}</td></tr>"
                for key, label in fields
                if result.get(key) is not None
            ]
            forecast = result.get("forecast")
            if isinstance(forecast, list):
                forecast_rows = []
                for day in forecast[:7]:
                    if not isinstance(day, dict):
                        continue
                    forecast_rows.append(
                        "<tr>"
                        f"<td>{cls._format_value(day.get('date', ''))}</td>"
                        f"<td>{cls._format_value(day.get('condition', ''))}</td>"
                        f"<td>{cls._format_value(day.get('temperature_min', ''))} / "
                        f"{cls._format_value(day.get('temperature_max', ''))} °C</td>"
                        "</tr>"
                    )
                if forecast_rows:
                    rows.append(
                        '<tr><th colspan="2">Previsão</th><td></td></tr>'
                        + "".join(forecast_rows)
                    )
            return "<table>" + "".join(rows) + "</table>"

        if tool_name == "currency":
            labels = {
                "name": "Par",
                "base": "De",
                "target": "Para",
                "amount": "Valor",
                "rate": "Cotação",
                "converted": "Conversão",
                "high": "Máxima",
                "low": "Mínima",
                "pct_change": "Variação (%)",
                "direction": "Tendência",
            }
            rows = [
                f"<tr><th>{label}</th><td>{cls._format_value(result[key])}</td></tr>"
                for key, label in labels.items()
                if result.get(key) is not None
            ]
            return "<table>" + "".join(rows) + "</table>"

        if tool_name == "movie":
            data = result.get("result")
            if isinstance(data, dict):
                fields = ("Title", "Year", "Rated", "Genre", "Runtime", "Plot", "Actors", "imdbRating")
                rows = [
                    f"<tr><th>{cls._escape_text(key)}</th><td>{cls._format_value(data[key])}</td></tr>"
                    for key in fields
                    if data.get(key) and data[key] != "N/A"
                ]
                poster = cls._safe_url(data.get("Poster"))
                if poster:
                    rows.append(
                        '<tr><th>Imagem</th><td>'
                        f'<a href="{escape(poster, quote=True)}">Abrir pôster</a>'
                        "</td></tr>"
                    )
                return "<table>" + "".join(rows) + "</table>"

        if tool_name == "report" and isinstance(result.get("sections"), dict):
            rows = []
            for title, content in list(result["sections"].items())[:20]:
                rows.append(
                    f"<div class=\"item\"><div class=\"title\">{cls._escape_text(title)}</div>"
                    f"<div>{cls._format_value(content)}</div></div>"
                )
            return "".join(rows) or "<p>O relatório está vazio.</p>"

        rows = []
        for key, value in list(result.items())[:24]:
            if key in {"data", "raw", "image_base64"}:
                continue
            url = cls._safe_url(value) if isinstance(value, str) else ""
            rendered = (
                f'<a href="{escape(url, quote=True)}">{escape(url)}</a>'
                if url
                else cls._format_value(value)
            )
            rows.append(f"<tr><th>{cls._escape_text(key)}</th><td>{rendered}</td></tr>")
        return "<table>" + "".join(rows) + "</table>" if rows else "<p>Resultado recebido.</p>"

    def _set_embedded_image(self, result):
        self.image_label.clear()
        self.image_label.hide()
        if not isinstance(result, dict):
            return
        mime = str(result.get("mimeType", result.get("mime_type", "")))
        encoded = result.get("image_base64", result.get("data"))
        if not mime.startswith("image/") or not isinstance(encoded, str):
            return
        try:
            if encoded.startswith("data:"):
                header, encoded = encoded.split(",", 1)
                data_mime = header[5:].split(";", 1)[0]
                if data_mime.startswith("image/"):
                    mime = data_mime
            if len(encoded) > self._MAX_IMAGE_BYTES * 2:
                return
            data = base64.b64decode(encoded, validate=True)
            if len(data) > self._MAX_IMAGE_BYTES:
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(QByteArray(data)):
                return
            self.image_label.setPixmap(
                pixmap.scaled(
                    340,
                    180,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.image_label.show()
        except (ValueError, TypeError):
            return

    def _open_result_link(self, url):
        if url.scheme() == "vega-video":
            self._play_video(url.path().lstrip("/"))
        elif url.scheme() in {"http", "https"}:
            QDesktopServices.openUrl(url)

    def _play_video(self, video_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", str(video_id or "")):
            video_ids = [
                value for value in video_id
                if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", str(value or ""))
            ] if isinstance(video_id, (list, tuple)) else []
        else:
            video_ids = [str(video_id)]
        if not video_ids:
            return
        if self.video_view is None:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWebEngineCore import QWebEngineSettings

            self.video_view = QWebEngineView(self)
            self.video_view.setFixedHeight(190)
            self.video_view.settings().setAttribute(
                QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,
                False,
            )
            self._layout.insertWidget(2, self.video_view)
        video_ids_json = json.dumps(video_ids[:5])
        self.video_view.setHtml(
            "<!doctype html><html><head>"
            '<meta name="referrer" content="origin">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<style>html,body{margin:0;width:100%;height:100%;background:#000}"
            "#player{border:0;width:100%;height:100%}</style></head><body>"
            '<div id="player"></div><script>'
            f"const videoIds = {video_ids_json};"
            "let activePlayer;"
            "function startVideo(index){"
            "if(index >= videoIds.length){"
            "document.body.innerHTML='<div style=\"color:white;padding:16px;font:14px sans-serif\">Não foi possível reproduzir este vídeo agora.</div>';"
            "return;"
            "}"
            "if(activePlayer){activePlayer.destroy();}"
            "activePlayer=new YT.Player('player',{videoId:videoIds[index],"
            "playerVars:{autoplay:1,playsinline:1,rel:0},"
            "events:{onReady:function(event){event.target.playVideo();},"
            "onError:function(){setTimeout(function(){startVideo(index+1);},150);}}});"
            "}"
            "window.onYouTubeIframeAPIReady=function(){startVideo(0);};"
            "</script><script src="
            '"https://www.youtube.com/iframe_api"></script></body></html>',
            QUrl("https://vega.local/"),
        )
        self.video_view.show()
        parent = self.parentWidget()
        reposition = getattr(parent, "_position_result_panel", None)
        if callable(reposition):
            reposition()

    def show_result(self, tool_name, result):
        if self.video_view is not None:
            self.video_view.stop()
            self.video_view.setUrl(QUrl("about:blank"))
            self.video_view.hide()
        title = self._TITLES.get(tool_name, str(tool_name).replace("_", " ").title())
        self.heading.setText(title)
        self.tool_label.setText("RESULTADO DA VEGA")
        self.body.document().setDefaultStyleSheet(
            "body { color: #d4e8ef; font-family: Arial; font-size: 12px; }"
            "p { margin: 4px 0 9px; }"
            ".item { padding: 8px 0; border-bottom: 1px solid #24404a; }"
            ".title { color: #e9f8ff; font-size: 13px; font-weight: bold; }"
            ".muted, th { color: #8fb7c5; }"
            "table { border-collapse: collapse; width: 100%; }"
            "td, th { padding: 6px 5px; text-align: left;"
            " border-bottom: 1px solid #24404a; vertical-align: top; }"
            "a { color: #a8e4f5; text-decoration: none; }"
            "a:hover { color: #ffffff; text-decoration: underline; }"
        )
        self.body.setHtml("<body>" + self._render_result(tool_name, result) + "</body>")
        self._set_embedded_image(result)
        self.show()
        self.raise_()
        if tool_name == "trending_videos" and result.get("autoplay"):
            items = result.get("items", [])
            video_ids = [
                str(item.get("videoId") or "")
                for item in items
                if isinstance(item, dict)
                and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", str(item.get("videoId") or ""))
            ]
            if video_ids:
                self._play_video(video_ids)
