import queue
import threading
import time
import unittest
from unittest.mock import patch

from core.audio.speaker import VegaSpeaker
from core.audio.voice import VegaVoice


class VegaSpeakerInterruptTests(unittest.TestCase):
    def test_interrupt_discards_old_audio_without_stopping_workers(self):
        speaker = VegaSpeaker.__new__(VegaSpeaker)
        speaker._state_lock = threading.Lock()
        speaker._generation = 0
        speaker._speaking = True
        speaker._speaking_event = threading.Event()
        speaker._speaking_event.set()
        speaker.is_running = True
        speaker.text_queue = queue.Queue()
        speaker.audio_queue = queue.Queue()
        speaker.text_queue.put((0, "old sentence"))
        speaker.text_queue.get_nowait()
        speaker.text_queue.put((0, "queued sentence"))
        speaker.audio_queue.put((b"old audio", None, 0))

        with patch("core.audio.speaker.sd.stop") as stop_audio:
            speaker.interrupt()

        stop_audio.assert_called_once_with()
        self.assertTrue(speaker.is_running)
        self.assertFalse(speaker.is_speaking)
        self.assertFalse(speaker._speaking_event.is_set())
        self.assertEqual(speaker._generation, 1)
        self.assertTrue(speaker.text_queue.empty())
        self.assertTrue(speaker.audio_queue.empty())

        speaker.speak("new sentence")
        self.assertEqual(speaker.text_queue.get_nowait(), (1, "new sentence"))

    def test_wait_until_done_unblocks_when_generation_is_interrupted(self):
        speaker = VegaSpeaker.__new__(VegaSpeaker)
        speaker._state_lock = threading.Lock()
        speaker._generation = 0
        speaker._speaking = True
        speaker._speaking_event = threading.Event()
        speaker._speaking_event.set()
        speaker.is_running = True
        speaker.text_queue = queue.Queue()
        speaker.audio_queue = queue.Queue()
        speaker.text_queue.put((0, "current sentence"))
        speaker.text_queue.get_nowait()
        finished = threading.Event()

        wait_thread = threading.Thread(
            target=lambda: (speaker.wait_until_done(5), finished.set()),
            daemon=True,
        )
        wait_thread.start()
        time.sleep(0.03)
        speaker.interrupt()
        wait_thread.join(timeout=0.3)

        self.assertTrue(finished.is_set())

    def test_voice_listener_notifies_speech_onset_during_barge_in(self):
        import numpy as np

        class FakeInputStream:
            def __init__(self, **_kwargs):
                self.blocks = [
                    np.zeros(640, dtype=np.int16),
                    np.full(640, 1200, dtype=np.int16),
                    *[np.zeros(640, dtype=np.int16) for _ in range(5)],
                ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                time.sleep(0.04)
                return self.blocks.pop(0), False

        voice = VegaVoice(microphone_index=0)
        voice.silence_duration = 0.08
        voice.minimum_speech_duration = 0.04
        voice.start_trigger_count = 1
        voice._noise_floor = 10.0
        onset = []
        voice.recognizer.recognize_google = lambda *_args, **_kwargs: "pare"

        with patch("core.audio.voice.sd.InputStream", FakeInputStream):
            text = voice.listen(
                barge_in=True,
                output_active=lambda: True,
                on_speech_start=lambda: onset.append(True),
            )

        self.assertEqual(text, "pare")
        self.assertEqual(onset, [True])

    def test_voice_listener_stops_when_output_finishes_before_user_starts(self):
        import numpy as np

        class FakeInputStream:
            def __init__(self, **_kwargs):
                self.blocks = [np.full(640, 5000, dtype=np.int16)]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return self.blocks.pop(0), False

        voice = VegaVoice(microphone_index=0)
        calls = []
        onset = []
        with patch("core.audio.voice.sd.InputStream", FakeInputStream):
            text = voice.listen(
                barge_in=True,
                output_active=lambda: True,
                stop_waiting=lambda: calls.append(True) or True,
                on_speech_start=lambda: onset.append(True),
            )

        self.assertIsNone(text)
        self.assertEqual(calls, [True])
        self.assertEqual(onset, [])

    def test_barge_in_raises_threshold_while_output_is_active(self):
        import numpy as np

        class FakeInputStream:
            def __init__(self, **_kwargs):
                self.block = np.full(640, 300, dtype=np.int16)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                time.sleep(0.04)
                return self.block, False

        voice = VegaVoice(microphone_index=0)
        voice._noise_floor = 10.0
        voice.start_trigger_count = 1
        voice.start_timeout = 0.12
        onset = []

        with patch("core.audio.voice.sd.InputStream", FakeInputStream):
            text = voice.listen(
                barge_in=True,
                output_active=lambda: True,
                on_speech_start=lambda: onset.append(True),
            )

        self.assertIsNone(text)
        self.assertEqual(onset, [])

    def test_transient_noise_does_not_trigger_speech_onset(self):
        import numpy as np

        class FakeInputStream:
            def __init__(self, **_kwargs):
                self.blocks = [
                    np.full(640, 1500, dtype=np.int16),
                    np.zeros(640, dtype=np.int16),
                    np.zeros(640, dtype=np.int16),
                    np.zeros(640, dtype=np.int16),
                    np.zeros(640, dtype=np.int16),
                ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                time.sleep(0.04)
                if not self.blocks:
                    return np.zeros(640, dtype=np.int16), False
                return self.blocks.pop(0), False

        voice = VegaVoice(microphone_index=0)
        voice._noise_floor = 10.0
        voice.start_timeout = 0.2
        voice.recognizer.recognize_google = lambda *_args, **_kwargs: "ruído"

        with patch("core.audio.voice.sd.InputStream", FakeInputStream):
            text = voice.listen(barge_in=False)

        self.assertIsNone(text)


if __name__ == "__main__":
    unittest.main()