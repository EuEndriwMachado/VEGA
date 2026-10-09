# -*- coding: utf-8 -*-
"""Modo LIVE da VEGA — conversa em tempo real com visão (Gemini Live API).

Por que existe
--------------
O pipeline normal da VEGA é por TURNOS: grava -> transcreve -> pensa -> fala.
Ele é preciso e barato, mas tem um custo estrutural: a conversa espera a vez.
O modo live é o outro regime, o mesmo das demos de assistente em tempo real:
o áudio e os quadros de vídeo vão CONTÍNUOS para o modelo, e a resposta volta
em áudio enquanto você ainda fala — com interrupção natural (barge-in).

Este módulo é ADITIVO e ISOLADO:
    - não altera o pipeline de turnos (que continua sendo o padrão);
    - não toca no orbe nem na interface;
    - é ligado explicitamente (`VEGA_LIVE_MODE` ou pedido por voz).

Dependências: usa SOMENTE o que a VEGA já tem (sounddevice, Pillow) e o SDK
`google-genai` já usado em `core.brain.ai`. Nada de pyaudio/cv2/mss novos.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Formatos do protocolo: o modelo recebe PCM 16 kHz e devolve PCM 24 kHz.
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_SIZE = 1024

# O modo live fica caro: cada minuto envia áudio e quadros contínuos. Estes
# limites protegem o usuário de uma sessão esquecida aberta.
MAX_SESSION_SECONDS = 300.0
FRAME_INTERVAL_SECONDS = 1.0


class LiveUnavailable(RuntimeError):
    """O modo live não pode rodar (sem chave, sem SDK, sem áudio)."""


@dataclass
class LiveConfig:
    """Configuração da sessão live."""

    model: str = ""
    video_mode: str = "none"          # "camera", "screen" ou "none"
    language: str = "pt-BR"
    system_instruction: str = ""
    max_seconds: float = MAX_SESSION_SECONDS
    send_audio: bool = True
    send_video: bool = True
    frame_interval: float = FRAME_INTERVAL_SECONDS
    # Function calling nativo: durante a conversa contínua o modelo pode pedir
    # para DESENHAR no canvas e para executar ações do computador. São as
    # mesmas ferramentas do registro oficial — nada novo de segurança, nada de
    # ação imaginária: o modelo só escolhe; o código valida e executa.
    enable_tools: bool = True

    def resolved_model(self) -> str:
        return (
            self.model
            or os.getenv("GEMINI_LIVE_MODEL", "")
            or os.getenv("GEMINI_MODEL_NAME", "")
            or "gemini-2.0-flash-live-001"
        )


def live_is_available() -> bool:
    """O modo live pode rodar neste ambiente?"""
    if not os.getenv("GEMINI_API_KEY"):
        return False
    try:
        import google.genai  # noqa: F401
    except Exception:
        return False
    return True


# ----------------------------------------------------------------------
# Captura de áudio e vídeo (reusa o que a VEGA já tem)
# ----------------------------------------------------------------------

class MicrophoneStreamer:
    """Lê o microfone em PCM 16 bits e entrega os blocos a um callback."""

    def __init__(self, device_index: Optional[int] = None, sample_rate: int = SEND_SAMPLE_RATE):
        self.device_index = device_index
        self.sample_rate = sample_rate
        self._stream = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, on_chunk: Callable[[bytes], None]) -> bool:
        try:
            import sounddevice as sd

            def _callback(indata, frames, time_info, status):
                if self._stop.is_set():
                    return
                try:
                    on_chunk(bytes(indata))
                except Exception:
                    pass

            self._stream = sd.RawInputStream(
                device=self.device_index,
                channels=CHANNELS,
                samplerate=self.sample_rate,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=_callback,
            )
            self._stream.start()
            return True
        except Exception as error:
            print(f"[VEGA Live] Microfone indisponível: {error}")
            self._stream = None
            return False

    def stop(self) -> None:
        self._stop.set()
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass


class AudioPlayer:
    """Reproduz os blocos PCM 24 kHz recebidos do modelo, em paralelo."""

    def __init__(self, sample_rate: int = RECEIVE_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._stream = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._speaking = False

    def _ensure_stream(self) -> bool:
        if self._stream is not None:
            return True
        try:
            import sounddevice as sd

            self._stream = sd.RawOutputStream(
                samplerate=self.sample_rate,
                channels=CHANNELS,
                dtype="int16",
                blocksize=0,
                latency="high",
            )
            self._stream.start()
            return True
        except Exception as error:
            print(f"[VEGA Live] Saída de áudio indisponível: {error}")
            self._stream = None
            return False

    def write(self, data: bytes) -> None:
        if self._stop.is_set() or not data:
            return
        with self._lock:
            if not self._ensure_stream():
                return
            self._speaking = True
            try:
                self._stream.write(data)
            except Exception:
                pass
            finally:
                self._speaking = False

    def interrupt(self) -> None:
        """Esvazia a reprodução — a fala do usuário interrompe a da VEGA."""
        stream = self._stream
        if stream is None:
            return
        try:
            stream.abort()
        except Exception:
            pass
        try:
            stream.start()
        except Exception:
            pass

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def stop(self) -> None:
        self._stop.set()
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass


def _jpeg_payload_from_pil(image) -> Optional[Dict[str, str]]:
    """Converte uma imagem Pillow em payload base64 JPEG para o protocolo."""
    if image is None:
        return None
    try:
        frame = image.copy()
        frame.thumbnail([1024, 1024])
        buffer = io.BytesIO()
        frame.convert("RGB").save(buffer, format="jpeg", quality=70)
        return {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
        }
    except Exception:
        return None


# ----------------------------------------------------------------------
# Sessão live
# ----------------------------------------------------------------------

class LiveSession:
    """Uma sessão de conversa em tempo real.

    A sessão roda num thread próprio com um event loop asyncio. A interface
    conversa com ela por métodos thread-safe: `start`, `stop`, `send_text`.
    """

    def __init__(
        self,
        config: Optional[LiveConfig] = None,
        on_text: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        frame_provider: Optional[Callable[[], Any]] = None,
        microphone_index: Optional[int] = None,
        ai=None,
        tool_declarations: Optional[List[Dict[str, Any]]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        on_tool_call: Optional[Callable[[str, Dict[str, Any], Any], None]] = None,
    ):
        self.config = config or LiveConfig()
        self.on_text = on_text
        self.on_status = on_status
        self.frame_provider = frame_provider
        self.microphone_index = microphone_index
        # `ai` é o VegaAI da VEGA: reaproveitamos o cliente Gemini já
        # configurado (chave, versão de API) em vez de abrir outro.
        self.ai = ai
        # Ferramentas: declarações no formato do registro oficial e o executor
        # que valida e roda cada chamada. Sem elas, a sessão é só conversa.
        self.tool_declarations = list(tool_declarations or [])
        self.tool_executor = tool_executor
        self.on_tool_call = on_tool_call

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._session = None
        self._stop = threading.Event()
        self._out_queue: Optional[asyncio.Queue] = None
        self._mic: Optional[MicrophoneStreamer] = None
        self._player: Optional[AudioPlayer] = None
        self._started_at = 0.0
        self._running = False

    # ------------------------------------------------------------------
    # API pública (thread-safe)
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running and not self._stop.is_set()

    def start(self) -> bool:
        """Inicia a sessão em segundo plano. False se indisponível."""
        if self.is_running:
            return True
        if not live_is_available():
            self._notify("Modo live indisponível: falta a chave do Gemini.")
            return False

        self._stop.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._run_thread, name="VegaLiveSession", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Encerra a sessão e libera microfone e alto-falante."""
        self._stop.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(lambda: None)
            except Exception:
                pass
        if self._mic is not None:
            self._mic.stop()
        if self._player is not None:
            self._player.stop()
        self._running = False

    def send_text(self, text: str) -> None:
        """Envia uma fala digitada para a sessão live."""
        loop = self._loop
        session = self._session
        if loop is None or session is None or not str(text or "").strip():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                session.send(input=str(text).strip(), end_of_turn=True), loop
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Execução interna
    # ------------------------------------------------------------------

    def _notify(self, message: str) -> None:
        if callable(self.on_status):
            try:
                self.on_status(message)
            except Exception:
                pass

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as error:
            print(f"[VEGA Live] Sessão encerrada: {error}")
        finally:
            self._running = False
            self._notify("Modo live encerrado.")

    def _build_config(self):
        """Monta a configuração do protocolo (ou None se o SDK não ajudar)."""
        try:
            from google.genai import types
        except Exception:
            return None

        instruction = self.config.system_instruction or (
            "Você é a VEGA, assistente pessoal do Endriw. Fale naturalmente em "
            "português brasileiro, com frases curtas, como em uma conversa "
            "falada. Você está vendo a tela ou a câmera: descreva e ajude com "
            "o que aparecer quando for útil. Não leia menus em voz alta."
        )
        try:
            return types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction=instruction,
                input_audio_transcription=types.AudioTranscriptionConfig(
                    language_codes=[self.config.language],
                ),
                context_window_compression=types.ContextWindowCompressionConfig(
                    trigger_tokens=0,
                    sliding_window=types.SlidingWindow(target_tokens=0),
                ),
                tools=self._live_tools(types),
            )
        except Exception as error:
            print(f"[VEGA Live] Config não suportada pelo SDK: {error}")
            return None

    def _live_tools(self, types):
        """Declara para o modelo as ferramentas que ele pode chamar ao vivo.

        São as MESMAS do registro oficial da VEGA (via `_registered_tool_schemas`),
        então nada de ação imaginária: o modelo escolhe pelo nome e o código
        executa com toda a validação que já existe. Sem ferramentas, devolve
        lista vazia (a sessão funciona só como conversa).
        """
        if not self.config.enable_tools:
            return []
        declarations = self.tool_declarations
        if not declarations:
            return []
        try:
            functions = [
                types.FunctionDeclaration(
                    name=item["name"],
                    description=item["description"],
                    parameters_json_schema=item["parameters"],
                )
                for item in declarations
            ]
            return [types.Tool(function_declarations=functions)]
        except Exception as error:
            print(f"[VEGA Live] Ferramentas não declaradas: {error}")
            return []

    def _client(self):
        """Reusa o cliente Gemini já configurado pela VEGA quando possível."""
        if self.ai is not None and getattr(self.ai, "gemini_client", None) is not None:
            return self.ai.gemini_client
        from google import genai

        return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        config = self._build_config()
        if config is None:
            self._notify("Modo live indisponível: SDK sem suporte a tempo real.")
            return

        model = self.config.resolved_model()
        self._out_queue = asyncio.Queue(maxsize=10)
        self._player = AudioPlayer()
        self._mic = MicrophoneStreamer(device_index=self.microphone_index)

        try:
            async with self._client().aio.live.connect(model=model, config=config) as session:
                self._session = session
                self._running = True
                self._notify("Modo live ativo. Pode falar.")

                tasks = [
                    asyncio.create_task(self._pump_out()),
                    asyncio.create_task(self._receive()),
                ]
                if self.config.send_audio and self._mic.start(self._on_mic_chunk):
                    tasks.append(asyncio.create_task(self._tick()))
                if self.config.send_video and callable(self.frame_provider):
                    tasks.append(asyncio.create_task(self._send_frames()))

                await self._watch(tasks)
                for task in tasks:
                    task.cancel()
        finally:
            self._session = None
            if self._mic is not None:
                self._mic.stop()
            if self._player is not None:
                self._player.stop()

    async def _watch(self, tasks: List[asyncio.Task]) -> None:
        """Mantém a sessão viva até o limite de tempo ou pedido de parada."""
        while not self._stop.is_set():
            if time.monotonic() - self._started_at >= self.config.max_seconds:
                self._notify("Modo live encerrado pelo limite de tempo.")
                break
            await asyncio.sleep(0.25)

    async def _tick(self) -> None:
        """Mantém o pump de áudio respirando (o envio é por callback)."""
        while not self._stop.is_set():
            await asyncio.sleep(0.1)

    def _on_mic_chunk(self, chunk: bytes) -> None:
        """Callback do microfone (thread do sounddevice) -> fila do loop."""
        queue = self._out_queue
        loop = self._loop
        if queue is None or loop is None or self._stop.is_set():
            return
        payload = {"data": chunk, "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"}
        try:
            loop.call_soon_threadsafe(queue.put_nowait, payload)
        except Exception:
            pass

    async def _send_frames(self) -> None:
        """Envia quadros de vídeo (câmera ou tela) no intervalo configurado."""
        while not self._stop.is_set():
            frame = None
            try:
                frame = self.frame_provider()
            except Exception:
                frame = None
            payload = _jpeg_payload_from_pil(frame)
            if payload is not None and self._out_queue is not None:
                try:
                    self._out_queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass
            await asyncio.sleep(max(0.2, self.config.frame_interval))

    async def _pump_out(self) -> None:
        """Envia o que estiver na fila (áudio e vídeo) para o modelo.

        Usa o canal ESPECÍFICO de cada tipo (`send_realtime_input(audio=/media=)`),
        que é o caminho correto do protocolo para streaming contínuo. O envio
        genérico (`send(input=...)`) era usado antes e não distinguia os tipos
        de mídia — com o canal específico, o modelo trata cada fluxo como o
        que ele realmente é.
        """
        queue = self._out_queue
        while not self._stop.is_set():
            if queue is None:
                return
            message = await queue.get()
            session = self._session
            if session is None:
                continue
            mime_type = str(message.get("mime_type", "") or "")
            raw = message.get("data", b"")
            try:
                await self._send_media(session, mime_type, raw)
            except Exception as error:
                if self._stop.is_set():
                    return
                print(f"[VEGA Live] Falha ao enviar mídia: {error}")

    async def _send_media(self, session, mime_type: str, raw) -> None:
        """Envia um bloco de mídia pelo canal correto do protocolo.

        O SDK espera `bytes` (ou base64) dentro de um `Blob`. Como o nosso
        payload interno é base64 (o `_jpeg_payload_from_pil` devolve string),
        decodificamos antes de enviar.
        """
        data = raw
        if isinstance(data, str):
            try:
                data = base64.b64decode(data)
            except Exception:
                data = data.encode("utf-8", "ignore")

        try:
            from google.genai import types
        except Exception:
            types = None

        blob = types.Blob(data=data, mime_type=mime_type) if types is not None else None

        if mime_type.startswith("audio/"):
            if blob is not None:
                await session.send_realtime_input(audio=blob)
            else:
                await session.send(input={"data": data, "mime_type": mime_type})
        elif mime_type.startswith(("image/", "video/")):
            if blob is not None:
                await session.send_realtime_input(media=blob)
            else:
                await session.send(input={"data": data, "mime_type": mime_type})
        else:
            await session.send(input={"data": data, "mime_type": mime_type})

    async def _receive(self) -> None:
        """Recebe áudio, transcrição e chamadas de ferramenta do modelo."""
        while not self._stop.is_set():
            session = self._session
            if session is None:
                await asyncio.sleep(0.1)
                continue
            try:
                async for response in session.receive():
                    if self._stop.is_set():
                        return

                    # Áudio do modelo: o caminho do protocolo é o conteúdo do
                    # turno (`server_content.model_turn.parts[].inline_data`).
                    # O campo `response.data` também é lido como alternativa, e
                    # a leitura é defensiva porque o formato varia entre versões
                    # do SDK — nenhum deles pode derrubar a sessão de voz.
                    audio = self._extract_audio(response)
                    if audio:
                        if self._player is not None:
                            self._player.write(audio)
                        continue

                    # Chamada de ferramenta: o modelo pediu para DESENHAR ou
                    # executar uma ação do PC. O código valida e executa pelo
                    # registro oficial, e devolve o resultado À SESSÃO, para
                    # que a VEGA continue a conversa sabendo o que aconteceu.
                    calls = self._extract_tool_calls(response)
                    if calls:
                        await self._handle_tool_calls(session, calls)
                        continue

                    text = self._extract_text(response)
                    if text and callable(self.on_text):
                        try:
                            self.on_text(text)
                        except Exception:
                            pass
            except Exception as error:
                if self._stop.is_set():
                    return
                print(f"[VEGA Live] Recebimento interrompido: {error}")
                await asyncio.sleep(0.5)

    @staticmethod
    def _extract_audio(response) -> Optional[bytes]:
        """Extrai o áudio PCM do evento, no formato do protocolo.

        Preferimos o caminho estrutural (`server_content.model_turn.parts`),
        que é como o SDK entrega o áudio do modelo em streaming; o campo
        `data` fica como alternativa para versões mais novas/antigas.
        """
        server_content = getattr(response, "server_content", None)
        model_turn = getattr(server_content, "model_turn", None)
        for part in getattr(model_turn, "parts", None) or []:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if data:
                return bytes(data) if not isinstance(data, bytes) else data
        data = getattr(response, "data", None)
        if data:
            try:
                return bytes(data)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _extract_text(response) -> str:
        """Extrai texto (transcrição) do evento, de forma defensiva."""
        text = getattr(response, "text", None)
        if text:
            return str(text)
        server_content = getattr(response, "server_content", None)
        output_transcription = getattr(server_content, "output_transcription", None)
        transcription_text = getattr(output_transcription, "text", None)
        return str(transcription_text) if transcription_text else ""

    @staticmethod
    def _extract_tool_calls(response) -> List[Any]:
        """Extrai as chamadas de ferramenta de um evento do protocolo.

        O SDK entrega as chamadas em `response.tool_call.function_calls`. A
        leitura é defensiva: uma versão diferente do SDK não pode derrubar a
        sessão por causa do formato.
        """
        tool_call = getattr(response, "tool_call", None)
        if tool_call is None:
            return []
        calls = getattr(tool_call, "function_calls", None)
        if not calls:
            return []
        try:
            return list(calls)
        except TypeError:
            return []

    async def _handle_tool_calls(self, session, calls) -> None:
        """Executa as chamadas e devolve as respostas à sessão."""
        responses = []
        for call in calls:
            name = str(getattr(call, "name", "") or "")
            arguments = getattr(call, "args", None)
            if arguments is None:
                arguments = {}
            result = None
            if callable(self.tool_executor) and name:
                try:
                    result = self.tool_executor(name, dict(arguments))
                except Exception as error:
                    result = {"error": str(error)}
                    print(f"[VEGA Live] Falha na ferramenta {name}: {error}")

            if callable(self.on_tool_call) and name:
                try:
                    self.on_tool_call(name, dict(arguments), result)
                except Exception:
                    pass

            responses.append({
                "id": getattr(call, "id", None),
                "name": name,
                "response": self._tool_response(result),
            })

        if not responses:
            return
        try:
            await session.send_tool_response(function_responses=responses)
        except Exception as error:
            # Nem toda versão do SDK expõe `send_tool_response`; se não existir,
            # a chamada foi executada mas o modelo não é notificado. Não é
            # motivo para derrubar a sessão de voz.
            print(f"[VEGA Live] Não foi possível responder à ferramenta: {error}")

    @staticmethod
    def _tool_response(result) -> Dict[str, Any]:
        """Normaliza o resultado da ferramenta para o formato do protocolo."""
        if isinstance(result, dict):
            return result
        if result is None:
            return {"status": "ok"}
        return {"result": str(result)}


