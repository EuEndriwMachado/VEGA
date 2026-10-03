import os
import base64
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QUrl, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from config.interface import InterfaceVEGA


def test_interface_renders_energy_sphere_and_controls():
    app = QApplication.instance() or QApplication([])
    window = InterfaceVEGA()
    window.resize(1266, 709)
    window.show()
    app.processEvents()

    image = window.grab().toImage()
    center = image.pixelColor(633, int(709 * 0.47))
    buttons = (window.text_button, window.camera_button, window.microphone_button)

    assert (image.width(), image.height()) == (1266, 709)
    assert center.red() > 180 and center.blue() > 200
    assert len(window._particles) == 1024
    assert len(window._orbits) >= 20
    previous_rotation = window._rotation
    window.set_processing(True)
    window._animate()
    assert window._processing is True
    assert window._rotation > previous_rotation + 0.18
    window.set_processing(False)
    window.set_speaking(True)
    window._last_animation_time -= 0.05
    window._animate()
    assert window._speaking is True
    assert window._activity > 0.0
    window.set_speaking(False)
    assert all(
        button.isVisible() and button.width() >= 64 and button.height() >= 64
        for button in buttons
    )
    assert window.controls_panel.geometry().bottom() < image.height()

    window.close()


def test_tool_result_panel_is_optional_and_shows_clickable_news():
    app = QApplication.instance() or QApplication([])
    window = InterfaceVEGA()
    window.resize(1266, 709)
    window.show()
    app.processEvents()

    assert not window.result_panel.isVisible()
    window.show_tool_result(
        "news",
        {
            "items": [
                {
                    "source": "G1",
                    "title": "Notícia de teste",
                    "link": "https://example.com/article",
                }
            ]
        },
    )
    app.processEvents()

    assert window.result_panel.isVisible()
    assert "Notícia de teste" in window.result_panel.body.toPlainText()
    assert "notícia" in window.result_panel.body.toPlainText().casefold()
    assert "https://example.com/article" in window.result_panel.body.toHtml()
    assert window.result_panel.height() < 400
    window.result_panel.close_button.click()
    assert not window.result_panel.isVisible()

    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    window.show_tool_result(
        "camera",
        {
            "mimeType": "image/png",
            "data": base64.b64encode(bytes(buffer.data())).decode("ascii"),
        },
    )
    assert window.result_panel.image_label.isVisible()
    window.close()


def test_video_results_stay_in_panel_and_can_be_cleared():
    app = QApplication.instance() or QApplication([])
    window = InterfaceVEGA()
    window.resize(1266, 709)
    window.show()

    with patch.object(window.result_panel, "_play_video") as play_video:
        window.show_tool_result(
            "trending_videos",
            {
                "autoplay": True,
                "items": [
                    {
                        "videoId": "abcDEF_1234",
                        "title": "Vídeo popular de teste",
                        "author": "Canal de teste",
                        "views": "1 milhão de visualizações",
                    },
                    {"videoId": "nextVid_123", "title": "Alternativa"},
                    {"videoId": "bad id!", "title": "ID inválido"},
                ],
            },
        )
        play_video.assert_called_once_with(["abcDEF_1234", "nextVid_123"])

    html = window.result_panel.body.toHtml()
    assert window.result_panel.isVisible()
    assert "Vídeo popular de teste" in html
    assert "vega-video:/abcDEF_1234" in html
    assert "vega-video://bad" not in html
    assert window.result_panel.video_view is None

    window.tool_result_cleared.emit()
    app.processEvents()
    assert not window.result_panel.isVisible()
    window.close()


def test_video_link_loads_youtube_embed_inside_interface():
    app = QApplication.instance() or QApplication([])
    window = InterfaceVEGA()

    class FakeWebView(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.parent = parent
            self.loaded_html = None
            self.base_url = None
            self.settings_object = FakeSettings()

        def settings(self):
            return self.settings_object

        def setFixedHeight(self, _height):
            return None

        def setHtml(self, html, base_url):
            self.loaded_html = html
            self.base_url = base_url

        def show(self):
            super().show()

    class FakeSettings:
        def __init__(self):
            self.attributes = []

        def setAttribute(self, *_args):
            self.attributes.append(_args)

    with patch("PySide6.QtWebEngineWidgets.QWebEngineView", FakeWebView):
        window.result_panel._open_result_link(QUrl("vega-video:/abcDEF_1234"))

    assert not window.result_panel.video_view.isHidden()
    assert 'const videoIds = ["abcDEF_1234"]' in window.result_panel.video_view.loaded_html
    assert "new YT.Player('player'" in window.result_panel.video_view.loaded_html
    assert "onError:function(){setTimeout(function(){startVideo(index+1);}" in (
        window.result_panel.video_view.loaded_html
    )
    assert "https://www.youtube.com/iframe_api" in window.result_panel.video_view.loaded_html
    assert '<meta name="referrer" content="origin">' in window.result_panel.video_view.loaded_html
    assert "playerVars:{autoplay:1" in window.result_panel.video_view.loaded_html
    assert "event.target.playVideo()" in window.result_panel.video_view.loaded_html
    assert window.result_panel.video_view.settings_object.attributes
    assert window.result_panel.video_view.settings_object.attributes[0][1] is False
    assert window.result_panel.video_view.base_url.toString() == "https://vega.local/"
    window.close()


def test_ctrl_v_attaches_image_without_replacing_typed_message():
    app = QApplication.instance() or QApplication([])
    window = InterfaceVEGA()
    clipboard_image = QImage(3, 3, QImage.Format.Format_RGB32)
    clipboard_image.fill(QColor("red"))
    QApplication.clipboard().setImage(clipboard_image)
    window.text_input.setText("O que tem de errado nesta imagem?")
    submitted = []
    window.message_submitted.connect(lambda text, image: submitted.append((text, image)))

    window.text_input.keyPressEvent(
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
        )
    )

    assert window.text_input.text() == "O que tem de errado nesta imagem?"
    assert window._pending_message_image is not None
    window._send_text()
    assert submitted[0][0] == "O que tem de errado nesta imagem?"
    assert not submitted[0][1].isNull()
    window.close()