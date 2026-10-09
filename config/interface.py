import math
import os
import random
import re
import threading
import time

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from config.result_panel import VegaResultPanel


class VegaMessageInput(QLineEdit):
    image_pasted = Signal(object)

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key.Key_V
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            clipboard = QApplication.clipboard()
            if clipboard.mimeData().hasImage():
                image = clipboard.image()
                if not image.isNull():
                    self.image_pasted.emit(image.copy())
                    if not self.text().strip():
                        self.setText("[imagem anexada]")
                    return
        super().keyPressEvent(event)


class InterfaceVEGA(QWidget):
    text_submitted = Signal(str)
    message_submitted = Signal(str, object)
    tool_result_ready = Signal(str, object)
    tool_result_cleared = Signal()
    # Canvas de desenho ao vivo: sinal thread-safe (mesmo padrão do tool_result_ready).
    canvas_draw_requested = Signal(str, object)
    canvas_add_requested = Signal(object)
    brain_view_requested = Signal()
    camera_toggled = Signal(bool)
    microphone_toggled = Signal(bool)
    # Câmera escolhida no menu (hover no botão de câmera).
    camera_selected = Signal(int)
    # Lista de câmeras pronta (vinda da thread de varredura).
    cameras_loaded = Signal(object)

    _OPEN_PATTERN = re.compile(
        r"^(?:por favor\s+)?(?:pode|poderia|consegue)?\s*"
        r"(?:abri|abrir|abra|abre|inicia|iniciar|inicie|executa|executar|execute)\s+"
        r"(?:o|a|os|as|um|uma)?\s*(.+?)(?:\s+por favor)?$"
    )

    def __init__(self, camera=None):
        super().__init__()
        font_path = os.path.join(
            os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"
        )
        if os.path.isfile(font_path):
            QFontDatabase.addApplicationFont(font_path)
        self.camera = camera
        self.project_mode = False
        self.setWindowTitle("VEGA")
        self.setMinimumSize(800, 600)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setStyleSheet("QWidget { background-color: #010305; }")

        from core.actions.native_windows import NativeWindowController

        self.native_controller = NativeWindowController()
        self.own_hwnd = int(self.winId())
        self._last_window_title = None
        self._pending_message_image = None
        self._audio_level = 0.0
        self._animation_tick = 0
        self._rotation = 0.0
        self._pulse = 0.0
        self._processing = False
        self._speaking = False
        self._smoothed_audio = 0.0
        self._activity = 0.0
        self._visibility_progress = 1.0
        self._opening = False
        self._closing = False
        self._last_animation_time = time.monotonic()
        self._grid_cache = None
        self._status_animation = None
        self._status_fade_animation = None
        self.chat_opacity_animation = None
        self._button_animations = {}
        self._chat_open = False

        golden_angle = math.pi * (3.0 - math.sqrt(5.0))
        particle_count = 1024
        self._particles = []
        for index in range(particle_count):
            y = 1.0 - (index + 0.5) * 2.0 / particle_count
            y += random.uniform(-0.7, 0.7) / particle_count
            y = min(0.999, max(-0.999, y))
            ring_radius = math.sqrt(1.0 - y * y)
            angle = index * golden_angle + random.uniform(-0.025, 0.025)
            self._particles.append(
                (
                    math.cos(angle) * ring_radius,
                    y,
                    math.sin(angle) * ring_radius,
                    random.uniform(0.65, 1.45),
                    random.uniform(0.0, math.tau),
                    random.uniform(0.91, 1.08),
                )
            )
        self._orbits = [
            (
                random.uniform(0.98, 1.24),
                random.uniform(-math.pi / 2, math.pi / 2),
                random.uniform(0.0, math.tau),
                random.choice((-1, 1)) * random.uniform(0.0015, 0.006),
                random.randint(15, 42),
            )
            for _ in range(24)
        ]
        self._create_controls()
        self.result_panel = VegaResultPanel(self)
        self.result_panel.hide()
        self.tool_result_ready.connect(self.show_tool_result)
        self.tool_result_cleared.connect(self.hide_tool_result)
        # Canvas de desenho ao vivo: sinal -> slot (thread-safe).
        self.canvas_draw_requested.connect(self.draw_on_live_canvas)
        self.canvas_add_requested.connect(self.add_canvas_commands)

        # Canvas de desenho ao vivo: widget INDEPENDENTE (janela própria).
        # O orbe permanece desenhado no paintEvent desta classe, intocado.
        self._live_canvas = None   # criado sob demanda (lazy)

        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._animate)
        self.hide()

        if camera is not None:
            self.conectar_camera(camera)

    def _create_controls(self):
        self.controls_panel = QFrame(self)
        self.controls_panel.setObjectName("vegaControlsPanel")
        self.controls_panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.controls_panel.setStyleSheet(
            "QFrame#vegaControlsPanel { background: transparent; border: none; }"
            "QLineEdit { color: #f4fdff; background: rgba(7,18,31,0.86);"
            " border: 1px solid rgba(185,239,255,0.82); border-radius: 16px;"
            " padding: 12px 18px; font-size: 15px; font-weight: 600;"
            " selection-background-color: rgba(124,229,255,0.35); }"
            "QLineEdit:focus { border: 1px solid #f4fdff; }"
            "QPushButton { color: #07121c; background: rgba(226,249,255,0.94);"
            " border: 1px solid rgba(190,242,255,0.98); border-radius: 28px;"
            " font-size: 22px; font-weight: 700;"
            " min-width: 64px; max-width: 64px; min-height: 64px; max-height: 64px;"
            " padding: 0; }"
            "QPushButton:hover { color: #020a10; background: #ffffff;"
            " border-color: #ffffff; }"
            "QPushButton:checked { color: #03111a;"
            " background: rgba(147,226,255,0.98); border: 1px solid #ffffff; }"
            "QPushButton:disabled { color: rgba(170,202,214,0.60);"
            " background: rgba(9,17,22,0.38);"
            " border: 1px solid rgba(110,150,163,0.28); }"
        )

        layout = QVBoxLayout(self.controls_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.status_label = QLabel("VEGA: pronta.", self.controls_panel)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setFixedHeight(24)
        self.status_label.setStyleSheet(
            "QLabel { color: rgba(220,245,255,0.96); background: transparent;"
            " border: none; padding: 0 8px; font-size: 17px;"
            " font-weight: 600; letter-spacing: 0.3px;"
            " qproperty-alignment: AlignCenter; }"
        )
        self.status_label.setFont(QFont("Arial", 9))
        self.status_label.setMinimumHeight(36)
        self.status_label.setMaximumHeight(60)
        self.status_opacity = QGraphicsOpacityEffect(self.status_label)
        self.status_opacity.setOpacity(0.92)
        self.status_label.setGraphicsEffect(self.status_opacity)
        layout.addWidget(self.status_label)

        self.status_fade_timer = QTimer(self)
        self.status_fade_timer.setSingleShot(True)
        self.status_fade_timer.timeout.connect(self._fade_status_out)

        self.text_input = VegaMessageInput(self.controls_panel)
        self.text_input.setPlaceholderText("Fale com a Vega...")
        self.text_input.returnPressed.connect(self._send_text)
        self.text_input.image_pasted.connect(self._receive_pasted_image)
        self.text_input.hide()
        self.text_input.setFixedHeight(0)
        self.text_input_opacity = QGraphicsOpacityEffect(self.text_input)
        self.text_input_opacity.setOpacity(0.0)
        self.text_input.setGraphicsEffect(self.text_input_opacity)
        layout.addWidget(self.text_input)

        buttons_layout = QHBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(22)
        buttons_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(buttons_layout)
        self.buttons_layout = buttons_layout

        assets_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "Uploads",
        )
        self.text_button = self._create_button(
            "✎", "Abrir caixa de texto", os.path.join(assets_dir, "interface_balãodefala.png")
        )
        self.camera_button = self._create_button(
            "◉", "Ativar ou desativar câmera", os.path.join(assets_dir, "interface_camera.png")
        )
        self.microphone_button = self._create_button(
            "◍", "Ativar ou silenciar microfone", os.path.join(assets_dir, "interface_microfone.png")
        )
        self.text_button.clicked.connect(self._toggle_text)
        self.camera_button.toggled.connect(self.camera_toggled.emit)
        self.microphone_button.toggled.connect(self.microphone_toggled.emit)
        # Menu de câmera: aparece ao passar o mouse no botão, permitindo
        # escolher QUAL câmera a VEGA usa. O botão continua com a mesma função
        # (clique = ativa/desativa); o menu é um widget separado, que não toca
        # no orbe.
        self._camera_menu = None
        self._camera_scan_running = False
        self.camera_button.installEventFilter(self)
        buttons_layout.addWidget(self.text_button)
        buttons_layout.addWidget(self.camera_button)
        buttons_layout.addWidget(self.microphone_button)

        self.controls_panel.adjustSize()
        self._position_controls()

    def _create_button(self, fallback, tooltip, icon_path):
        button = QPushButton(fallback, self.controls_panel)
        button.setCheckable(True)
        button.setFixedSize(64, 64)
        button.setToolTip(tooltip)
        icon = QIcon(icon_path) if os.path.isfile(icon_path) else QIcon()
        if not icon.isNull():
            button.setText("")
            button.setIcon(icon)
            button.setIconSize(QSize(38, 38))
        effect = QGraphicsOpacityEffect(button)
        effect.setOpacity(1.0)
        button.setGraphicsEffect(effect)
        button.pressed.connect(lambda: self._animate_button(button, effect))
        return button

    # ------------------------------------------------------------------
    # MENU DE CÂMERA (aparece ao passar o mouse no botão)
    # ------------------------------------------------------------------

    def eventFilter(self, watched, event):
        """Atalho: o botão de câmera abre o painel pelo botão direito.

        O CLIQUE normal continua com a função original (ativar/desativar
        câmera). O painel de escolha abre pelo botão direito — que é um gesto
        explícito e nunca faz a VEGA "travar" esperando o mouse.
        """
        if watched is getattr(self, "camera_button", None):
            if event.type() == QEvent.Type.ContextMenu:
                self._toggle_camera_menu()
                return True
        return super().eventFilter(watched, event)

    def _toggle_camera_menu(self):
        """Abre o menu nativo de câmera.

        Usa `QMenu` em vez de um painel desenhado à mão: o Qt calcula o tamanho
        pelo conteúdo e posiciona sozinho. As tentativas anteriores (Popup,
        janela flutuante, widget filho) ou travavam a VEGA, ou viravam uma
        "barrinha" porque a geometria era medida errado em janela frameless.

        A varredura de câmeras roda em thread separada (abrir cada dispositivo
        levava segundos e congelava a interface).
        """
        try:
            from config.camera_menu import CameraMenu, load_preferred_camera

            if self._camera_menu is None:
                self._camera_menu = CameraMenu(self)
                self._camera_menu.camera_selected.connect(self.camera_selected.emit)
                self._camera_menu.rescan_requested.connect(self._reload_camera_menu)
                self.cameras_loaded.connect(self._apply_camera_list)

            atual = getattr(self, "camera_index", None)
            if atual is None:
                atual = load_preferred_camera()
            self._camera_menu.populate(
                self._cached_camera_devices(),
                current_index=atual,
            )
            self._camera_menu.open_at(self.camera_button)
            self._request_camera_list()
        except Exception as error:
            print(f"[VEGA Interface] Menu de câmera indisponível: {error}")

    def _cached_camera_devices(self):
        """Lista de câmeras já conhecida, sem abrir nenhum dispositivo."""
        try:
            from core.vision.camera import VegaCamera

            cached = getattr(VegaCamera, "_devices_cache", None)
            return [dict(item) for item in cached] if cached else []
        except Exception:
            return []

    def _request_camera_list(self, force=False):
        """Pede a lista de câmeras em SEGUNDO PLANO e entrega por sinal."""
        if getattr(self, "_camera_scan_running", False):
            return
        self._camera_scan_running = True

        def worker():
            try:
                from core.vision.camera import VegaCamera

                if force:
                    VegaCamera.invalidate_camera_cache()
                devices = VegaCamera.list_camera_devices()
            except Exception as error:
                print(f"[VEGA Interface] Falha ao listar câmeras: {error}")
                devices = []
            finally:
                self._camera_scan_running = False
            try:
                self.cameras_loaded.emit(devices)
            except Exception:
                pass

        threading.Thread(
            target=worker, name="VegaCameraScan", daemon=True
        ).start()

    def _apply_camera_list(self, devices):
        """Recebe a lista pronta (na thread da UI) e atualiza o menu aberto."""
        from config.camera_menu import load_preferred_camera

        if self._camera_menu is None or not self._camera_menu.isVisible():
            return
        atual = getattr(self, "camera_index", None)
        if atual is None:
            atual = load_preferred_camera()
        try:
            self._camera_menu.populate(devices, current_index=atual)
            self._camera_menu.open_at(self.camera_button)
        except Exception as error:
            print(f"[VEGA Interface] Falha ao montar o menu de câmera: {error}")

    def _reload_camera_menu(self):
        """"Procurar novamente": descarta o cache e re-escaneia em fundo."""
        self._request_camera_list(force=True)

    def set_camera_index(self, index):
        """Registra qual câmera está selecionada (usado pelo painel)."""
        try:
            self.camera_index = int(index)
        except (TypeError, ValueError):
            self.camera_index = None
        if self._camera_menu is not None:
            self._camera_menu.hide()

    def _position_controls(self):
        if not hasattr(self, "controls_panel"):
            return
        width = min(560, max(390, self.width() - 90))
        input_height = (
            48
            if self.text_input.isVisible() and self.text_input.maximumHeight() > 0
            else 0
        )
        status_height = (
            56
            if self.status_label.isVisible() and self.status_label.text().strip()
            else 0
        )
        button_height = 72 if not self._chat_open else 0
        height = status_height + button_height + input_height + 54
        target_y = max(14, self.height() - height - 26)
        self.controls_panel.setGeometry(
            (self.width() - width) // 2,
            target_y,
            width,
            height,
        )

    def _animate(self):
        now = time.monotonic()
        delta = min(0.05, max(0.001, now - self._last_animation_time))
        self._last_animation_time = now
        self._animation_tick += 1
        audio_alpha = 1.0 - math.exp(-delta / (0.045 if self._audio_level > self._smoothed_audio else 0.14))
        self._smoothed_audio += (self._audio_level - self._smoothed_audio) * audio_alpha
        activity_target = max(
            self._smoothed_audio * 0.86,
            0.82 if self._speaking else 0.0,
            0.48 if self._processing else 0.0,
        )
        activity_alpha = 1.0 - math.exp(
            -delta / (0.08 if activity_target > self._activity else 0.34)
        )
        self._activity += (activity_target - self._activity) * activity_alpha
        self._rotation = (
            self._rotation + delta * (11.25 + self._activity * 36.0)
        ) % 360.0
        self._pulse = (self._pulse + delta * (1.56 + self._activity * 0.95)) % math.tau

        if self._opening:
            self._visibility_progress = min(1.0, self._visibility_progress + delta / 0.58)
            self._opening = self._visibility_progress < 1.0
        elif self._closing:
            self._visibility_progress = max(0.0, self._visibility_progress - delta / 0.38)
            if self._visibility_progress <= 0.0:
                self._closing = False
                self._animation_timer.stop()
                self.hide()
                return
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(1, 3, 5))
        self._draw_grid(painter)
        center_x = self.width() * 0.5
        center_y = self.height() * 0.505
        radius = min(self.width() * 0.135, self.height() * 0.245)
        visibility = self._visibility_progress
        radius *= 0.96 + 0.04 * visibility
        activity = self._activity
        painter.save()
        painter.setOpacity(0.38 + 0.62 * visibility)
        back_orbits, front_orbits = self._build_orbit_paths(
            center_x, center_y, radius, activity
        )
        self._draw_orbits(painter, back_orbits, activity)
        self._draw_energy_sphere(painter, center_x, center_y, radius, activity)
        self._draw_orbits(painter, front_orbits, activity)
        painter.restore()

    def _draw_grid(self, painter):
        if self._grid_cache is None or self._grid_cache.size() != self.size():
            self._grid_cache = QPixmap(self.size())
            self._grid_cache.fill(QColor(1, 3, 5))
            grid_painter = QPainter(self._grid_cache)
            grid_painter.setPen(QPen(QColor(71, 147, 170, 11), 1.0))
            spacing = 48
            for x in range(0, self.width(), spacing):
                grid_painter.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), spacing):
                grid_painter.drawLine(0, y, self.width(), y)
            grid_painter.end()
        painter.drawPixmap(0, 0, self._grid_cache)

    def _project_point(self, x, y, z, center_x, center_y, radius):
        perspective = 1.0 / max(0.55, 1.0 - z * 0.16)
        return (
            center_x + x * radius * perspective,
            center_y + y * radius * perspective * 0.96,
        )

    def _build_orbit_paths(self, center_x, center_y, radius, activity):
        back_paths = []
        front_paths = []
        for ring_radius, tilt, phase, speed, alpha in self._orbits:
            rotation = phase + self._rotation * speed * (1.0 + activity * 1.8)
            cos_rotation, sin_rotation = math.cos(rotation), math.sin(rotation)
            cos_tilt, sin_tilt = math.cos(tilt), math.sin(tilt)
            half_tilt = tilt * 0.55
            cos_half_tilt, sin_half_tilt = math.cos(half_tilt), math.sin(half_tilt)
            points = []
            for step in range(73):
                angle = math.tau * step / 72
                path_variation = (
                    0.016 * math.sin(angle * 3.0 + phase)
                    + 0.009 * math.sin(angle * 5.0 - phase * 1.7)
                )
                local_radius = ring_radius + path_variation
                angle_x, angle_y = math.cos(angle), math.sin(angle)
                local_x = angle_x * local_radius
                local_y = angle_y * local_radius * cos_tilt
                local_z = angle_y * local_radius * sin_tilt
                x = local_x * cos_rotation - local_z * sin_rotation
                z = local_x * sin_rotation + local_z * cos_rotation
                y = local_y * cos_half_tilt - z * sin_half_tilt
                z = local_y * sin_half_tilt + z * cos_half_tilt
                screen_x, screen_y = self._project_point(
                    x, y, z, center_x, center_y, radius
                )
                points.append((screen_x, screen_y, z))

            back_path = QPainterPath()
            front_path = QPainterPath()
            previous_front = None
            for first, second in zip(points, points[1:]):
                is_front = (first[2] + second[2]) >= 0
                path = front_path if is_front else back_path
                if is_front != previous_front:
                    path.moveTo(first[0], first[1])
                path.lineTo(second[0], second[1])
                previous_front = is_front
            color_alpha = int(alpha * (1.2 if activity else 1.0))
            back_paths.append((back_path, color_alpha))
            front_paths.append((front_path, color_alpha))
        return back_paths, front_paths

    @staticmethod
    def _draw_orbits(painter, orbit_paths, activity):
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for path, alpha in orbit_paths:
            color_alpha = min(62, int(alpha * (1.2 if activity else 1.0)))
            painter.setPen(QPen(QColor(110, 177, 239, color_alpha), 0.8))
            painter.drawPath(path)
        painter.restore()

    def _draw_energy_sphere(self, painter, center_x, center_y, radius, activity):
        pulse = 1.0 + math.sin(self._pulse) * 0.008 + activity * 0.018
        radius *= pulse
        glow_radius = radius * (1.58 + activity * 0.12)
        glow = QRadialGradient(center_x, center_y, glow_radius)
        glow.setColorAt(0.0, QColor(225, 244, 255, 84 + int(activity * 42)))
        glow.setColorAt(0.34, QColor(112, 182, 255, 54 + int(activity * 28)))
        glow.setColorAt(0.72, QColor(42, 111, 218, 24 + int(activity * 14)))
        glow.setColorAt(1.0, QColor(4, 40, 90, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(
            int(center_x - glow_radius), int(center_y - glow_radius),
            int(glow_radius * 2), int(glow_radius * 2),
        )

        atmosphere = QRadialGradient(center_x, center_y, radius * 1.05)
        atmosphere.setColorAt(0.0, QColor(153, 215, 255, 194))
        atmosphere.setColorAt(0.38, QColor(111, 184, 255, 222))
        atmosphere.setColorAt(0.78, QColor(81, 151, 255, 178))
        atmosphere.setColorAt(0.94, QColor(39, 105, 213, 20))
        atmosphere.setColorAt(1.0, QColor(39, 105, 213, 0))
        painter.setBrush(atmosphere)
        painter.drawEllipse(
            int(center_x - radius), int(center_y - radius),
            int(radius * 2), int(radius * 2),
        )

        rotation = math.radians(self._rotation)
        cos_y, sin_y = math.cos(rotation), math.sin(rotation)
        cos_x, sin_x = math.cos(rotation * 0.63), math.sin(rotation * 0.63)
        projected = []
        for x, y, z, size, phase, distance in self._particles:
            x *= distance
            y *= distance
            z *= distance
            x, z = x * cos_y - z * sin_y, x * sin_y + z * cos_y
            y, z = y * cos_x - z * sin_x, y * sin_x + z * cos_x
            shimmer = 0.62 + 0.38 * math.sin(phase + self._pulse + z * 2.0)
            projected.append((z, x, y, size, shimmer))

        depth_buckets = 12
        shimmer_buckets = 4
        particle_paths = [
            QPainterPath() for _ in range(depth_buckets * shimmer_buckets)
        ]
        for z, x, y, size, shimmer in projected:
            screen_x, screen_y = self._project_point(
                x, y, z, center_x, center_y, radius
            )
            depth = (z + 1.0) * 0.5
            point_size = size * (1.5 + depth * 1.6)
            depth_index = min(depth_buckets - 1, int(depth * depth_buckets))
            shimmer_index = min(
                shimmer_buckets - 1, int(shimmer * shimmer_buckets)
            )
            path_index = depth_index * shimmer_buckets + shimmer_index
            particle_paths[path_index].addEllipse(
                QRectF(
                    screen_x - point_size / 2,
                    screen_y - point_size / 2,
                    max(1.0, point_size),
                    max(1.0, point_size),
                )
            )

        painter.setPen(Qt.PenStyle.NoPen)
        for depth_index in range(depth_buckets):
            depth = (depth_index + 0.5) / depth_buckets
            for shimmer_index in range(shimmer_buckets):
                path = particle_paths[depth_index * shimmer_buckets + shimmer_index]
                if path.isEmpty():
                    continue
                shimmer = (shimmer_index + 0.5) / shimmer_buckets
                alpha = int((42 + depth * 178) * shimmer * (1.0 + activity * 0.48))
                if depth > 0.82:
                    alpha = int(alpha * 0.72)
                color = QColor(
                    98 + int(depth * 92),
                    167 + int(depth * 61),
                    255,
                    min(255, alpha),
                )
                painter.setBrush(color)
                painter.drawPath(path)

        core_radius = radius * 0.9
        core = QRadialGradient(
            center_x,
            center_y,
            core_radius * 0.44,
        )
        core.setColorAt(0.0, QColor(255, 255, 255, 255))
        core.setColorAt(0.22, QColor(232, 248, 255, 244))
        core.setColorAt(0.6, QColor(180, 220, 255, 185))
        core.setColorAt(0.88, QColor(89, 163, 246, 20))
        core.setColorAt(1.0, QColor(39, 112, 220, 0))
        painter.setBrush(core)
        painter.drawEllipse(
            int(center_x - core_radius), int(center_y - core_radius),
            int(core_radius * 2), int(core_radius * 2),
        )

    def resizeEvent(self, event):
        self._position_controls()
        self._position_result_panel()
        super().resizeEvent(event)

    @Slot(str, object)
    def show_tool_result(self, tool_name, result):
        self.result_panel.show_result(tool_name, result)
        self._position_result_panel()

    @Slot()
    def hide_tool_result(self):
        self.result_panel.hide()

    def _position_result_panel(self):
        if not hasattr(self, "result_panel") or not self.result_panel.isVisible():
            return
        width = min(380, max(280, int(self.width() * 0.32)))
        available_height = max(220, min(480, self.height() - 156))
        self.result_panel.body.document().setTextWidth(width - 34)
        content_height = self.result_panel.body.document().size().height()
        image_height = 190 if self.result_panel.image_label.isVisible() else 0
        video_height = 200 if (
            self.result_panel.video_view is not None
            and self.result_panel.video_view.isVisible()
        ) else 0
        height = max(
            220,
            min(available_height, int(content_height + 92 + image_height + video_height)),
        )
        self.result_panel.setGeometry(
            max(16, self.width() - width - 28),
            max(64, (self.height() - height) // 2),
            width,
            height,
        )

    @Slot()
    def despertar(self):
        self._closing = False
        self._opening = True
        self._visibility_progress = 0.0
        self._last_animation_time = time.monotonic()
        self._animation_timer.start(16)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.push_status_message("VEGA pronta.")

    @Slot(float)
    def atualizar_audio(self, level):
        self._audio_level = min(1.0, max(0.0, float(level)))

    @Slot(bool)
    def set_processing(self, active):
        self._processing = bool(active)

    @Slot(bool)
    def set_speaking(self, active):
        self._speaking = bool(active)

    @Slot(str)
    def executar_comando_holografico(self, command):
        return self.executar_comando(command)

    # ==================================================================
    # CANVAS DE DESENHO AO VIVO (Fase 3)
    # Janela independente; o orbe permanece intocado.
    # ==================================================================

    def get_live_canvas(self):
        """Retorna o painel de desenho, criando-o sob demanda."""
        if self._live_canvas is None:
            from config.live_canvas import LiveCanvasDialog
            self._live_canvas = LiveCanvasDialog()
        return self._live_canvas

    @Slot(str, object)
    def draw_on_live_canvas(self, title, spec):
        """Recebe o plano da IA e desenha animado no painel.

        Chamado pelo cérebro via sinal (thread-safe, como os outros slots).
        """
        try:
            from config.live_canvas import build_commands_from_spec
            commands = build_commands_from_spec(spec if isinstance(spec, dict) else {})
            if not commands:
                return False
            canvas = self.get_live_canvas()
            canvas.start_drawing(title)
            canvas.set_commands(commands)
            return True
        except Exception as error:
            print(f"[VEGA Canvas] Erro ao desenhar: {error}")
            return False

    @Slot(object)
    def add_canvas_commands(self, extra_commands):
        """Adiciona comandos ao desenho em andamento (editar no meio)."""
        try:
            from config.live_canvas import build_commands_from_spec
            if self._live_canvas is None:
                return False
            extra = build_commands_from_spec(extra_commands if isinstance(extra_commands, dict) else {})
            if not extra:
                return False
            self._live_canvas.add_commands(extra)
            return True
        except Exception as error:
            print(f"[VEGA Canvas] Erro ao editar desenho: {error}")
            return False

    def conectar_camera(self, camera, start=False):
        self.camera = camera
        if start and not camera.is_running:
            camera.start()

    def set_camera_state(self, active):
        self.camera_button.setChecked(bool(active))

    def set_microphone_state(self, active):
        self.microphone_button.setChecked(bool(active))

    @Slot(str)
    def push_status_message(self, message):
        text = str(message or "").strip()
        if not text:
            return
        if text.upper().startswith("VEGA:"):
            display = text
        elif text.upper().startswith("[VEGA]"):
            display = "VEGA: " + text[6:].strip()
        else:
            display = "VEGA: " + text
        self.status_label.setText(display)
        self.status_opacity.setOpacity(1.0)
        self.status_fade_timer.stop()
        self.status_fade_timer.start(2500)
        animation = QPropertyAnimation(self.status_opacity, b"opacity", self.status_label)
        animation.setDuration(320)
        animation.setStartValue(0.08)
        animation.setKeyValueAt(0.35, 1.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._status_animation = animation
        animation.start()

    def _fade_status_out(self):
        animation = QPropertyAnimation(self.status_opacity, b"opacity", self.status_label)
        animation.setDuration(500)
        animation.setStartValue(self.status_opacity.opacity())
        animation.setEndValue(0.12)
        animation.setEasingCurve(QEasingCurve.InCubic)
        self._status_fade_animation = animation
        animation.start()

    def _animate_button(self, button, effect):
        animation = QPropertyAnimation(effect, b"opacity", button)
        animation.setDuration(170)
        animation.setStartValue(0.52)
        animation.setKeyValueAt(0.45, 0.82)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._button_animations[button] = animation
        animation.finished.connect(lambda: self._button_animations.pop(button, None))
        animation.start()

    def _toggle_text(self):
        self._set_chat_visible(not self.text_input.isVisible())

    def _set_chat_visible(self, visible):
        self._chat_open = bool(visible)
        if self.chat_opacity_animation is not None:
            self.chat_opacity_animation.stop()
        if visible:
            self._set_buttons_visible(False)
            self.text_input.setFixedHeight(44)
            self.text_input.show()
            self.text_input.setFocus()
            self.text_input_opacity.setOpacity(0.0)
            start, end = 0.0, 1.0
        else:
            self.text_input.clear()
            start, end = self.text_input_opacity.opacity(), 0.0
        self._position_controls()
        animation = QPropertyAnimation(self.text_input_opacity, b"opacity", self.text_input)
        animation.setDuration(180)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.OutCubic if visible else QEasingCurve.InCubic)
        if not visible:
            animation.finished.connect(self._finish_hiding_chat)
        self.chat_opacity_animation = animation
        animation.start()

    def _finish_hiding_chat(self):
        self.text_input.hide()
        self.text_input.setFixedHeight(0)
        self.text_input_opacity.setOpacity(0.0)
        self._set_buttons_visible(True)
        self._position_controls()

    def _set_buttons_visible(self, visible):
        for index in range(self.buttons_layout.count()):
            widget = self.buttons_layout.itemAt(index).widget()
            if widget is not None:
                widget.setVisible(visible)

    def _send_text(self):
        text = self.text_input.text().strip()
        if not text:
            return
        self.message_submitted.emit(text, self._pending_message_image)
        self.text_submitted.emit(text)
        self._pending_message_image = None
        self._set_chat_visible(False)

    def _receive_pasted_image(self, image):
        self._pending_message_image = image

    def executar_comando(self, command):
        text = str(command or "").strip().lower()
        if not text:
            return False
        open_project_phrases = (
            "abrir projeto", "abra projeto", "novo projeto", "criar projeto",
            "abrir ambiente holografico", "abrir ambiente holográfico",
            "iniciar ambiente holografico", "iniciar ambiente holográfico",
        )
        if any(phrase in text for phrase in open_project_phrases):
            self.abrir_projeto_novo()
            return True
        if any(phrase in text for phrase in ("fechar interface", "fechar projeto", "fechar holograma")):
            self.project_mode = False
            self._opening = False
            self._closing = True
            self._animation_timer.start(16)
            return True
        app_name = self._extract_app_name(text)
        if app_name:
            QTimer.singleShot(1500, lambda: self.selecionar_app_por_voz(app_name))
        return False

    _KNOWN_APPS = (
        "chrome", "google chrome", "navegador", "vscode", "vs code",
        "visual studio code", "spotify", "whatsapp", "notepad",
        "bloco de notas", "calculadora", "paint", "cmd", "powershell",
        "explorador", "telegram", "discord", "word", "excel",
    )

    @classmethod
    def _extract_app_name(cls, command):
        text = (command or "").strip()
        if not text or any(word in text for word in ("janela", "projeto", "interface")):
            return None
        match = cls._OPEN_PATTERN.match(text)
        if not match:
            return None
        target = match.group(1).strip().lower()
        if not target:
            return None
        if "site" in target or "página" in target or "pagina" in target:
            return "chrome"
        return target if len(target.split()) <= 3 or any(app in target for app in cls._KNOWN_APPS) else None

    def selecionar_app_por_voz(self, app_name):
        window = self._find_window_by_title(app_name)
        if window is not None:
            self.native_controller.focus(window.hwnd)
            self._last_window_title = window.title
            return
        attempts = getattr(self, "_app_selection_attempts", 0) + 1
        if attempts <= 10:
            self._app_selection_attempts = attempts
            QTimer.singleShot(500, lambda: self.selecionar_app_por_voz(app_name))
        else:
            self._app_selection_attempts = 0

    def _find_window_by_title(self, app_name):
        try:
            windows = self.native_controller.list_windows()
        except Exception:
            return None
        synonyms = {
            "vs code": ("visual studio", "code"),
            "vscode": ("visual studio", "code"),
            "visual studio code": ("visual studio", "code"),
            "chrome": ("chrome", "google chrome"),
            "navegador": ("chrome", "edge", "firefox"),
            "google": ("chrome",),
            "youtube": ("chrome", "youtube"),
        }
        words = [part for part in app_name.split() if len(part) > 2]
        terms = synonyms.get(app_name, tuple(words) or (app_name,))
        matches = []
        for window in windows:
            if window.is_minimized or window.hwnd == self.own_hwnd:
                continue
            title = (window.title or "").lower()
            score = sum(term in title for term in terms)
            if score:
                matches.append((score, window))
        return max(matches, key=lambda item: item[0])[1] if matches else None

    @Slot()
    def abrir_projeto_novo(self):
        self.project_mode = True
        self.setWindowTitle("VEGA")
        self.despertar()