# ----------------------------------------------------------------------
# Integração com o cérebro (opcional e isolada)
# ----------------------------------------------------------------------

def build_live_session(brain, on_text=None, on_status=None, video_mode=None) -> LiveSession:
    """Monta uma `LiveSession` ligada ao cérebro da VEGA.

    O provedor de quadros usa o MESMO callback de captura que o modo de visão
    por turnos já usa (`brain.camera_frame_callback`), para câmera; para tela,
    usa o capturador da interface. Assim o modo live não inventa um caminho
    novo de captura — reusa o que já foi validado.

    As ferramentas declaradas são as MESMAS do registro oficial (via o
    construtor de schemas que já existe em `core.brain.ai`) e o executor é o
    mesmo `_execute_registered_tool_call` do cérebro. Ou seja: durante a
    conversa contínua, o modelo pode pedir para desenhar no canvas e para
    executar ações do PC — sempre passando pela validação e pela segurança que
    já existem no pipeline de turnos.
    """
    mode = video_mode or os.getenv("VEGA_LIVE_VIDEO", "none").strip().lower()
    frame_provider = None
    if mode in {"camera", "screen"}:
        frame_provider = _frame_provider_for(brain, mode)

    declarations, executor = _live_tools_for(brain)

    return LiveSession(
        config=LiveConfig(video_mode=mode),
        on_text=on_text,
        on_status=on_status,
        frame_provider=frame_provider,
        ai=getattr(brain, "ai", None),
        tool_declarations=declarations,
        tool_executor=executor,
        on_tool_call=getattr(brain, "live_tool_callback", None),
    )


