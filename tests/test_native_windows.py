"""Testes do controle de janelas nativas (Win32) — apenas leitura, sem invadir."""

import ctypes

import pytest

from core.actions.native_windows import NativeWindowController


@pytest.fixture
def controller():
    return NativeWindowController()


@pytest.mark.skipif(not hasattr(ctypes, "windll"), reason="Somente Windows")
class TestNativeWindowController:
    def test_lista_janelas_visiveis(self, controller):
        windows = controller.list_windows()
        assert isinstance(windows, list)
        assert len(windows) >= 1
        for w in windows:
            assert w.title  # todas com título
            assert len(w.rect) == 4

    def test_get_foreground_retorna_janela(self, controller):
        fg = controller.get_foreground()
        assert fg is not None
        assert fg.title
        assert fg.hwnd

    def test_filtra_janelas_fora_da_tela(self, controller):
        windows = controller.list_windows()
        for w in windows:
            left, top, right, bottom = w.rect
            # Janela cloaked do Windows fica em coordenadas absurdas (-25600)
            assert right > -20000

    def test_taskbar_estado_flag(self, controller):
        # Não mexe na taskbar real; só valida a flag inicial
        assert controller.taskbar_visible is True

    def test_normalize_nao_muda_janela_pequena(self, controller):
        fg = controller.get_foreground()
        if fg is None:
            pytest.skip("sem foreground")
        # Janela já pequena: normalize não deve mexer
        left, top, right, bottom = fg.rect
        w = right - left
        h = bottom - top
        if w > controller.screen_width * 0.9:
            pytest.skip("foreground atual é grande demais para teste seguro")
        assert controller.normalize_window(fg.hwnd) is False
