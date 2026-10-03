"""News, price and page monitoring tools."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests


class MonitoringTools:
    def __init__(self, timeout: float = 8.0, state_path="data/monitoring.json"):
        self.timeout = timeout
        self.state_path = state_path

    def _state(self):
        try:
            return json.loads(open(self.state_path, encoding="utf-8").read())
        except (OSError, ValueError):
            return {"prices": [], "pages": []}

    def _save_state(self, state):
        import os
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)

    def news(self, limit: int = 3) -> dict[str, Any]:
        feeds = [
            ("BBC", "https://feeds.bbci.co.uk/portuguese/rss.xml"),
            ("G1", "https://g1.globo.com/dynamo/rss2.xml"),
            ("Reuters", "https://feeds.reuters.com/reuters/worldNews"),
        ]
        headlines = []
        for source, url in feeds:
            try:
                response = requests.get(url, timeout=self.timeout, headers={"User-Agent": "VEGA/1.0"})
                response.raise_for_status()
                root = ET.fromstring(response.content)
                for item in root.findall("./channel/item")[:2]:
                    title = re.sub(r"\s+", " ", item.findtext("title", "")).strip()
                    link = item.findtext("link", "")
                    if title:
                        headlines.append({"source": source, "title": title, "link": link})
            except Exception:
                continue
        return {"retrieved_at": datetime.now(timezone.utc).isoformat(), "items": headlines[: max(1, int(limit))]}

    def trending_videos(self, limit: int = 1, region: str = "BR", query: str = "") -> dict[str, Any]:
        limit = min(10, max(1, int(limit)))
        region = re.sub(r"[^A-Za-z]", "", str(region or "BR")).upper()[:2] or "BR"
        query = re.sub(r"\s+", " ", str(query or "")).strip()[:160]
        registry_url = "https://api.invidious.io/instances.json?sort_by=api"

        try:
            registry_response = requests.get(
                registry_url,
                timeout=min(self.timeout, 8.0),
                headers={"User-Agent": "VEGA/1.0"},
            )
            registry_response.raise_for_status()
            registry = registry_response.json()
        except Exception as error:
            return {"items": [], "region": region, "error": f"Catálogo de vídeos indisponível: {error}"}

        instances = []
        for entry in registry:
            if not isinstance(entry, list) or len(entry) < 2:
                continue
            host, details = entry[0], entry[1]
            parsed = urlparse(f"https://{host}")
            if (
                isinstance(details, dict)
                and details.get("api")
                and details.get("type") == "https"
                and parsed.scheme == "https"
                and parsed.hostname == host
            ):
                instances.append(parsed.geturl())

        for base_url in instances:
            try:
                params = (
                    {"q": query, "type": "video", "sort_by": "upload_date"}
                    if query
                    else {"region": region}
                )
                endpoint = "search" if query else "trending"
                response = requests.get(
                    f"{base_url}/api/v1/{endpoint}",
                    params=params,
                    timeout=min(self.timeout, 6.0),
                    headers={"User-Agent": "VEGA/1.0"},
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                continue

            videos = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                video_id = str(item.get("videoId") or "")
                title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id) or not title:
                    continue
                videos.append({
                    "videoId": video_id,
                    "title": title[:240],
                    "author": str(item.get("author") or "")[:120],
                    "views": str(item.get("viewCountText") or ""),
                    "published": str(item.get("publishedText") or ""),
                })
                if len(videos) >= limit:
                    break

            if videos:
                return {
                    "items": videos,
                    "region": region,
                    "source": "YouTube",
                    "autoplay": True,
                    "query": query,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }

        return {"items": [], "region": region, "error": "Nenhum vídeo em alta foi encontrado agora."}

    def price(self, symbol: str, source: str = "") -> dict[str, Any]:
        symbol = str(symbol).strip()
        if not symbol:
            raise ValueError("Informe o produto ou símbolo para monitorar.")
        state = self._state()
        entry = {"symbol": symbol, "source": source, "created_at": datetime.now(timezone.utc).isoformat()}
        if entry not in state["prices"]:
            state["prices"].append(entry)
        self._save_state(state)
        return {**entry, "status": "registered", "note": "Use o scheduler para consultar novamente."}

    def page_snapshot(self, url: str) -> dict[str, Any]:
        response = requests.get(url, timeout=self.timeout, headers={"User-Agent": "VEGA/1.0"})
        response.raise_for_status()
        content = response.text
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        state = self._state()
        previous = next((item for item in state["pages"] if item.get("url") == url), None)
        current = {"url": url, "status_code": response.status_code, "hash": digest, "characters": len(content)}
        state["pages"] = [item for item in state["pages"] if item.get("url") != url] + [current]
        self._save_state(state)
        current["changed"] = previous is not None and previous.get("hash") != digest
        return current

    def report(self, title: str, sections: dict[str, Any]) -> dict[str, Any]:
        return {"title": title, "created_at": datetime.now(timezone.utc).isoformat(), "sections": sections}
