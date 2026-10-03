import unittest
import asyncio
from unittest.mock import patch

from tools.api_tools import WeatherTool, CurrencyTool
from core.brain.brain import VegaBrain
from core.audio.speaker import VegaSpeaker


class WeatherCurrencyTtsTests(unittest.TestCase):
    def setUp(self):
        self.brain = VegaBrain()

    def test_weather_execution_and_format(self):
        weather_tool = self.brain.tool_manager.get_tool("weather")
        self.assertIsNotNone(weather_tool)

        params = self.brain._build_tool_parameters("como tá o tempo em São Paulo?", weather_tool)
        self.assertEqual(params.get("city"), "sao paulo")

        # Test formatting with realistic dummy data
        sample_result = {
            "source": "open-meteo",
            "location": "São Paulo",
            "temperature_c": 22.4,
            "feels_like_c": 21.8,
            "humidity": 68,
            "wind_kmh": 14.5,
            "condition": "parcialmente nublado",
            "temp_max": 26.0,
            "temp_min": 17.0,
            "rain_prob": 20,
        }
        formatted = self.brain._format_tool_result(weather_tool, sample_result)
        self.assertIn("São Paulo", formatted)
        self.assertIn("graus", formatted)
        self.assertIn("parcialmente nublado", formatted)
        # Verify no raw unformatted symbol like '°C' in formatted output
        self.assertNotIn("°C", formatted)

    def test_tool_result_callback_preserves_spoken_weather_response(self):
        weather_tool = self.brain.tool_manager.get_tool("weather")
        sample_result = {
            "location": "Curitiba",
            "temperature_c": 18,
            "condition": "nublado",
            "temp_min": 12,
            "temp_max": 21,
            "rain_prob": 10,
        }
        original_implementation = weather_tool.implementation
        captured = []
        self.brain.tool_result_callback = lambda name, result: captured.append((name, result))
        weather_tool.implementation = lambda **_params: sample_result
        try:
            response = self.brain._process_tool_request("como tá o tempo em Curitiba?")
        finally:
            weather_tool.implementation = original_implementation

        self.assertEqual(captured, [("weather", sample_result)])
        self.assertIn("Curitiba", response)

    def test_streaming_video_request_executes_tool_and_updates_panel(self):
        video_tool = self.brain.tool_manager.get_tool("trending_videos")
        sample_result = {
            "items": [{"videoId": "abcDEF_1234", "title": "Vídeo teste"}],
            "region": "BR",
        }
        original_implementation = video_tool.implementation
        captured = []
        self.brain.tool_result_callback = lambda name, result: captured.append((name, result))
        video_tool.implementation = lambda **_params: sample_result
        try:
            response = list(self.brain.process_stream("me mostra os vídeos em alta"))
        finally:
            video_tool.implementation = original_implementation

        self.assertEqual(captured, [("trending_videos", sample_result)])
        self.assertEqual(response, ["Encontrei um vídeo para você: Vídeo teste."])
        self.assertNotIn("videoId", " ".join(response))

    def test_pdf_request_extracts_windows_path_and_formats_preview(self):
        tool = self.brain.tool_manager.get_tool("summarize_document")
        request = r"resuma o PDF C:\Users\Endriw\Documents\Lista de física.pdf"

        params = self.brain._build_tool_parameters(request, tool)
        response = self.brain._format_tool_result(
            tool,
            {
                "path": params["path"],
                "pages": 4,
                "characters": 1500,
                "preview": "Questão 1: resolva a equação do segundo grau.",
            },
        )

        self.assertEqual(params["path"], r"C:\Users\Endriw\Documents\Lista de física.pdf")
        self.assertIn("4 páginas", response)
        self.assertIn("Questão 1", response)
        self.assertNotIn("characters", response)

    def test_currency_execution_and_amount_extraction(self):
        currency_tool = self.brain.tool_manager.get_tool("currency")
        self.assertIsNotNone(currency_tool)

        # Test single quote
        p1 = self.brain._build_tool_parameters("qual a cotação do dólar hoje?", currency_tool)
        self.assertEqual(p1.get("base"), "USD")
        self.assertEqual(p1.get("target"), "BRL")
        self.assertEqual(p1.get("amount"), 1.0)

        # Test conversion with amount
        p2 = self.brain._build_tool_parameters("quanto tá 50 euros em reais?", currency_tool)
        self.assertEqual(p2.get("base"), "EUR")
        self.assertEqual(p2.get("target"), "BRL")
        self.assertEqual(p2.get("amount"), 50.0)

        # Test BRL to USD
        p3 = self.brain._build_tool_parameters("converte 100 reais em dólar", currency_tool)
        self.assertEqual(p3.get("base"), "BRL")
        self.assertEqual(p3.get("target"), "USD")
        self.assertEqual(p3.get("amount"), 100.0)

    def test_currency_formatting(self):
        currency_tool = self.brain.tool_manager.get_tool("currency")
        sample_quote = {
            "source": "awesomeapi",
            "base": "USD",
            "target": "BRL",
            "amount": 1.0,
            "rate": 5.14,
            "converted": 5.14,
            "high": 5.18,
            "low": 5.12,
            "pct_change": 0.25,
            "direction": "alta",
        }
        formatted = self.brain._format_tool_result(currency_tool, sample_quote)
        self.assertIn("dólar", formatted)
        self.assertIn("5,14", formatted)
        self.assertNotIn("1 USD = 5.14 BRL", formatted)

        # Test conversion format
        sample_conv = {
            "source": "awesomeapi",
            "base": "EUR",
            "target": "BRL",
            "amount": 50.0,
            "rate": 5.90,
            "converted": 295.0,
        }
        formatted_conv = self.brain._format_tool_result(currency_tool, sample_conv)
        self.assertIn("50", formatted_conv)
        self.assertIn("euros", formatted_conv)
        self.assertIn("295,00", formatted_conv)

    def test_tts_speech_preparation(self):
        # 1. Currency speech
        c_text = "O dólar tá cotado a R$ 5,14 agora."
        spoken_c = VegaSpeaker._prepare_text_for_speech(c_text)
        self.assertIn("5 reais e 14 centavos", spoken_c)
        self.assertNotIn("R$", spoken_c)

        # 2. Weather speech
        w_text = "Em Curitiba tá 18°C com sensação de 18°C, vento a 12 km/h e 75% de umidade."
        spoken_w = VegaSpeaker._prepare_text_for_speech(w_text)
        self.assertIn("18 graus", spoken_w)
        self.assertIn("12 quilômetros por hora", spoken_w)
        self.assertIn("75 por cento", spoken_w)
        self.assertNotIn("°C", spoken_w)
        self.assertNotIn("km/h", spoken_w)
        self.assertNotIn("%", spoken_w)

    def test_etec_is_pronounced_as_etequi_by_edge_tts(self):
        speaker = VegaSpeaker.__new__(VegaSpeaker)
        speaker.pronunciations = {"ETEC": "etéqui"}
        speaker.voice = "pt-BR-FranciscaNeural"
        speaker.rate = "+0%"
        speaker.pitch = "+0Hz"
        spoken_text = []

        class DummyCommunicate:
            def __init__(self, text, *_args, **_kwargs):
                spoken_text.append(text)

            async def stream(self):
                yield {"type": "audio", "data": b"test"}

        with patch("core.audio.speaker.edge_tts.Communicate", DummyCommunicate):
            asyncio.run(speaker._generate_edge_audio("A ETEC fica perto da Etec."))

        self.assertEqual(spoken_text, ["A etéqui fica perto da etéqui."])


if __name__ == "__main__":
    unittest.main()
