# -*- coding: utf-8 -*-
"""Catálogo unificado de capacidades da VEGA.

Por que existe
--------------
A VEGA tinha duas camadas de capacidade, cada uma com o seu formato:

1. as **regras** do parser de comandos (`core.actions.intent_catalog.Rule`),
   que reconhecem a fala e executam localmente, sem custo;
2. as **ferramentas** registradas (`tools.api_manager.ToolSpec`), que chamam
   APIs externas e são oferecidas ao modelo via tool calling.

Elas têm papéis diferentes e continuam existindo — mas ninguém conseguia
responder à pergunta simples "quais capacidades a VEGA tem?" sem consultar as
duas. Cada consumidor (roteador semântico, diagnóstico, interface) montava a
resposta do jeito que dava.

Este módulo NÃO substitui nenhuma das duas camadas: ele as AGREGA numa leitura
só, normalizada. Nenhum recurso é removido e nenhum formato existente muda —
as regras continuam executando pelo catálogo do parser e as ferramentas pelo
`ToolManager`.

Também era aqui que morava o corte cego de capacidades: o roteador semântico
recebia apenas as 45 primeiras e ficava cego para o resto do catálogo. A
seleção por relevância (`select_relevant`) resolve isso sem inflar o custo do
prompt: as capacidades mais próximas da fala são escolhidas, não as primeiras
da lista.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# Tipos de capacidade. A distinção importa: uma comando local é gratuita e
# determinística; uma ferramenta pode custar rede, tempo e chave de API.
KIND_COMMAND = "command"
KIND_TOOL = "tool"

# Descrições em prosa das regras locais, para o índice de relevância.
# Sem elas o nome cru ("shutdown pc") vira a única pista, e uma fala ambígua
# como "deixa o pc mais claro" acabava trazendo "desligar o pc" como opção.
_COMMAND_DESCRIPTIONS = {
    "cancel_shutdown": "cancelar o desligamento agendado do computador",
    "restart_pc": "reiniciar o computador",
    "shutdown_pc": "desligar o computador",
    "screen_off": "apagar a tela do computador",
    "lock_pc": "bloquear o computador",
    "bluetooth_on": "ligar o bluetooth",
    "bluetooth_off": "desligar o bluetooth",
    "notifications_on": "ativar as notificacoes",
    "notifications_off": "desativar as notificacoes",
    "brightness_max": "aumentar o brilho da tela ao maximo",
    "brightness_min": "baixar o brilho da tela ao minimo",
    "brightness_up": "aumentar o brilho da tela",
    "brightness_down": "diminuir o brilho da tela",
    "volume_mute": "silenciar o som do computador",
    "volume_unmute": "reativar o som do computador",
    "volume_max": "aumentar o volume ao maximo",
    "volume_min": "baixar o volume ao minimo",
    "volume_up": "aumentar o volume do computador",
    "volume_down": "diminuir o volume do computador",
    "media_play_pause": "pausar ou retomar a musica que esta tocando",
    "media_next": "pular para a proxima faixa",
    "media_previous": "voltar para a faixa anterior",
    "media_stop": "parar a musica",
    "play_music": "tocar uma musica ou playlist no youtube ou spotify",
    "play_music_generic": "tocar musica",
    "play_funk": "tocar funk",
    "play_personal_playlist": "tocar uma playlist pessoal do usuario",
    "save_personal_playlist": "salvar uma playlist pessoal",
    "list_personal_playlists": "listar as playlists pessoais",
    "close_named_app": "fechar um aplicativo pelo nome",
    "close_window": "fechar a janela ativa",
    "minimize_all": "minimizar todas as janelas",
    "maximize_window": "maximizar a janela ativa",
    "restore_window": "restaurar a janela ativa",
    "minimize_window": "minimizar a janela ativa",
    "switch_window": "alternar entre janelas abertas",
    "fullscreen": "ativar tela cheia",
    "select_all": "selecionar tudo",
    "copy": "copiar a selecao",
    "paste": "colar",
    "cut": "recortar a selecao",
    "undo": "desfazer a ultima acao",
    "redo": "refazer a ultima acao",
    "save": "salvar o arquivo atual",
    "new": "criar um arquivo novo",
    "alt_tab": "alternar para a janela anterior",
    "search_youtube": "pesquisar um video no youtube",
    "search_google": "pesquisar no google",
    "open_site_google": "abrir o google no navegador",
    "open_site_youtube": "abrir o youtube",
    "open_site_canva": "abrir o canva",
    "open_site_whatsapp": "abrir o whatsapp web",
    "open_site_spotify": "abrir o spotify",
    "open_folder_documents": "abrir a pasta de documentos",
    "open_folder_downloads": "abrir a pasta de downloads",
    "open_folder_desktop": "abrir a area de trabalho",
    "open_named_app": "abrir um programa ou aplicativo pelo nome",
}

# Palavras que não ajudam a distinguir capacidades (conectores, verbos de
# recheio, pronomes). Ficam fora do índice de relevância para não criar ruído.
_STOP_WORDS = frozenset({
    "a", "o", "as", "os", "um", "uma", "de", "do", "da", "dos", "das", "em",
    "no", "na", "nos", "nas", "por", "para", "pra", "com", "sem", "e", "ou",
    "que", "qual", "quando", "onde", "como", "meu", "minha", "seu", "sua",
    "isso", "isto", "aquilo", "esse", "essa", "este", "esta", "ele", "ela",
    "eu", "voce", "você", "me", "te", "se", "ja", "já", "ai", "aí", "por favor",
    "vega", "favor", "agora", "entao", "então", "faz", "fazer",
})


def _fold(text: str) -> str:
    """Minúsculas, sem acentos e sem pontuação — para comparar palavras."""
    value = str(text or "").lower()
    value = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(text: str) -> List[str]:
    return [token for token in _fold(text).split() if token and token not in _STOP_WORDS]


@dataclass
class Capability:
    """Uma coisa que a VEGA sabe fazer, independentemente de como é executada.

    name:        identificador estável (nome da regra ou da ferramenta).
    kind:        KIND_COMMAND (local) ou KIND_TOOL (externa).
    domain:      agrupamento legível (sistema, midia, apps, clima...).
    priority:    ordem de preferência quando as duas camadas competem; menor
                 primeiro. Espelha as faixas de `intent_catalog`.
    description: o que a capacidade faz, em linguagem natural (vai ao prompt).
    parameters:  nomes dos parâmetros aceitos (só ferramentas costumam ter).
    tags:        palavras que ajudam a reconhecer o pedido na fala.
    auth_required: exige autorização adicional (PIN/rosto) para executar.
    semantic_only: só pode ser disparada por escolha do modelo, nunca por
                 reconhecimento lexical (estruturas complexas).
    """

    name: str
    kind: str = KIND_COMMAND
    domain: str = "geral"
    priority: int = 100
    description: str = ""
    parameters: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    auth_required: bool = False
    semantic_only: bool = False

    def search_terms(self) -> List[str]:
        """Termos que descrevem a capacidade, para casar com a fala."""
        terms = [self.name, self.domain, self.description, *self.tags, *self.parameters]
        return _tokens(" ".join(str(term) for term in terms if term))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "domain": self.domain,
            "priority": self.priority,
            "description": self.description,
            "parameters": list(self.parameters),
            "tags": list(self.tags),
            "auth_required": self.auth_required,
            "semantic_only": self.semantic_only,
        }

    def describe(self) -> Dict[str, str]:
        """Formato mínimo consumido pelo roteador semântico.

        Mantido como dicionário (e não como o objeto inteiro) porque o prompt
        do roteador só precisa de nome e domínio — e é assim que ele já
        consumia o inventário do parser antes.
        """
        return {"name": self.name, "domain": self.domain}


class CapabilityCatalog:
    """Conjunto agregado de capacidades, com seleção por relevância."""

    def __init__(self, capabilities: Optional[Iterable[Capability]] = None):
        self._capabilities: List[Capability] = list(capabilities or [])

    # ------------------------------------------------------------------
    # Construção a partir das camadas vivas
    # ------------------------------------------------------------------

    def add(self, capability: Capability) -> Capability:
        self._capabilities.append(capability)
        return capability

    @classmethod
    def from_parser(cls, commands_parser) -> "CapabilityCatalog":
        """Lê as regras do catálogo do parser de comandos."""
        return cls(_command_capabilities(commands_parser))

    @classmethod
    def from_tool_manager(cls, tool_manager) -> "CapabilityCatalog":
        """Lê as ferramentas registradas no `ToolManager`."""
        return cls(_tool_capabilities(tool_manager))

    @classmethod
    def build(cls, commands_parser=None, tool_manager=None) -> "CapabilityCatalog":
        """Agrega as duas camadas numa leitura só."""
        catalog = cls()
        if commands_parser is not None:
            for capability in _command_capabilities(commands_parser):
                catalog.add(capability)
        if tool_manager is not None:
            for capability in _tool_capabilities(tool_manager):
                catalog.add(capability)
        return catalog

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    def capabilities(self) -> List[Capability]:
        return list(self._capabilities)

    def sorted_capabilities(self) -> List[Capability]:
        """Ordem determinística: prioridade e, depois, nome."""
        return sorted(self._capabilities, key=lambda item: (item.priority, item.name))

    def get(self, name: str) -> Optional[Capability]:
        for capability in self._capabilities:
            if capability.name == name:
                return capability
        return None

    def names(self) -> List[str]:
        return [capability.name for capability in self._capabilities]

    def describe(self) -> List[Dict[str, str]]:
        """Inventário mínimo (nome + domínio), na ordem de prioridade.

        É o formato que o roteador semântico consome.
        """
        return [item.describe() for item in self.sorted_capabilities()]

    def inventory(self) -> List[Dict[str, Any]]:
        """Inventário completo, para diagnóstico e interface."""
        return [item.to_dict() for item in self.sorted_capabilities()]

    # ------------------------------------------------------------------
    # Seleção por relevância
    # ------------------------------------------------------------------

    # Termos que aparecem no nome de várias capacidades e descrevem o CONTEXTO
    # (o computador, o aparelho), não a AÇÃO. Eles não distinguem uma capacidade
    # de outra: "deixa o pc mais claro" é sobre brilho, mas casaria com
    # "shutdown_pc" só porque ambos falam de "pc". Contam menos na pontuação.
    _GENERIC_TERMS = frozenset({
        "pc", "computador", "maquina", "aparelho", "dispositivo", "tela",
        "som", "audio", "janela", "app", "aplicativo", "programa", "arquivo",
        "video", "musica", "site", "pagina",
    })

    # Especificidade: um termo raro e distintivo vale mais que um termo comum.
    _WEIGHT_OWN = 3
    _WEIGHT_DOMAIN = 1

    def select_relevant(self, text: str, limit: int = 45) -> List[Capability]:
        """Devolve as capacidades mais prováveis para esta fala.

        O roteador semântico só deve consultar o modelo quando o parser local
        falhou. Mandar o catálogo inteiro no prompt custaria caro em cada
        chamada de fallback; cortar as primeiras N (como era feito antes)
        deixava metade do catálogo invisível para a IA.

        A seleção pontua cada capacidade pelos termos que a fala compartilha
        com ela (nome, domínio, descrição, tags e parâmetros) e mantém sempre
        um piso de cobertura: se a fala não casar com nada, as capacidades de
        maior prioridade (as mais genéricas e seguras) entram na lista, para o
        modelo nunca ficar sem opções.

        Devolve uma lista ORDENADA por relevância, limitada a `limit`.
        """
        limit = max(1, int(limit))
        capabilities = self.sorted_capabilities()
        if len(capabilities) <= limit:
            return capabilities

        spoken = set(_tokens(text))
        scored = []
        for index, capability in enumerate(capabilities):
            own_terms = set(_tokens(f"{capability.name} {capability.description}"))
            domain_terms = set(capability.tags)
            own_hits = 0
            for term in spoken & own_terms:
                own_hits += 1 if term in self._GENERIC_TERMS else self._WEIGHT_OWN
            domain_hits = 0
            for term in spoken & domain_terms:
                domain_hits += 1 if term in self._GENERIC_TERMS else self._WEIGHT_DOMAIN
            score = own_hits + domain_hits
            # Evidência só de CONTEXTO não basta: "deixa o pc mais claro"
            # compartilha "pc" com "desligar o pc", mas isso não faz da fala um
            # pedido de energia. Se todos os termos casados forem genéricos
            # (pc, tela, som...), a capacidade é rebaixada — quem tiver um termo
            # de AÇÃO (claro, brilho, desliga) ganha a disputa.
            action_hits = (spoken & own_terms) - self._GENERIC_TERMS
            if not action_hits and (spoken & own_terms):
                score -= 1
            # Empate: a MENOR prioridade vence, mantendo as ações de energia
            # (prioridade ~0) longe do topo de uma fala ambígua.
            scored.append((score, -capability.priority, -index, capability))

        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in scored[:limit]]

    def describe_relevant(self, text: str, limit: int = 45) -> List[Dict[str, str]]:
        """Inventário mínimo das capacidades relevantes para a fala."""
        return [item.describe() for item in self.select_relevant(text, limit=limit)]


# ----------------------------------------------------------------------
# Adaptadores das camadas existentes
# ----------------------------------------------------------------------

# Sinônimos por DOMÍNIO. As regras locais são reconhecedores: elas não têm
# tags. Sem um vocabulário que a fala realmente usa ("mais claro", "escuro",
# "barulho", "som"), uma paráfrase nunca prevista não casa com nada — e o
# roteador semântico acaba recebendo opções erradas (era assim que "deixa o pc
# mais claro" trazia "desligar o pc" como candidata).
_DOMAIN_TERMS = {
    "brightness": ("brilho", "claro", "escuro", "luminosidade", "tela", "clarear", "escurecer"),
    "volume": ("volume", "som", "audio", "barulho", "alto", "baixo", "silenciar", "silencia", "mudo", "muta", "mutar", "silencio"),
    "energia": ("desligar", "reiniciar", "energia"),
    "midia": ("musica", "tocar", "toca", "playlist", "faixa", "youtube", "spotify", "video"),
    "sistema": ("bluetooth", "notificacoes"),
    "janelas": ("janela", "fechar", "minimizar", "maximizar", "alternar"),
    "teclado": ("atalho", "copiar", "colar", "recortar", "salvar", "desfazer"),
    "apps": ("aplicativo", "programa", "app"),
    "busca": ("pesquisar", "buscar", "procurar", "google", "youtube"),
}


def _domain_terms(name: str, domain: str) -> List[str]:
    """Vocabulário de busca de uma regra.

    O "domínio" de uma regra é organizacional (brilho, volume e bluetooth são
    todos "sistema"), então usar o domínio como assunto dava às regras de
    brilho o vocabulário de bluetooth. O assunto real vem do NOME da regra:
    o prefixo antes do underscore ("brightness", "volume", "media"...).
    """
    value = str(name or "").lower()
    subject = value.split("_", 1)[0] if "_" in value else value
    terms = list(_DOMAIN_TERMS.get(subject, ()))
    if not terms:
        terms = list(_DOMAIN_TERMS.get(str(domain or "").lower(), ()))
    return terms


def _command_capabilities(commands_parser) -> List[Capability]:
    """Converte as `Rule` do parser em `Capability`.

    A conversão é somente de forma: o nome, o domínio e a prioridade vêm
    exatamente dos que já governam a execução local. O parser continua sendo
    quem executa — aqui só descrevemos o que ele sabe fazer.
    """
    catalog = getattr(commands_parser, "_intent_catalog", None)
    if catalog is None:
        return []

    capabilities = []
    for rule in catalog.sorted_rules():
        name = str(getattr(rule, "name", "") or "")
        domain = str(getattr(rule, "domain", "") or "geral")
        capabilities.append(Capability(
            name=name,
            kind=KIND_COMMAND,
            domain=domain,
            priority=int(getattr(rule, "priority", 100) or 100),
            description=_COMMAND_DESCRIPTIONS.get(name) or _humanize_capability_name(name),
            tags=_domain_terms(name, domain),
        ))
    return [item for item in capabilities if item.name]


def _tool_capabilities(tool_manager) -> List[Capability]:
    """Converte as `ToolSpec` do `ToolManager` em `Capability`."""
    if tool_manager is None:
        return []
    try:
        specs = tool_manager.list_tools()
    except Exception:
        return []

    # As ferramentas entram DEPOIS das regras locais: quando as duas camadas
    # puderem resolver a mesma fala, a execução local (gratuita e offline)
    # continua vencendo.
    capabilities = []
    for spec in specs:
        parameters = [
            str(parameter)
            for parameter in (getattr(spec, "parameters", None) or [])
            if parameter
        ]
        tags = [
            str(tag)
            for tag in (getattr(spec, "tags", None) or [])
            if tag
        ]
        capabilities.append(Capability(
            name=str(getattr(spec, "name", "") or ""),
            kind=KIND_TOOL,
            domain=str(getattr(spec, "category", "") or "ferramentas"),
            priority=200,
            description=str(getattr(spec, "purpose", "") or ""),
            parameters=parameters,
            tags=tags,
            auth_required=bool(getattr(spec, "auth_required", False)),
            semantic_only=bool(getattr(spec, "semantic_only", False)),
        ))
    return [item for item in capabilities if item.name]


def _humanize_capability_name(name: Any) -> str:
    """Transforma "volume_up" em "volume up" (texto pesquisável).

    A descrição de uma regra local não existe em prosa: as regras são
    reconhecedores. O nome é o resumo fiel do que elas fazem, então ele é
    exposto em forma legível por palavras — o suficiente para o índice de
    relevância e para leitura humana no diagnóstico.
    """
    value = str(name or "").strip()
    if not value:
        return ""
    return value.replace("_", " ").strip()


def tokenize(text: str) -> List[str]:
    """Exposto para quem quiser reusar a mesma tokenização da seleção."""
    return _tokens(text)


def fold(text: str) -> str:
    """Exposto para quem quiser reusar a mesma normalização da seleção."""
    return _fold(text)


def load_capability_catalog(brain) -> Optional[CapabilityCatalog]:
    """Monta o catálogo a partir de um `VegaBrain` já construído.

    Devolve None (sem levantar) quando o cérebro ainda não tem as camadas —
    o chamador simplesmente segue sem o catálogo enriquecido.
    """
    try:
        parser = getattr(brain, "commands_parser", None)
        tools = getattr(brain, "tool_manager", None)
        if parser is None and tools is None:
            return None
        return CapabilityCatalog.build(commands_parser=parser, tool_manager=tools)
    except Exception:
        return None
