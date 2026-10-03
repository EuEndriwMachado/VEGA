"""Controle de janelas NATIVAS do Windows via Win32 API.

Permite que a VEGA controle aplicações reais (Chrome, Explorador, VS Code...)
por comandos enquanto a interface permanece aberta:
- Focar / listar janelas visíveis
- Maximizar, minimizar, restaurar
- Mover e redimensionar
- Esconder/mostrar a barra de tarefas do Windows
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass
from typing import List, Optional, Tuple

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None

SW_HIDE = 0
SW_SHOW = 5
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
GA_ROOT = 2


@dataclass
class NativeWindowInfo:
    hwnd: int
    title: str
    rect: Tuple[int, int, int, int]  # left, top, right, bottom
    is_maximized: bool = False
    is_minimized: bool = False


class NativeWindowController:
    """Wrapper seguro: em plataformas sem Win32, tudo vira no-op."""

    def __init__(self, screen_width: int = None, screen_height: int = None):
        self.screen_width = screen_width or self._physical_screen_width()
        self.screen_height = screen_height or self._physical_screen_height()
        self._taskbar_shown = True

    # ----------------------------------------------------------
    # Geometria real da tela (pixels físicos, c/ DPI scaling)
    # ----------------------------------------------------------
    @staticmethod
    def _physical_screen_width() -> int:
        if user32 is None:
            return 1920
        return int(user32.GetSystemMetrics(0))  # SM_CXSCREEN

    @staticmethod
    def _physical_screen_height() -> int:
        if user32 is None:
            return 1080
        return int(user32.GetSystemMetrics(1))  # SM_CYSCREEN

    def refresh_screen_size(self):
        """Re-le a resolução física atual (chamar após trocar modo de tela)."""
        self.screen_width = self._physical_screen_width()
        self.screen_height = self._physical_screen_height()

    # ----------------------------------------------------------
    # Utilidades internas
    # ----------------------------------------------------------
    @staticmethod
    def _available() -> bool:
        return user32 is not None

    def _root(self, hwnd: int) -> int:
        return user32.GetAncestor(hwnd, GA_ROOT) or hwnd

    @staticmethod
    def _is_real_window(hwnd: int) -> bool:
        if not user32.IsWindow(hwnd):
            return False
        if not user32.IsWindowVisible(hwnd):
            return False
        # Ignora janelas sem título (popups ocultos, overlays)
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return False
        # Ignora janelas tool / cloaked (UWP invisíveis)
        ex_style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        if ex_style & 0x00000080:  # WS_EX_TOOLWINDOW
            return False
        return True

    # ----------------------------------------------------------
    # Consultas
    # ----------------------------------------------------------
    def get_rect(self, hwnd: int) -> Tuple[int, int, int, int]:
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)

    def get_title(self, hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value

    def get_placement(self, hwnd: int) -> Tuple[bool, bool]:
        """Retorna (maximizada, minimizada)."""
        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [
                ("length", wt.UINT),
                ("flags", wt.UINT),
                ("showCmd", wt.UINT),
                ("ptMinPosition", wt.POINT),
                ("ptMaxPosition", wt.POINT),
                ("rcNormalPosition", wt.RECT),
            ]
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
        return wp.showCmd == SW_MAXIMIZE, wp.showCmd == SW_MINIMIZE

    def get_foreground(self) -> Optional[NativeWindowInfo]:
        if not self._available():
            return None
        hwnd = self._root(user32.GetForegroundWindow())
        if not hwnd or not self._is_real_window(hwnd):
            return None
        maximized, minimized = self.get_placement(hwnd)
        return NativeWindowInfo(
            hwnd=hwnd,
            title=self.get_title(hwnd),
            rect=self.get_rect(hwnd),
            is_maximized=maximized,
            is_minimized=minimized,
        )

    def list_windows(self) -> List[NativeWindowInfo]:
        if not self._available():
            return []
        results: List[NativeWindowInfo] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def callback(hwnd, _lparam):
            if self._is_real_window(hwnd):
                left, top, right, bottom = self.get_rect(hwnd)
                # Descarta janelas "cloaked" (ex: autenticação fora da tela)
                if right <= left or bottom <= top:
                    return True
                if right < -1200 or bottom < -1200 or left > self.screen_width + 1200 or top > self.screen_height + 1200:
                    return True
                maximized, minimized = self.get_placement(hwnd)
                results.append(NativeWindowInfo(
                    hwnd=hwnd,
                    title=self.get_title(hwnd),
                    rect=(left, top, right, bottom),
                    is_maximized=maximized,
                    is_minimized=minimized,
                ))
            return True

        user32.EnumWindows(callback, 0)
        return results

    def window_from_point(self, x: int, y: int) -> Optional[NativeWindowInfo]:
        if not self._available():
            return None
        point = wt.POINT(int(x), int(y))
        hwnd = user32.WindowFromPoint(point)
        if not hwnd:
            return None
        hwnd = self._root(hwnd)
        if not self._is_real_window(hwnd):
            return None
        maximized, minimized = self.get_placement(hwnd)
        return NativeWindowInfo(
            hwnd=hwnd,
            title=self.get_title(hwnd),
            rect=self.get_rect(hwnd),
            is_maximized=maximized,
            is_minimized=minimized,
        )

    # ----------------------------------------------------------
    # Ações
    # ----------------------------------------------------------
    def focus(self, hwnd: int) -> bool:
        """Traz a janela para frente, contornando o bloqueio de foco
        do Windows para processos em segundo plano (AttachThreadInput)."""
        if not self._available():
            return False
        hwnd = self._root(hwnd)
        # Minimizada? Restaurar antes de focar.
        if self.get_placement(hwnd)[1]:
            user32.ShowWindow(hwnd, SW_RESTORE)
        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        if user32.SetForegroundWindow(hwnd):
            return True
        # Bloqueio de foreground: anexa a fila de input do thread em foco.
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        cur_thread = kernel32.GetCurrentThreadId()
        attached = False
        try:
            if fg_thread and fg_thread != cur_thread:
                attached = bool(user32.AttachThreadInput(fg_thread, cur_thread, True))
            user32.BringWindowToTop(hwnd)
            ok = bool(user32.SetForegroundWindow(hwnd))
            if not ok:
                # Última tentativa: batinha de ALT libera o foreground lock.
                user32.keybd_event(0x12, 0, 0, 0)   # ALT down
                ok = bool(user32.SetForegroundWindow(hwnd))
                user32.keybd_event(0x12, 0, 0x0002, 0)  # ALT up
            return ok
        finally:
            if attached:
                user32.AttachThreadInput(fg_thread, cur_thread, False)

    def minimize(self, hwnd: int) -> bool:
        return bool(user32.ShowWindow(hwnd, SW_MINIMIZE))

    def maximize(self, hwnd: int) -> bool:
        return bool(user32.ShowWindow(hwnd, SW_MAXIMIZE))

    def restore(self, hwnd: int) -> bool:
        return bool(user32.ShowWindow(hwnd, SW_RESTORE))

    def toggle_maximize(self, hwnd: int) -> bool:
        maximized, _ = self.get_placement(hwnd)
        if maximized:
            return self.restore(hwnd)
        return self.maximize(hwnd)

    def move_window(self, hwnd: int, x: int, y: int, w: int, h: int) -> bool:
        return bool(user32.SetWindowPos(
            hwnd, 0, int(x), int(y), int(w), int(h),
            SWP_NOZORDER | SWP_NOACTIVATE,
        ))

    def normalize_window(self, hwnd: int, width_fraction: float = 0.68, height_fraction: float = 0.78) -> bool:
        """Traz uma janela grande para um tamanho civilizado e centrado."""
        left, top, right, bottom = self.get_rect(hwnd)
        w = right - left
        h = bottom - top
        if w <= self.screen_width * 0.9 and h <= self.screen_height * 0.9:
            return False  # já está ok
        if self.get_placement(hwnd)[0]:
            self.restore(hwnd)
        new_w = int(self.screen_width * width_fraction)
        new_h = int(self.screen_height * height_fraction)
        new_x = (self.screen_width - new_w) // 2
        new_y = (self.screen_height - new_h) // 2
        return self.move_window(hwnd, new_x, new_y, new_w, new_h)

    def cycle_windows(self, direction: str = "right") -> Optional[NativeWindowInfo]:
        """Alt-Tab manual: foca a próxima janela visível."""
        windows = [w for w in self.list_windows() if not w.is_minimized]
        if not windows:
            return None
        fg = self.get_foreground()
        hwnds = [w.hwnd for w in windows]
        if fg and fg.hwnd in hwnds:
            idx = hwnds.index(fg.hwnd)
        else:
            idx = -1
        idx = (idx + (1 if direction == "right" else -1)) % len(windows)
        target = windows[idx]
        self.focus(target.hwnd)
        return target

    # ----------------------------------------------------------
    # Barra de tarefas do Windows
    # ----------------------------------------------------------
    def hide_taskbar(self) -> bool:
        if not self._available():
            return False
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)
            hidden = not bool(user32.IsWindowVisible(hwnd))
            if hidden:
                self._taskbar_shown = False
            return hidden
        return False

    def show_taskbar(self) -> bool:
        if not self._available():
            return False
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)
            shown = bool(user32.IsWindowVisible(hwnd))
            if shown:
                self._taskbar_shown = True
            return shown
        return False

    @property
    def taskbar_visible(self) -> bool:
        return self._taskbar_shown
