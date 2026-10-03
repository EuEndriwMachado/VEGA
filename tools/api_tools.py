from __future__ import annotations

import os
from typing import Any, Dict, Optional

from tools.api_client import ExternalToolClient
from tools.api_manager import ToolManager, ToolSpec
from tools.productivity import ProductivityTools
from tools.monitoring import MonitoringTools


WMO_WEATHER_CODES = {
    0: "céu limpo",
    1: "predominantemente limpo",
    2: "parcialmente nublado",
    3: "nublado",
    45: "com nevoeiro",
    48: "com neblina",
    51: "com garoa leve",
    53: "com garoa moderada",
    55: "com garoa densa",
    56: "com garoa gelada",
    57: "com garoa congelante",
    61: "com chuva fraca",
    63: "com chuva moderada",
    65: "com chuva forte",
    66: "com chuva congelante",
    67: "com chuva torrencial",
    71: "com neve fraca",
    73: "com neve moderada",
    75: "com neve forte",
    77: "com granizo fino",
    80: "com pancadas de chuva",
    81: "com pancadas de chuva moderadas",
    82: "com pancadas de chuva fortes",
    85: "com pancadas de neve",
    86: "com nevasca",
    95: "com tempestade e trovoadas",
    96: "com tempestade e granizo",
    99: "com tempestade forte e granizo",
}


class WeatherTool:
    name = "weather"

    def __init__(self, client: Optional[ExternalToolClient] = None):
        self.client = client or ExternalToolClient()

    def execute(self, location: str = "", city: str = "", lat: Optional[float] = None, lon: Optional[float] = None):
        query = location or city
        if (lat is None or lon is None) and not query:
            raise ValueError("Informe a localização, cidade ou coordenadas para consultar o clima.")

        display_name = query
        if lat is None or lon is None:
            geocoder = GeocodingTool(self.client)
            geo_res = geocoder.execute(city=query)
            lat = geo_res["lat"]
            lon = geo_res["lon"]
            display_name = geo_res.get("city") or geo_res.get("display_name") or query

        params = {
            "latitude": lat,
            "longitude": lon,
            "current": (
                "temperature_2m,apparent_temperature,precipitation,rain,"
                "weather_code,wind_speed_10m,wind_direction_10m,"
                "relative_humidity_2m,is_day"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "apparent_temperature_max,apparent_temperature_min,"
                "precipitation_sum,rain_sum,precipitation_probability_max,"
                "wind_speed_10m_max"
            ),
            "timezone": "auto",
            "forecast_days": 7,
        }

        data = self.client.get_json("https://api.open-meteo.com/v1/forecast", params=params)
        current = data.get("current", {})
        daily = data.get("daily", {})

        weather_code = current.get("weather_code")
        is_day = bool(current.get("is_day", 1))
        condition = WMO_WEATHER_CODES.get(weather_code, "tempo estável")
        if weather_code == 0:
            condition = "ensolarado e céu limpo" if is_day else "céu limpo e noite estrelada"

        daily_max_list = daily.get("temperature_2m_max", [])
        daily_min_list = daily.get("temperature_2m_min", [])
        daily_rain_list = daily.get("precipitation_probability_max", [])

        temp_max = daily_max_list[0] if daily_max_list else None
        temp_min = daily_min_list[0] if daily_min_list else None
        rain_prob = daily_rain_list[0] if daily_rain_list else None

        # Previsão completa dos próximos 7 dias
        dates = daily.get("time", [])
        daily_codes = daily.get("weather_code", [])
        max_apparent = daily.get("apparent_temperature_max", [])
        min_apparent = daily.get("apparent_temperature_min", [])
        precipitation_sum = daily.get("precipitation_sum", [])
        rain_sum = daily.get("rain_sum", [])
        max_wind = daily.get("wind_speed_10m_max", [])

        forecast = []
        for i, date in enumerate(dates):
            forecast.append(
                {
                    "date": date,
                    "weather_code": daily_codes[i] if i < len(daily_codes) else None,
                    "condition": WMO_WEATHER_CODES.get(
                        daily_codes[i] if i < len(daily_codes) else None,
                        "tempo estável",
                    ),
                    "temperature_max": daily_max_list[i] if i < len(daily_max_list) else None,
                    "temperature_min": daily_min_list[i] if i < len(daily_min_list) else None,
                    "feels_like_max": max_apparent[i] if i < len(max_apparent) else None,
                    "feels_like_min": min_apparent[i] if i < len(min_apparent) else None,
                    "precipitation": precipitation_sum[i] if i < len(precipitation_sum) else None,
                    "rain": rain_sum[i] if i < len(rain_sum) else None,
                    "rain_probability": daily_rain_list[i] if i < len(daily_rain_list) else None,
                    "wind_speed_max": max_wind[i] if i < len(max_wind) else None,
                }
            )

        return {
            "source": "open-meteo",
            "location": display_name,
            "latitude": float(lat),
            "longitude": float(lon),
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_kmh": current.get("wind_speed_10m"),
            "wind_direction": current.get("wind_direction_10m"),
            "rain_mm": current.get("rain"),
            "precipitation_mm": current.get("precipitation"),
            "weather_code": weather_code,
            "condition": condition,
            "is_day": is_day,
            "temp_max": temp_max,
            "temp_min": temp_min,
            "rain_prob": rain_prob,
            "forecast": forecast,
            "data": data,
        }


