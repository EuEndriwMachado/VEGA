from __future__ import annotations

import json
import importlib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional


@dataclass
class ToolSpec:
    name: str
    purpose: str
    category: str
    parameters: List[str]
    auth_required: bool = False
    auth_hint: str = ""
    endpoint: str = ""
    method: str = "GET"
    response_format: str = "json"
    timeout_seconds: float = 10.0
    requirements: List[str] = field(default_factory=list)
    implementation: Optional[Callable[..., Any]] = None
    tags: List[str] = field(default_factory=list)
    semantic_only: bool = False

    def to_dict(self):
        return {
            "name": self.name,
            "purpose": self.purpose,
            "category": self.category,
            "parameters": list(self.parameters),
            "auth_required": self.auth_required,
            "auth_hint": self.auth_hint,
            "endpoint": self.endpoint,
            "method": self.method,
            "response_format": self.response_format,
            "timeout_seconds": self.timeout_seconds,
            "requirements": list(self.requirements),
            "tags": list(self.tags),
            "semantic_only": self.semantic_only,
        }


class ToolManager:
    """Gerencia um catálogo de ferramentas externas e resolve a melhor ferramenta por contexto."""

    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}
        self._register_default_tools()
        self._load_plugins()

    def _load_plugins(self):
        """Load optional ToolSpec providers listed in VEGA_TOOL_MODULES."""
        modules = [name.strip() for name in os.getenv("VEGA_TOOL_MODULES", "").split(",") if name.strip()]
        for module_name in modules:
            try:
                module = importlib.import_module(module_name)
                factory = getattr(module, "register_tools", None)
                if callable(factory):
                    factory(self)
            except Exception as error:
                print(f"[VegaTools] Plugin indisponível {module_name}: {error}")

    def register(self, spec: ToolSpec):
        self._tools[spec.name] = spec
        return spec

    def get_tool(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def execute(self, name: str, **kwargs):
        spec = self.get_tool(name)
        if spec is None or spec.implementation is None:
            raise ValueError(f"Ferramenta '{name}' não encontrada ou sem implementação.")
        return spec.implementation(**kwargs)

    @staticmethod
    def _has_currency_intent(text: str) -> bool:
        """Detecta se há intenção genuína de cotação/câmbio/conversão de moeda."""
        # Palavras-chave explícitas de mercado financeiro
        if re.search(r"\b(?:cotacao|cota[çc][aã]o|c[aâ]mbio|convers[aã]o|converter)\b", text):
            return True
        # Perguntas diretas: "quanto tá o dólar", "preço do dólar", "valor do euro", "dólar hoje", etc.
        if re.search(r"\b(?:quanto\s+(?:t[aá]|est[aá]|vale|custa)|pre[çc]o\s+d[oe]|valor\s+d[oe])\s+(?:o\s+)?(?:d[oó]lar(?:es)?|euro(?:s)?|bitcoin(?:s)?|libras?|reais|iene|peso)\b", text):
            return True
        if re.search(r"\b(?:d[oó]lar(?:es)?|euro(?:s)?|bitcoin(?:s)?|libra(?:s)?)\s+(?:hoje|comercial|turismo|agora)\b", text):
            return True
        # Padrões de conversão com números: "10 dólares em reais", "50 euros para real", "100 reais em dólar"
        if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:d[oó]lar(?:es)?|euro(?:s)?|reais|libras?|bitcoins?|usd|eur|brl)\s*(?:em|para|pra)\s*(?:reais|d[oó]lar(?:es)?|euro(?:s)?|libras?|bitcoins?|usd|eur|brl)\b", text):
            return True
        return False

    @staticmethod
    def _has_weather_intent(text: str) -> bool:
        """Detecta se há intenção genuína de previsão do tempo/clima."""
        # Evitar falsos positivos onde 'tempo' significa duração, hora ou demora
        if re.search(r"\b(?:n[aã]o\s+(?:deu|tive|tenho)\s+tempo|quanto\s+tempo|faz\s+tempo|ao\s+mesmo\s+tempo|perda\s+de\s+tempo|passar\s+o\s+tempo|com\s+o\s+tempo|dar\s+tempo|d[aá]\s+tempo|tempo\s+livre|tempo\s+gasto)\b", text):
            return False
        # Consultas explícitas de meteorologia
        if re.search(r"\b(?:previs[aã]o\s+(?:do\s+)?tempo|clima|meteorologia|sensa[çc][aã]o\s+t[eé]rmica)\b", text):
            return True
        if re.search(r"\b(?:como\s+t[aá]\s+(?:o\s+)?tempo|como\s+est[aá]\s+(?:o\s+)?tempo|tempo\s+hoje|tempo\s+amanh[aã]|tempo\s+(?:em|na|no)\s+[a-z]+)\b", text):
            return True
        if re.search(r"\b(?:vai\s+chover|t[aá]\s+chovendo|chover|chuva\s+(?:hoje|amanh[aã]|agora)|vai\s+fazer\s+(?:frio|calor)|temperatura\s+(?:hoje|amanh[aã]|agora|(?:em|na|no)\s+[a-z]+)|qual\s+a\s+temperatura)\b", text):
            return True
        return False

    # Verbos/expressões que indicam que a pessoa está PEDINDO algo. Sem um
    # deles, uma frase que só MENCIONA o assunto não deve chamar ferramenta:
    # "que dia bonito hoje" é comentário sobre o clima, não um pedido de data;
    # "meu filme favorito é bom" não é consulta de filme.
    _REQUEST_MARKERS = (
        "me diga", "me diz", "qual", "quais", "quanto", "quantos",
        "quando", "onde", "que horas", "que dia", "que data",
        "me mostra", "mostra", "mostre", "exibe", "abre", "abra", "abrir",
        "criar", "crie", "fazer", "faca", "gerar", "gera", "busca", "busque",
        "procura", "procure", "pesquisa", "pesquise", "tira", "tire",
        "envia", "envie", "manda", "mande", "quero", "preciso", "tem como",
        "consegue", "resuma", "resume", "leia", "toque", "toca",
        "adicionar", "adiciona", "marcar", "marca", "colocar", "coloca",
    )

    @classmethod
    def _looks_like_request(cls, normalized: str) -> bool:
        """A fala pede algo, ou só menciona o assunto?"""
        if not normalized:
            return False
        if normalized.strip().endswith("?"):
            return True
        return any(cls._contains_term(normalized, marker) for marker in cls._REQUEST_MARKERS)

    @staticmethod
    def _has_time_intent(text: str) -> bool:
        """Intenção genuína de saber a HORA (não menção casual a tempo).

        "que horas são" pergunta a hora. "que horas o filme começa?" pergunta
        sobre o filme — a hora aparece como detalhe de outra pergunta. Para
        distinguir, exigimos que a fala ACABE na pergunta de hora, sem outro
        assunto pendente depois.
        """
        if re.search(
            r"\b(?:que horas (?:sao|e|agora)|horas sao|me diga (?:a|as) horas?|"
            r"qual (?:a|as) horas?|a hora agora|horario agora|que horario)\b\s*\??$",
            text,
        ):
            return True

        # "que horas" seguido de SUJEITO e VERBO de outro assunto (o filme
        # comeca, a loja abre) é pergunta sobre esse assunto, não sobre a hora.
        if re.search(r"\bque horas\b", text):
            return not bool(re.search(
                r"\bque horas\b\s+(?:o|a|os|as|um|uma)?\s*\w+\s+"
                r"(?:comeca|comecou|abre|abriu|fecha|fechou|passa|sai|chega|"
                r"termina|acaba|vai|inicia|iniciou)\b",
                text,
            ))

        return bool(re.search(r"\b(?:hora|horario)\b", text)) and bool(
            re.search(r"\b(?:que|qual|me diga|me diz|agora|sao)\b", text)
        )

    @staticmethod
    def _has_date_intent(text: str) -> bool:
        """Intenção genuína de saber a DATA.

        Exige que "dia" seja o OBJETO da pergunta. "que dia é hoje" pergunta;
        "que dia bonito hoje" é comentário sobre o clima — o adjetivo depois de
        "dia" entrega o elogio, não a pergunta. Sem esta distinção, a VEGA
        respondia a data para quem estava só comentando o tempo.
        """
        # Adjetivo logo depois de "dia" => comentário, não pergunta.
        if re.search(
            r"\bdia\s+(?:bonito|feio|lindo|maravilhoso|otimo|tranquilo|calmo|"
            r"chuvoso|nublado|ensolarado|quente|frio|longo|dificil|cansativo|bom)\b",
            text,
        ):
            return False
        # "que dia" seguido de OUTRO assunto (você nasceu, ele chegou) não é
        # pedido da data de hoje.
        if re.search(r"\bque dia\b\s+(?:\w+\s+)?(?:voce|vc|ele|ela|nos|eu)\b", text):
            return False
        return bool(re.search(
            r"\b(?:que dia|qual (?:o )?dia|que data|qual (?:a )?data|hoje e que dia|"
            r"data de hoje|dia de hoje|me diga (?:o )?dia|em que dia)\b",
            text,
        ))

    def resolve_for_text(self, text: str) -> Optional[ToolSpec]:
        if not text:
            return None
        normalized = self._normalize(text)
        candidates: List[tuple[int, ToolSpec]] = []

        # Intent checks prioritários para ferramentas críticas
        is_currency = self._has_currency_intent(normalized)
        is_weather = self._has_weather_intent(normalized)
        is_time = self._has_time_intent(normalized)
        is_date = self._has_date_intent(normalized)

        if is_currency and "currency" in self._tools:
            return self._tools["currency"]

        if is_weather and "weather" in self._tools:
            return self._tools["weather"]

        # Hora e data: usam o MESMO padrão estrito de intenção. Sem ele,
        # "que dia bonito hoje" (comentário sobre o clima) virava consulta de
        # data e a VEGA respondia a data em vez de conversar.
        if is_time and "current_time" in self._tools:
            return self._tools["current_time"]

        if is_date and "current_date" in self._tools:
            return self._tools["current_date"]

        # Sem NENHUM indício de pedido, a fala é conversa — não chame ferramenta.
        # É a proteção contra a frase aleatória que virava chamada de ferramenta.
        if not self._looks_like_request(normalized):
            return None

        # Fala que apenas MENCIONA hora/data não deve cair em current_time nem
        # current_date pelo score de tags ("dia" aparece em "que dia bonito").
        # Essas duas só entram quando passaram pelo filtro estrito acima.
        _intent_guarded = {"current_time", "current_date"}

        # Aliases refinados para evitar disparos acidentais
        aliases = {
            "geocoding": ["onde fica", "localizacao", "localização", "geocoding", "coordenadas de"],
            "timezone": ["fuso horario", "fuso de", "hora local em", "que horas sao em"],
            "country": ["capital de", "populacao de", "dados do pais", "continente de"],
            "dictionary": ["qual o significado", "significado de", "significado da palavra", "definicao de"],
            "movie": ["sinopse do filme", "filme do", "filme sobre", "serie sobre", "cinema"],
            "calendar": ["criar evento", "adicionar na agenda", "novo compromisso", "marcar na agenda"],
            "email": ["enviar email", "mandar email", "redigir email"],
            "whatsapp": ["enviar whatsapp", "mandar mensagem no whatsapp", "zap para"],
            "vscode": ["abrir no vscode", "abrir projeto no vscode", "abrir no visual studio code"],
            "create_document": ["criar documento", "gerar relatorio", "criar arquivo markdown"],
            "create_presentation": ["criar apresentacao", "slides sobre", "roteiro de apresentacao"],
            "report": ["relatorio executivo", "relatorio vega", "resumo executivo"],
            "trending_videos": ["videos em alta", "videos populares", "youtube em alta", "principais videos", "videos da internet", "abre os videos", "abre os videos em alta", "mostra os videos em alta", "me mostra os videos", "videos do momento"],
            "news": ["noticias de hoje", "abre as noticias", "abra as noticias", "mostra as noticias", "mostre as noticias", "leia as noticias", "manchetes de hoje", "principais noticias", "o que aconteceu hoje", "novidades de hoje"],
            "summarize_document": ["resume pdf", "resuma pdf", "resume esse pdf", "resuma esse pdf", "leia pdf", "leitura de pdf"],
        }

        for spec in self._tools.values():
            if spec.semantic_only:
                continue
            if spec.name in {"currency", "weather"}:
                # Já avaliados com filtros estritos de intenção
                continue
            if spec.name in _intent_guarded:
                # Hora e data só por intenção explícita (ver acima).
                continue

            score = 0
            score += sum(3 for tag in spec.tags if self._contains_term(normalized, tag))
            score += sum(2 for parameter in spec.parameters if self._contains_term(normalized, parameter))
            for alias in aliases.get(spec.name, []):
                if self._contains_term(normalized, alias):
                    score += 6
            if score >= 6:
                candidates.append((score, spec))

        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    @staticmethod
    def _contains_term(text: str, term: str) -> bool:
        return bool(
            re.search(
                rf"(?<![a-z0-9áàãâéêíóôúç]){re.escape(term)}(?![a-z0-9áàãâéêíóôúç])",
                text,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = text.lower().strip()
        normalized = "".join(
            character
            for character in unicodedata.normalize("NFKD", normalized)
            if not unicodedata.combining(character)
        )
        normalized = re.sub(r"[^a-z0-9\sáàãâéêíóôúç\-]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    def _register_default_tools(self):
        self.register(
            ToolSpec(
                name="weather",
                purpose="Consulta clima e previsão do tempo por cidade ou coordenadas.",
                category="clima",
                parameters=["location", "city", "country", "lat", "lon"],
                auth_required=False,
                auth_hint="Sem chave obrigatória para Open-Meteo.",
                endpoint="https://api.open-meteo.com/v1/forecast",
                method="GET",
                response_format="json",
                timeout_seconds=10.0,
                requirements=["HTTPS", "janela de timeout"],
                tags=["tempo", "clima", "previsao", "temperatura", "chuva", "vento"],
            )
        )

        self.register(
            ToolSpec(
                name="currency",
                purpose="Consulta taxas de câmbio e conversão entre moedas.",
                category="financas",
                parameters=["base", "target", "amount", "currency"],
                auth_required=False,
                auth_hint="Sem chave obrigatória para AwesomeAPI ou Frankfurter.",
                endpoint="https://economia.awesomeapi.com.br/last/",
                method="GET",
                response_format="json",
                timeout_seconds=10.0,
                requirements=["HTTPS", "tratamento de erro"],
                tags=["moeda", "cambio", "cotacao", "dolar", "euro"],
            )
        )

        self.register(
            ToolSpec(
                name="geocoding",
                purpose="Converte cidade/endereço em coordenadas geográficas ou vice-versa.",
                category="geografia",
                parameters=["city", "address", "country", "lat", "lon"],
                auth_required=False,
                auth_hint="Sem chave para OpenStreetMap Nominatim por padrão.",
                endpoint="https://nominatim.openstreetmap.org/search",
                method="GET",
                response_format="json",
                timeout_seconds=10.0,
                requirements=["HTTPS", "usuário-agente", "rate limit"],
                tags=["cidade", "localizacao", "geocoding", "endereco", "mapa"],
            )
        )

        self.register(
            ToolSpec(
                name="timezone",
                purpose="Consulta fuso horário por localização ou coordenadas.",
                category="geografia",
                parameters=["location", "lat", "lon", "timezone"],
                auth_required=False,
                auth_hint="Sem chave obrigatória para APIs públicas.",
                endpoint="https://timeapi.io/api/Time/current/coordinate",
                method="GET",
                response_format="json",
                timeout_seconds=10.0,
                requirements=["HTTPS", "lat/lon"],
                tags=["fuso", "horario", "timezone", "hora local"],
            )
        )

        self.register(
            ToolSpec(
                name="country",
                purpose="Consulta dados básicos de países e geografia.",
                category="geografia",
                parameters=["country", "code", "name"],
                auth_required=False,
                auth_hint="Sem chave para REST Countries.",
                endpoint="https://restcountries.com/v3.1/all",
                method="GET",
                response_format="json",
                timeout_seconds=10.0,
                requirements=["HTTPS"],
                tags=["pais", "país", "geografia", "capital", "continente"],
            )
        )

        self.register(
            ToolSpec(
                name="dictionary",
                purpose="Busca definições de palavras em dicionários públicos.",
                category="linguagem",
                parameters=["word", "term", "language"],
                auth_required=False,
                auth_hint="Sem chave para dicionários públicos.",
                endpoint="https://api.dictionaryapi.dev/api/v2/entries/en/",
                method="GET",
                response_format="json",
                timeout_seconds=10.0,
                requirements=["HTTPS"],
                tags=["dicionario", "significado", "palavra", "tradução"],
            )
        )

        self.register(
            ToolSpec(
                name="movie",
                purpose="Consulta informações de filmes e séries.",
                category="entretenimento",
                parameters=["title", "movie", "series", "query"],
                auth_required=False,
                auth_hint="Depende da API escolhida e pode exigir chave.",
                endpoint="https://www.omdbapi.com/",
                method="GET",
                response_format="json",
                timeout_seconds=10.0,
                requirements=["HTTPS", "API key opcional"],
                tags=["filme", "série", "cinema", "ator", "titulo"],
            )
        )
