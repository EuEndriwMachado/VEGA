import os
import tempfile
import threading
import unittest
import wave
from unittest.mock import patch

import vega


class StartupBehaviorTests(unittest.TestCase):
    def test_background_music_starts_for_valid_wav(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name

        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(b"\x00\x00" * 100)

        write_started = threading.Event()
        stream_closed = threading.Event()

        class DummyOutputStream:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                return None

            def write(self, _chunk):
                write_started.set()

            def stop(self):
                return None

            def close(self):
                stream_closed.set()

        with patch.object(vega.sd, "OutputStream", DummyOutputStream):
            result = vega.start_background_music(path=path, volume=0.08)
            self.assertIsNotNone(result)
            self.assertTrue(write_started.wait(timeout=1))
            vega.stop_background_music()
            self.assertTrue(stream_closed.is_set())

        os.unlink(path)

    def test_background_music_fades_out_after_90_seconds(self):
        base_volume = 0.12
        faded_volume = vega.calculate_background_music_volume(base_volume, elapsed_seconds=95)
        self.assertLess(faded_volume, base_volume)
        self.assertGreater(faded_volume, 0.0)
        self.assertEqual(vega.calculate_background_music_volume(base_volume, elapsed_seconds=120), 0.0)

    def test_stopping_background_music_does_not_stop_other_audio(self):
        with patch.object(vega.sd, "stop") as stop_audio:
            vega.stop_background_music()
        stop_audio.assert_not_called()

    def test_news_confirmation_accepts_common_yes_phrases_and_rejects_no(self):
        for phrase in ("sim", "sim, quero", "quero", "pode falar", "manda aí"):
            with self.subTest(phrase=phrase):
                self.assertIs(vega.classify_news_confirmation(phrase), True)

        for phrase in ("não", "não quero", "agora não", "depois"):
            with self.subTest(phrase=phrase):
                self.assertIs(vega.classify_news_confirmation(phrase), False)

    def test_activation_update_starts_with_quiet_greeting(self):
        text = vega.build_activation_update()
        self.assertIn("Olá, Endriw", text)
        self.assertNotIn("Sim, Endriw", text)

    @patch("vega.requests.get")
    def test_startup_briefing_uses_only_three_short_headlines(self, mock_get):
        class DummyResponse:
            def __init__(self, payload):
                self.content = payload.encode("utf-8")

            def raise_for_status(self):
                return None

        def fake_get(url, timeout, headers=None):
            if "bbci" in url:
                payload = """
                <rss><channel>
                    <item><title>BBC 1</title><link>https://example.com/bbc1</link></item>
                    <item><title>BBC 2</title><link>https://example.com/bbc2</link></item>
                    <item><title>BBC 3</title><link>https://example.com/bbc3</link></item>
                </channel></rss>
                """
            elif "reuters" in url:
                payload = """
                <rss><channel>
                    <item><title>Reuters 1</title><link>https://example.com/r1</link></item>
                    <item><title>Reuters 2</title><link>https://example.com/r2</link></item>
                </channel></rss>
                """
            else:
                payload = """
                <rss><channel>
                    <item><title>G1 1</title><link>https://example.com/g1</link></item>
                    <item><title>G1 2</title><link>https://example.com/g2</link></item>
                </channel></rss>
                """
            return DummyResponse(payload)

        mock_get.side_effect = fake_get

        text = vega.build_startup_news_briefing(limit=3)

        self.assertIn("Olá, Endriw", text)
        self.assertEqual(text.count("A BBC informa"), 1)
        self.assertEqual(text.count("A Reuters informa"), 1)
        self.assertEqual(text.count("O G1 informa"), 1)
        self.assertLess(len(text.split()), 45)

    def test_random_upload_music_picker_chooses_existing_upload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = os.path.join(tmpdir, "a.mp3")
            second = os.path.join(tmpdir, "b.wav")
            with open(first, "wb") as handle:
                handle.write(b"a")
            with open(second, "wb") as handle:
                handle.write(b"b")

            chosen = vega.choose_random_background_music(tmpdir)
            self.assertTrue(chosen in {first, second})
            self.assertTrue(os.path.exists(chosen))

    def test_activation_update_only_once_per_day(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = vega.VegaMemory(base_path=tmpdir)
            self.assertTrue(vega.should_run_activation_update(memory))
            vega.mark_activation_update_sent(memory)
            self.assertFalse(vega.should_run_activation_update(memory))

    @patch("vega.requests.get")
    def test_startup_news_fetches_only_three_items(self, mock_get):
        class DummyResponse:
            def __init__(self, payload):
                self.content = payload.encode("utf-8")

            def raise_for_status(self):
                return None

        def fake_get(url, timeout, headers=None):
            if "bbci" in url:
                payload = """
                <rss><channel>
                    <item><title>BBC 1</title><link>https://example.com/bbc1</link></item>
                    <item><title>BBC 2</title><link>https://example.com/bbc2</link></item>
                    <item><title>BBC 3</title><link>https://example.com/bbc3</link></item>
                </channel></rss>
                """
            elif "reuters" in url:
                payload = """
                <rss><channel>
                    <item><title>Reuters 1</title><link>https://example.com/r1</link></item>
                    <item><title>Reuters 2</title><link>https://example.com/r2</link></item>
                </channel></rss>
                """
            else:
                payload = """
                <rss><channel>
                    <item><title>G1 1</title><link>https://example.com/g1</link></item>
                    <item><title>G1 2</title><link>https://example.com/g2</link></item>
                </channel></rss>
                """
            return DummyResponse(payload)

        mock_get.side_effect = fake_get
        items = vega.fetch_startup_news(limit=3)
        self.assertEqual(len(items), 3)
        self.assertTrue(all(item.get("title") for item in items))
        self.assertTrue(all(item.get("source") for item in items))

    def test_identity_contains_creator_aliases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = vega.VegaMemory(base_path=tmpdir)
            memory.seed_identity()
            permanent = memory.get_permanent()
            self.assertIn("meu criador", permanent)
            self.assertIn("endriw", permanent)
            self.assertEqual(permanent["meu criador"]["value"], "Endriw Machado")
            self.assertEqual(permanent["endriw"]["value"], "Endriw Machado")

    def test_identity_contains_exact_vega_description(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = vega.VegaMemory(base_path=tmpdir)
            memory.seed_identity()
            permanent = memory.get_permanent()
            description = permanent["sobre a vega"]["value"]
            self.assertIn("Meu nome vem da estrela Vega", description)
            self.assertIn("constelação de Lira", description)
            self.assertIn("an-nasr al-wāqi‘", description)
            self.assertIn("a águia que cai", description)
            self.assertIn("Um astro de imenso potencial destrutivo", description)
            self.assertIn("seis bilhões de toneladas", description)

    @patch("vega.requests.get")
    def test_activation_update_has_intro_and_single_source_reference(self, mock_get):
        class DummyResponse:
            def __init__(self, payload):
                self.content = payload.encode("utf-8")

            def raise_for_status(self):
                return None

        bbc_payload = """
        <rss><channel>
            <item><title>Primeiro tema da BBC</title></item>
            <item><title>Segundo tema da BBC</title></item>
        </channel></rss>
        """
        reuters_payload = """
        <rss><channel>
            <item><title>Tema da Reuters</title></item>
        </channel></rss>
        """
        g1_payload = """
        <rss><channel>
            <item><title>Tema do G1</title></item>
        </channel></rss>
        """

        def fake_get(url, timeout, headers=None):
            if "bbci" in url:
                return DummyResponse(bbc_payload)
            if "reuters" in url:
                return DummyResponse(reuters_payload)
            return DummyResponse(g1_payload)

        mock_get.side_effect = fake_get

        text = vega.build_activation_update()

        self.assertIn("Olá, Endriw", text)
        self.assertTrue(
            any(fragment in text for fragment in [
                "Vou te atualizar",
                "Aqui vai um resumo",
                "Deixa eu te passar"
            ])
        )
        self.assertLessEqual(text.count("BBC"), 1)
        self.assertIn("A BBC informa", text)



if __name__ == "__main__":
    unittest.main()
