"""Playlists pessoais da VEGA: guardar e tocar playlists do usuário.

Extraído de `VegaCommands` para manter as responsabilidades separadas:
aqui vive apenas a lógica de "minha playlist", sem comandos de sistema.
"""

from __future__ import annotations

import os
import time
import webbrowser
from urllib.parse import quote_plus


class PersonalPlaylistService:
    """Guarda playlists nomeadas e toca a playlist certa na plataforma certa.

    O serviço depende apenas de uma memória com a interface
    `get_playlist` / `list_playlists` / `save_playlist`; sem memória,
    todas as operações degradam para uma resposta clara, nunca uma exceção.
    """

    def __init__(self, memory=None):
        self.memory = memory

    # ==========================================================
    # MEMÓRIA DE PLAYLISTS
    # ==========================================================

    def _lookup(self, name):
        if self.memory is None or not hasattr(self.memory, "get_playlist"):
            return None
        try:
            return self.memory.get_playlist(name)
        except Exception:
            return None

    def save(self, name, platform="youtube", target=""):
        """Registra uma playlist pessoal pelo nome falado."""
        name = str(name or "").strip()
        if not name:
            return "Qual é o nome da playlist que você quer que eu guarde?"

        if self.memory is None or not hasattr(self.memory, "save_playlist"):
            return "Não consigo guardar playlists agora: memória indisponível."

        try:
            saved = self.memory.save_playlist(
                name,
                target=str(target or "").strip(),
                platform=str(platform or "youtube").lower(),
            )
        except Exception as error:
            return f"Não consegui guardar a playlist {name}: {error}"

        if not saved:
            return f"Não consegui guardar a playlist {name}. Tente de novo."

        return (
            f"Guardei a playlist {name}. "
            "Quando quiser, é só dizer 'toca minha playlist "
            f"{name}'."
        )

    def list_names(self):
        if self.memory is None or not hasattr(self.memory, "list_playlists"):
            return []
        try:
            return self.memory.list_playlists()
        except Exception:
            return []

    # ==========================================================
    # REPRODUÇÃO
    # ==========================================================

    def play(self, name, platform=None):
        """Toca a playlist pessoal `name` na plataforma correta.

        Ordem de decisão:
        1. playlist salva -> usa o URI/URL/termo guardado;
        2. não salva -> cai na busca normal pelo nome, avisando o usuário.
        """

        name = str(name or "").strip()
        if not name:
            return "Qual playlist você quer ouvir?"

        saved = self._lookup(name)
        if saved is None:
            return self._play_unsaved(name, platform)

        target = str(saved.get("target") or "").strip()
        saved_platform = str(saved.get("platform") or "youtube").lower()
        chosen = str(platform or saved_platform).lower()
        display = str(saved.get("name") or name).strip()

        if not target:
            # Salva sem URI: busca pelo nome na plataforma escolhida.
            return self._play_unsaved(display, chosen, saved=True)

        return self._open_target(target, chosen, display)

    def _play_unsaved(self, name, platform=None, saved=False):
        """Sem playlist registrada: busca pelo nome e explica o que faltou."""
        chosen = str(platform or "youtube").lower()
        result = self._search_platform(name, chosen)
        if saved:
            return result
        return (
            f"Ainda não tenho a playlist {name} guardada. "
            f"{result} Se quiser fixar depois, diga 'guarda essa playlist "
            f"{name}'."
        )

    def _open_target(self, target, platform, display):
        """Abre um alvo já conhecido (URI spotify:, URL http ou termo)."""

        lowered = target.lower()

        # URI nativo do Spotify: toca a playlist exata, sem busca.
        if lowered.startswith("spotify:"):
            try:
                os.startfile(target)
            except Exception as error:
                return f"Não consegui abrir a playlist {display} no Spotify: {error}"
            self._ensure_playback()
            return f"Tocando a playlist {display} no Spotify."

        # URL direta (YouTube Music, Deezer, etc.).
        if lowered.startswith(("http://", "https://")):
            try:
                webbrowser.open(target)
            except Exception as error:
                return f"Não consegui abrir a playlist {display}: {error}"
            return f"Tocando a playlist {display}."

        # Termo simples guardado: delega para a busca da plataforma.
        return self._search_platform(target, platform, display=display)

    def _search_platform(self, query, platform, display=None):
        """Busca `query` na plataforma escolhida (fallback sem URI)."""

        label = display or query
        platform = str(platform or "youtube").lower()

        try:
            if "spotify" in platform:
                try:
                    os.startfile("spotify:search:" + quote_plus(query))
                except Exception:
                    webbrowser.open(
                        "https://open.spotify.com/search/" + quote_plus(query)
                    )
                self._ensure_playback()
                return f"Tocando a playlist {label} no Spotify."

            if "deezer" in platform:
                webbrowser.open(
                    "https://www.deezer.com/search/" + quote_plus(query)
                )
                return f"Abrindo a playlist {label} no Deezer."

            # YouTube Music é o padrão: toca direto, sem login.
            webbrowser.open(
                "https://music.youtube.com/search?q="
                + quote_plus(query)
                + "&sp=EgIQAQ%253D%253D"
            )
            return f"Tocando a playlist {label} no YouTube Music."

        except Exception as error:
            return f"Não consegui tocar a playlist {label}: {error}"

    @staticmethod
    def _ensure_playback():
        """Dispara play de mídia depois de abrir o app (best effort)."""
        try:
            time.sleep(2.0)
            from core.actions.commands import VegaCommands
            VegaCommands.media_play_pause()
        except Exception:
            pass