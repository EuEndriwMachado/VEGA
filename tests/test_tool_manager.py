import unittest
from unittest.mock import patch

from tools.api_manager import ToolManager, ToolSpec
from tools.api_tools import build_tool_registry
from tools.monitoring import MonitoringTools


class ToolManagerTests(unittest.TestCase):
    def test_tool_catalog_has_weather_and_currency(self):
        manager = ToolManager()
        self.assertIsNotNone(manager.get_tool("weather"))
        self.assertIsNotNone(manager.get_tool("currency"))

    def test_resolve_tool_by_keywords(self):
        manager = ToolManager()
        result = manager.resolve_for_text("qual é a previsão do tempo em São Paulo")
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "weather")

    def test_resolve_currency_queries(self):
        manager = ToolManager()
        queries = [
            "qual a cotação do dólar hoje?",
            "quanto tá o euro?",
            "converter 50 dólares em reais",
            "cotação do bitcoin",
            "dólar hoje",
            "câmbio do euro",
        ]
        for query in queries:
            result = manager.resolve_for_text(query)
            self.assertIsNotNone(result, f"Deveria resolver moeda para: '{query}'")
            self.assertEqual(result.name, "currency", f"Esperava 'currency' para: '{query}'")

    def test_false_positives_do_not_trigger_currency_or_weather(self):
        manager = ToolManager()
        false_positives = [
            "isso é real?",
            "qual o valor da pizza?",
            "perdi uma moeda na rua",
            "paguei 20 reais no almoço",
            "não tenho tempo pra isso",
            "quanto tempo vai demorar?",
            "faz tempo que a gente não se fala",
            "ao mesmo tempo podemos fazer outra coisa",
        ]
        for query in false_positives:
            result = manager.resolve_for_text(query)
            self.assertIsNone(
                result,
                f"Consulta casual '{query}' não deve acionar ferramentas acidentalmente (acionou {result.name if result else None})",
            )

    def test_tool_metadata_is_complete(self):
        spec = ToolManager().get_tool("weather")
        self.assertTrue(spec.name)
        self.assertTrue(spec.category)
        self.assertTrue(spec.purpose)
        self.assertIn("location", spec.parameters)

    def test_video_trend_request_resolves_to_embedded_video_tool(self):
        tool = build_tool_registry().resolve_for_text("me mostra os vídeos em alta")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "trending_videos")

    def test_pdf_summary_request_resolves_to_document_tool(self):
        tool = build_tool_registry().resolve_for_text("resume esse PDF")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "summarize_document")

    def test_software_and_homework_intents_are_deferred_to_model_selection(self):
        homework_handler = lambda folder_or_path=None, language=None: "started"
        manager = build_tool_registry(homework_handler=homework_handler)
        phrases = (
            "vamos criar um site",
            "quero montar um aplicativo para organizar meus estudos",
            "preciso resolver as atividades de biologia",
            "me ajuda com o dever que está no PDF",
        )

        assert all(manager.resolve_for_text(phrase) is None for phrase in phrases)
        assert manager.get_tool("create_project").semantic_only is True
        homework = manager.get_tool("solve_homework")
        assert homework.semantic_only is True
        self.assertIs(homework.implementation, homework_handler)

    def test_semantic_tools_are_available_in_function_call_schemas(self):
        from core.brain.ai import _registered_tool_schemas

        manager = build_tool_registry(homework_handler=lambda folder_or_path=None, language=None: "started")
        declarations = {item["name"]: item for item in _registered_tool_schemas(manager.list_tools())}

        self.assertIn("files", declarations["create_project"]["parameters"]["properties"])
        self.assertIn("folder_or_path", declarations["solve_homework"]["parameters"]["properties"])
        self.assertIn("language", declarations["solve_homework"]["parameters"]["properties"])

    def test_pdf_summary_request_resolves_to_document_tool(self):
        tool = build_tool_registry().resolve_for_text("resume esse PDF")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "summarize_document")

    @patch("tools.monitoring.requests.get")
    def test_trending_videos_uses_https_instance_and_limits_results(self, mock_get):
        class DummyResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        registry = [["videos.example", {"api": True, "type": "https"}]]
        videos = [
            {
                "videoId": f"video_{index}",
                "title": f"Vídeo {index}",
                "author": "Canal teste",
                "viewCountText": "1 mil visualizações",
            }
            for index in range(5)
        ]
        mock_get.side_effect = [DummyResponse(registry), DummyResponse(videos)]

        result = MonitoringTools().trending_videos(limit=3)

        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["items"][0]["videoId"], "video_0")
        self.assertEqual(mock_get.call_args_list[1].args[0], "https://videos.example/api/v1/trending")

    @patch("tools.monitoring.requests.get")
    def test_video_search_uses_query_and_requests_autoplay(self, mock_get):
        class DummyResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        registry = [["videos.example", {"api": True, "type": "https"}]]
        video = {
            "videoId": "abcDEF_1234",
            "title": "São Paulo x Santos Brasileirão",
            "author": "Canal de futebol",
            "publishedText": "há 2 horas",
        }
        mock_get.side_effect = [DummyResponse(registry), DummyResponse([video])]

        result = MonitoringTools().trending_videos(
            limit=1,
            query="São Paulo Santos Brasileirão",
        )

        self.assertTrue(result["autoplay"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(
            mock_get.call_args_list[1].kwargs["params"],
            {
                "q": "São Paulo Santos Brasileirão",
                "type": "video",
                "sort_by": "upload_date",
            },
        )


if __name__ == "__main__":
    unittest.main()
