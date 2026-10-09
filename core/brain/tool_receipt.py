# -*- coding: utf-8 -*-
"""Recibo estruturado do último dado verificado obtido por uma ferramenta.

Problema que este módulo resolve
-------------------------------
Quando o usuário pergunta "qual a cotação do dólar hoje", a VEGA responde pela
ferramenta ``currency`` (custo zero de token). No turno seguinte, "e por que
isso?", o caminho da ferramenta já retornou e nada guardou QUAL dado concreto
foi obtido: a resposta entra no histórico como fala da VEGA, e o prompt manda o
modelo NÃO tratar fala da VEGA como fato. Resultado: a IA responde "porque o
quê?".

Aqui cada execução de ferramenta deixa um recibo estruturado (valores reais,
não texto solto). O recibo é injetado como bloco de sistema no turno seguinte,
então a IA tem o fato — não uma paráfrase — e consegue responder perguntas
sobre ele.

O recibo é dado de API com origem explícita, então não sofre a regra
"não trate resposta da VEGA como fato do usuário".
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# Quanto tempo um dado de ferramenta continua sendo "o dado da conversa".
# Cotação e clima mudam devagar; passado isso, o recibo perde validade e a IA
# deve consultar de novo em vez de responder com informação velha.
DEFAULT_TTL_SECONDS = 900.0


def _fmt_number(value: Any, decimals: int = 2) -> str:
    """Formata número no padrão brasileiro (vírgula decimal)."""
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals <= 0:
        text = f"{number:,.0f}"
    else:
        text = f"{number:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _fmt_signed_pct(value: Any) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    direction = "alta" if number > 0.05 else "queda" if number < -0.05 else "estável"
    magnitude = _fmt_number(abs(number), 2)
    if direction == "estável":
        return "estável hoje"
    return f"{direction} de {magnitude}% hoje"


@dataclass
class ToolReceipt:
    """Um dado verificado, com origem, resumo legível e valores brutos."""

    tool: str
    summary: str
    facts: Dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    user_question: str = ""
    topic: str = ""

    def is_fresh(self, now: Optional[float] = None) -> bool:
        reference = time.time() if now is None else now
        return (reference - self.at) <= self.ttl_seconds

    def age_seconds(self, now: Optional[float] = None) -> float:
        reference = time.time() if now is None else now
        return max(0.0, reference - self.at)

    def age_label(self, now: Optional[float] = None) -> str:
        age = self.age_seconds(now)
        if age < 60:
            return f"há {int(age)}s"
        if age < 3600:
            return f"há {int(age // 60)}min"
        return f"há {int(age // 3600)}h"

    def to_context_block(self, now: Optional[float] = None) -> str:
        """Bloco de sistema para o próximo turno. Vazio se o dado esfriou."""
        if not self.is_fresh(now):
            return ""
        lines = [
            "DADO VERIFICADO NESTA CONVERSA (obtido por consulta real, "
            f"{self.age_label(now)}):",
            f"- {self.summary}",
        ]
        extra = self._extra_lines()
        if extra:
            lines.extend(extra)
        lines.append(
            "- Use este dado quando a pessoa se referir a ele (\"e por que isso?\", "
            "\"isso vai subir?\", \"e amanhã?\"). Ele é um fato consultado, não uma "
            "fala sua; não invente números diferentes destes."
        )
        return "\n".join(lines)

    def _extra_lines(self) -> list:
        facts = self.facts or {}
        tool = self.tool
        lines = []

        if tool == "currency":
            variation = _fmt_signed_pct(facts.get("pct_change"))
            if variation:
                lines.append(f"- Variação do dia: {variation}")
            high, low = _fmt_number(facts.get("high")), _fmt_number(facts.get("low"))
            if high and low:
                lines.append(f"- Máxima do dia: {high} | Mínima do dia: {low}")
            amount = facts.get("amount")
            converted = _fmt_number(facts.get("converted"))
            if amount and converted and float(amount) != 1.0:
                lines.append(
                    f"- Conversão pedida: {_fmt_number(amount)} "
                    f"{facts.get('base') or ''} = {converted} {facts.get('target') or ''}"
                )

        elif tool == "weather":
            location = facts.get("location") or ""
            temp = facts.get("temperature_c")
            feels = facts.get("feels_like_c")
            condition = facts.get("condition") or ""
            if location:
                lines.append(f"- Local: {location}")
            if temp is not None:
                lines.append(f"- Temperatura agora: {_fmt_number(temp, 0)}°C")
            if feels is not None:
                lines.append(f"- Sensação térmica: {_fmt_number(feels, 0)}°C")
            if condition:
                lines.append(f"- Condição: {condition}")
            if facts.get("temp_max") is not None and facts.get("temp_min") is not None:
                lines.append(
                    f"- Máxima/Mínima previstas: {_fmt_number(facts.get('temp_max'), 0)}°C"
                    f" / {_fmt_number(facts.get('temp_min'), 0)}°C"
                )
            if facts.get("rain_prob") is not None:
                lines.append(f"- Chance de chuva: {_fmt_number(facts.get('rain_prob'), 0)}%")

        elif tool == "geocoding":
            if facts.get("display_name"):
                lines.append(f"- Local encontrado: {facts['display_name']}")
            if facts.get("lat") is not None and facts.get("lon") is not None:
                lines.append(f"- Coordenadas: {facts['lat']}, {facts['lon']}")

        elif tool == "dictionary":
            definitions = facts.get("definitions") or []
            for index, definition in enumerate(definitions[:3], 1):
                lines.append(f"- Definição {index}: {definition}")

        elif tool == "movie":
            data = facts.get("result") or {}
            if data.get("Title"):
                lines.append(f"- Título: {data['Title']}")
            if data.get("Year"):
                lines.append(f"- Ano: {data['Year']}")
            if data.get("Plot"):
                lines.append(f"- Sinopse: {data['Plot']}")

        elif tool == "news":
            for index, item in enumerate((facts.get("items") or [])[:3], 1):
                title = str(item.get("title") or "").strip()
                source = str(item.get("source") or "").strip()
                if title:
                    lines.append(f"- Manchete {index} ({source}): {title}")

        elif tool == "summarize_document":
            preview = re.sub(r"\s+", " ", str(facts.get("preview") or "")).strip()
            if preview:
                lines.append(f"- Prévia do documento: {preview[:1200]}")

        elif tool == "trending_videos":
            for index, item in enumerate((facts.get("items") or [])[:3], 1):
                title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
                if title:
                    lines.append(f"- Vídeo {index}: {title[:140]}")

        return lines


def build_receipt(tool_name: str, result: Any, user_question: str = "") -> Optional[ToolReceipt]:
    """Converte o resultado bruto de uma ferramenta em recibo estruturado.

    Devolve ``None`` quando o resultado não tem dado factual aproveitável
    (erro, vazio ou ferramenta sem mapeamento) — nesse caso nada é guardado,
    para não injetar lixo no contexto do próximo turno.
    """
    if not isinstance(result, dict):
        return None
    if result.get("error"):
        return None

    tool = str(tool_name or "").strip()
    if not tool:
        return None

    facts: Dict[str, Any] = {}
    summary = ""
    topic = ""

    if tool == "currency":
        rate = result.get("rate")
        if rate is None:
            return None
        base = result.get("base", "USD")
        target = result.get("target", "BRL")
        rate_text = _fmt_number(rate, 4 if target != "BRL" else 2)
        variation = _fmt_signed_pct(result.get("pct_change"))
        summary = f"Cotação {base}→{target}: {rate_text}"
        if variation:
            summary += f" ({variation})"
        topic = f"a cotação {base}→{target}"
        facts = {
            "base": base,
            "target": target,
            "rate": rate,
            "high": result.get("high"),
            "low": result.get("low"),
            "pct_change": result.get("pct_change"),
            "amount": result.get("amount"),
            "converted": result.get("converted"),
            "source": result.get("source"),
        }

    elif tool == "weather":
        temperature = result.get("temperature_c")
        if temperature is None and isinstance(result.get("data"), dict):
            temperature = (result["data"].get("current") or {}).get("temperature_2m")
        if temperature is None:
            return None
        location = result.get("location") or "sua região"
        condition = result.get("condition") or "tempo estável"
        summary = (
            f"Clima em {location}: {_fmt_number(temperature, 0)}°C, {condition}"
        )
        topic = f"o clima em {location}"
        facts = {
            "location": location,
            "temperature_c": temperature,
            "feels_like_c": result.get("feels_like_c"),
            "condition": condition,
            "temp_max": result.get("temp_max"),
            "temp_min": result.get("temp_min"),
            "rain_prob": result.get("rain_prob"),
            "humidity": result.get("humidity"),
        }

    elif tool == "geocoding":
        if not result.get("display_name") and result.get("lat") is None:
            return None
        summary = f"Localização: {result.get('display_name') or result.get('query') or 'consultado'}"
        topic = f"a localização de {result.get('display_name') or result.get('query') or 'local consultado'}"
        facts = {
            "display_name": result.get("display_name"),
            "lat": result.get("lat"),
            "lon": result.get("lon"),
            "city": result.get("city"),
        }

    elif tool == "dictionary":
        definitions = result.get("definitions") or []
        if not definitions:
            return None
        summary = f"Definições de {result.get('word')}"
        topic = f"o significado de {result.get('word')}"
        facts = {"word": result.get("word"), "definitions": definitions[:3]}

    elif tool == "movie":
        data = result.get("result") or {}
        if not data.get("Title"):
            return None
        summary = f"Filme: {data.get('Title')} ({data.get('Year', 'ano desconhecido')})"
        topic = f"o filme {data.get('Title')}"
        facts = {"title": result.get("title"), "result": data}

    elif tool == "news":
        items = result.get("items") or []
        if not items:
            return None
        summary = f"{len(items)} manchete(s) consultada(s) agora"
        topic = "as notícias de hoje"
        facts = {"items": items[:3]}

    elif tool == "trending_videos":
        items = result.get("items") or []
        if not items:
            return None
        first = str(items[0].get("title") or "").strip()
        summary = f"Vídeo em alta: {first[:140]}" if first else "Vídeos em alta consultados"
        topic = "os vídeos em alta"
        facts = {"items": items[:3]}

    elif tool == "summarize_document":
        preview = str(result.get("preview") or "").strip()
        if not preview:
            return None
        summary = f"Documento resumido ({result.get('pages') or '?'} páginas)"
        topic = "o documento que você pediu para resumir"
        facts = {"preview": preview, "pages": result.get("pages"), "path": result.get("path")}

    elif tool in {"create_presentation_pptx", "create_document_docx", "create_spreadsheet_xlsx", "create_mindmap_image"}:
        path = str(result.get("path") or "").strip()
        if not path:
            return None
        created_for = {
            "create_presentation_pptx": "a apresentação",
            "create_document_docx": "o documento",
            "create_spreadsheet_xlsx": "a planilha",
            "create_mindmap_image": "o mapa mental",
        }.get(tool, "o arquivo")
        name = Path(path).name
        summary = f"{created_for.capitalize()} criado(s): {name}"
        topic = created_for
        facts = {"path": path, "kind": result.get("kind")}

    elif tool == "edit_video":
        path = str(result.get("path") or "").strip()
        if not path:
            return None
        operation = str(result.get("operation") or "edit").replace("video_", "")
        summary = f"Vídeo editado ({operation}): {Path(path).name}"
        topic = "o vídeo editado"
        facts = {"path": path, "source": result.get("source"), "operation": operation}

    else:
        return None

    return ToolReceipt(
        tool=tool,
        summary=summary,
        facts=facts,
        user_question=str(user_question or "").strip(),
        topic=topic,
    )