class CurrencyTool:
    name = "currency"

    def __init__(self, client: Optional[ExternalToolClient] = None):
        self.client = client or ExternalToolClient()

    def execute(self, base: str = "USD", target: str = "BRL", amount: float = 1.0):
        base_code = (base or "USD").upper().strip()
        target_code = (target or "BRL").upper().strip()
        amount_value = float(amount or 1.0)

        # 1. Tentar AwesomeAPI para cotações em tempo real envolvendo BRL
        if target_code == "BRL" or base_code == "BRL":
            query_pair = f"{base_code}-BRL" if target_code == "BRL" else f"{target_code}-BRL"
            key = query_pair.replace("-", "")
            try:
                data = self.client.get_json(f"https://economia.awesomeapi.com.br/last/{query_pair}")
                if key in data:
                    item = data[key]
                    bid = float(item["bid"])
                    high = float(item.get("high", bid))
                    low = float(item.get("low", bid))
                    pct_change = float(item.get("pctChange", 0.0))
                    name = item.get("name", f"{base_code}/{target_code}")

                    if target_code == "BRL":
                        rate = bid
                        rate_high = high
                        rate_low = low
                        variation = pct_change
                    else:
                        rate = 1.0 / bid if bid > 0 else 0.0
                        rate_high = (1.0 / low) if low > 0 else rate
                        rate_low = (1.0 / high) if high > 0 else rate
                        variation = -pct_change

                    direction = "alta" if variation > 0.05 else "baixa" if variation < -0.05 else "estável"

                    return {
                        "source": "awesomeapi",
                        "base": base_code,
                        "target": target_code,
                        "name": name,
                        "amount": amount_value,
                        "rate": float(rate),
                        "converted": amount_value * float(rate),
                        "high": float(rate_high),
                        "low": float(rate_low),
                        "pct_change": float(variation),
                        "direction": direction,
                        "data": data,
                    }
            except Exception:
                # Se falhar a AwesomeAPI, continua para fallback no Frankfurter
                pass

        # 2. Fallback / Pares internacionais: Frankfurter API
        url = "https://api.frankfurter.app/latest"
        data = self.client.get_json(url, params={"from": base_code, "to": target_code})
        rate = data.get("rates", {}).get(target_code)
        if rate is None:
            raise ValueError(f"Taxa para {target_code} não encontrada.")
        rate_val = float(rate)
        return {
            "source": "frankfurter",
            "base": base_code,
            "target": target_code,
            "amount": amount_value,
            "rate": rate_val,
            "converted": amount_value * rate_val,
            "data": data,
        }


class GeocodingTool:
    name = "geocoding"

    def __init__(self, client: Optional[ExternalToolClient] = None):
        self.client = client or ExternalToolClient()

    def execute(self, city: str = "", address: str = ""):
        query = (city or address).strip()
        if not query:
            raise ValueError("Informe uma cidade ou endereço.")

        # 1. Tentar Open-Meteo Geocoding (rápido, sem bloqueio de rate-limit)
        try:
            geo_data = self.client.get_json(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": query, "count": 1, "language": "pt", "format": "json"},
            )
            results = geo_data.get("results", [])
            if results:
                first = results[0]
                city_name = first.get("name", query)
                admin1 = first.get("admin1", "")
                country = first.get("country", "")
                parts = [p for p in [city_name, admin1, country] if p]
                return {
                    "source": "open-meteo",
                    "query": query,
                    "city": city_name,
                    "state": admin1,
                    "country": country,
                    "lat": float(first.get("latitude", 0.0)),
                    "lon": float(first.get("longitude", 0.0)),
                    "display_name": ", ".join(parts),
                    "data": geo_data,
                }
        except Exception:
            pass

        # 2. Fallback para Nominatim
        data = self.client.get_json(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "jsonv2", "limit": 1},
            headers={"Accept-Language": "pt-BR"},
        )
        if not data:
            raise ValueError(f"Nenhum resultado encontrado para '{query}'.")
        first = data[0]
        return {
            "source": "nominatim",
            "query": query,
            "lat": float(first.get("lat", 0.0)),
            "lon": float(first.get("lon", 0.0)),
            "display_name": first.get("display_name"),
            "data": data,
        }