def _live_tools_for(brain):
    """Devolve (declarações, executor) das ferramentas do modo live.

    Apenas ferramentas SEGURAS para uso contínuo: nada que exija PIN/rosto
    entra na sessão de voz (o portão de segurança não existe no fluxo live, e
    oferecê-las ao modelo seria abrir um atalho que a VEGA não tem no modo
    normal). Ferramentas com `auth_required` ficam de fora.
    """
    declarations: List[Dict[str, Any]] = []
    executor = None
    try:
        from core.brain.ai import _registered_tool_schemas

        manager = getattr(brain, "tool_manager", None)
        specs = manager.list_tools() if manager is not None else []
        safe_specs = [
            spec for spec in specs
            if not getattr(spec, "auth_required", False)
            and callable(getattr(spec, "implementation", None))
        ]
        declarations = _registered_tool_schemas(safe_specs)
        execute = getattr(brain, "_execute_registered_tool_call", None)
        if callable(execute):
            executor = execute
    except Exception as error:
        print(f"[VEGA Live] Ferramentas indisponíveis no modo live: {error}")
        return [], None
    return declarations, executor


def _frame_provider_for(brain, mode: str) -> Optional[Callable[[], Any]]:
    if mode == "camera":
        callback = getattr(brain, "camera_frame_callback", None)
        if callable(callback):
            return callback
        return None
    # Tela: usa o capturador do brain se existir; caso contrário, nenhum.
    callback = getattr(brain, "screen_frame_callback", None)
    if callable(callback):
        return callback
    return None