def build_tool_registry(homework_handler=None) -> ToolManager:
    manager = ToolManager()
    productivity = ProductivityTools()
    monitoring = MonitoringTools()
    manager.register(
        ToolSpec(
            name="weather",
            purpose="Consulta clima e previsão do tempo por cidade ou coordenadas.",
            category="clima",
            parameters=["location", "city", "country", "lat", "lon"],
            auth_required=False,
            auth_hint="Sem chave para Open-Meteo.",
            endpoint="https://api.open-meteo.com/v1/forecast",
            method="GET",
            response_format="json",
            timeout_seconds=10.0,
            requirements=["HTTPS", "timeout"],
            tags=["tempo", "clima", "previsao", "temperatura", "chuva", "vento"],
            implementation=WeatherTool().execute,
        )
    )
    manager.register(
        ToolSpec(
            name="currency",
            purpose="Consulta câmbio e conversão entre moedas.",
            category="financas",
            parameters=["base", "target", "amount", "currency"],
            auth_required=False,
            auth_hint="Sem chave para AwesomeAPI ou Frankfurter.",
            endpoint="https://economia.awesomeapi.com.br/last/",
            method="GET",
            response_format="json",
            timeout_seconds=10.0,
            requirements=["HTTPS"],
            tags=["moeda", "cambio", "cotacao", "dolar", "euro", "bitcoin"],
            implementation=CurrencyTool().execute,
        )
    )
    manager.register(
        ToolSpec(
            name="geocoding",
            purpose="Converte cidade/endereço em coordenadas geográficas.",
            category="geografia",
            parameters=["city", "address", "country", "lat", "lon"],
            auth_required=False,
            auth_hint="Sem chave para OSM Nominatim.",
            endpoint="https://nominatim.openstreetmap.org/search",
            method="GET",
            response_format="json",
            timeout_seconds=10.0,
            requirements=["HTTPS", "rate limit"],
            tags=["cidade", "localizacao", "geocoding", "endereco"],
            implementation=GeocodingTool().execute,
        )
    )
    manager.register(ToolSpec(
        name="dictionary", purpose="Busca uma definição ou explicação de palavra.", category="linguagem",
        parameters=["word", "term"], tags=["dicionario", "significado", "palavra", "definicao"],
        endpoint="https://api.dictionaryapi.dev/api/v2/entries/en/", implementation=DictionaryTool().execute,
    ))
    manager.register(ToolSpec(
        name="movie", purpose="Consulta filmes e séries.", category="entretenimento",
        parameters=["title", "movie", "series"], tags=["filme", "serie", "cinema", "titulo"],
        endpoint="https://www.omdbapi.com/", implementation=MovieTool().execute,
    ))
    manager.register(ToolSpec(
        name="find_files", purpose="Localiza arquivos pelo nome ou caminho.", category="produtividade",
        parameters=["query", "path"], tags=["arquivo", "ficheiro", "encontrar", "localizar"], implementation=productivity.find_files,
    ))
    manager.register(ToolSpec(
        name="read_file", purpose="Lê um arquivo de texto.", category="produtividade",
        parameters=["path"], tags=["ler arquivo", "abrir arquivo", "conteudo"], implementation=productivity.read_text,
    ))
    manager.register(ToolSpec(
        name="summarize_document", purpose="Extrai e resume o texto de um PDF ou documento textual local.", category="produtividade",
        parameters=["path"], tags=["resumir", "documento", "pdf", "texto"], implementation=productivity.summarize_document,
    ))
    manager.register(ToolSpec(
        name="read_spreadsheet", purpose="Lê linhas de uma planilha CSV.", category="produtividade",
        parameters=["path"], tags=["planilha", "csv", "tabela"], implementation=productivity.read_csv,
    ))
    manager.register(ToolSpec(name="calendar", purpose="Cria um evento de calendário local.", category="produtividade", parameters=["title", "start", "end"], tags=["calendario", "agenda", "evento"], implementation=productivity.create_calendar_event))
    manager.register(ToolSpec(name="email", purpose="Prepara um e-mail no cliente local.", category="comunicacao", parameters=["recipient", "subject", "body"], tags=["email", "e-mail"], implementation=productivity.compose_email))
    manager.register(ToolSpec(name="whatsapp", purpose="Prepara uma mensagem do WhatsApp.", category="comunicacao", parameters=["recipient", "message"], tags=["whatsapp", "mensagem"], implementation=productivity.compose_whatsapp))
    manager.register(ToolSpec(name="vscode", purpose="Abre uma pasta ou arquivo no Visual Studio Code.", category="desenvolvimento", parameters=["path"], tags=["vscode", "editor"], implementation=productivity.open_in_vscode))
    manager.register(ToolSpec(
        name="create_project",
        purpose=(
            "Use quando a pessoa quiser criar ou desenvolver um site, aplicativo ou outro software, "
            "mesmo que não diga literalmente 'criar projeto'. Gere todos os arquivos necessários "
            "em files, com o caminho relativo como chave e o conteúdo completo como valor; a VEGA "
            "mostra os arquivos aparecendo progressivamente no VS Code. Não use para listas escolares."
        ),
        category="desenvolvimento",
        parameters=["path", "files", "open_in_editor"],
        tags=["projeto", "site", "aplicativo", "programacao", "codigo"],
        implementation=productivity.create_project,
        semantic_only=True,
    ))
    if callable(homework_handler):
        manager.register(ToolSpec(
            name="solve_homework",
            purpose=(
                "Use quando a pessoa quiser fazer, resolver ou receber ajuda com uma lista de exercícios, "
                "atividade, dever ou trabalho escolar, inclusive quando a intenção estiver formulada "
                "indiretamente. Reuse o fluxo existente para localizar ou selecionar o PDF e gerar as "
                "soluções. Informe folder_or_path ou language apenas quando a pessoa os mencionar. "
                "Não use para listas de compras, tarefas ou listas genéricas."
            ),
            category="educacao",
            parameters=["folder_or_path", "language"],
            tags=["escola", "dever", "atividade", "exercicios", "lista escolar"],
            implementation=homework_handler,
            semantic_only=True,
        ))
    manager.register(ToolSpec(name="create_document", purpose="Cria um documento Markdown.", category="produtividade", parameters=["path", "title", "content"], tags=["documento", "relatorio"], implementation=productivity.create_document))
    manager.register(ToolSpec(name="create_presentation", purpose="Cria um roteiro de apresentação em Markdown.", category="produtividade", parameters=["path", "title", "slides"], tags=["apresentacao", "slides"], implementation=productivity.create_presentation_outline))
    manager.register(ToolSpec(
        name="news", purpose="Busca as principais notícias em RSS.", category="informacao",
        parameters=["limit"], tags=["noticia", "noticias", "manchetes", "atualidades"], implementation=monitoring.news,
    ))
    manager.register(ToolSpec(
        name="trending_videos",
        purpose="Busca vídeos populares do YouTube e os exibe para reprodução incorporada na interface da VEGA.",
        category="entretenimento",
        parameters=["limit", "region", "query"],
        endpoint="https://api.invidious.io/instances.json",
        timeout_seconds=14.0,
        requirements=["HTTPS", "instância pública de catálogo", "reprodução incorporada"],
        tags=["videos em alta", "videos populares", "youtube em alta", "video popular", "tendencias do youtube"],
        implementation=monitoring.trending_videos,
    ))
    manager.register(ToolSpec(
        name="page_snapshot", purpose="Consulta uma página e calcula sua assinatura.", category="monitoramento",
        parameters=["url"], tags=["monitorar pagina", "acompanhar pagina", "site mudou"], implementation=monitoring.page_snapshot,
    ))
    manager.register(ToolSpec(
        name="price_monitor", purpose="Registra um item para monitoramento de preço.", category="monitoramento",
        parameters=["symbol", "source"], tags=["preco", "produto", "monitorar preco"], implementation=monitoring.price,
    ))
    manager.register(ToolSpec(
        name="report", purpose="Monta um relatório estruturado a partir do pedido.", category="produtividade",
        parameters=["title", "sections"], tags=["relatorio", "resumo executivo"], implementation=monitoring.report,
    ))
    return manager


class DictionaryTool:
    def __init__(self, client: Optional[ExternalToolClient] = None):
        self.client = client or ExternalToolClient()

    def execute(self, word: str = "", term: str = ""):
        value = (word or term).strip()
        if not value:
            raise ValueError("Informe a palavra.")
        data = self.client.get_json(f"https://api.dictionaryapi.dev/api/v2/entries/en/{value}")
        meanings = data[0].get("meanings", []) if data else []
        definitions = [d.get("definition") for m in meanings for d in m.get("definitions", []) if d.get("definition")]
        return {"word": value, "definitions": definitions[:5], "data": data}


class MovieTool:
    def __init__(self, client: Optional[ExternalToolClient] = None):
        self.client = client or ExternalToolClient()

    def execute(self, title: str = "", movie: str = "", series: str = ""):
        value = (title or movie or series).strip()
        if not value:
            raise ValueError("Informe o título.")
        data = self.client.get_json("https://www.omdbapi.com/", params={"apikey": os.getenv("OMDB_API_KEY", "",), "t": value})
        return {"title": value, "result": data}
