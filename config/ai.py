import os
from dataclasses import dataclass, field
from typing import Optional
from config.environment import load_vega_environment

load_vega_environment()


def normalize_gemini_model_name(value):
    if not value:
        return ""

    candidate = str(value).strip()
    if not candidate:
        return ""

    if candidate.startswith("models/"):
        return candidate
    if candidate.startswith("gemini-"):
        return f"models/{candidate}"
    return candidate


def normalize_gemini_live_model_name(value):
    if not value:
        return ""

    candidate = str(value).strip()
    if not candidate:
        return ""

    if candidate.startswith("models/"):
        return candidate
    if candidate.startswith("gemini-"):
        return f"models/{candidate}"
    return candidate


# Chaves e configuração por ambiente. O código não registra modelos fixos por provedor.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "")
OPENROUTER_FALLBACK_MODEL = os.getenv("OPENROUTER_FALLBACK_MODEL", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "")
OPENAI_FALLBACK_MODEL = os.getenv("OPENAI_FALLBACK_MODEL", "")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip() or "http://localhost:11434"
# Sem fallback forçado: deixar OLLAMA_MODEL vazio permite que o Gemini assuma.
# Preencha no .env apenas quando quiser rodar 100% offline com Ollama local.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "").strip()
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = normalize_gemini_model_name(os.getenv("GEMINI_MODEL_NAME", ""))
GEMINI_FALLBACK_MODEL = normalize_gemini_model_name(os.getenv("GEMINI_FALLBACK_MODEL", ""))
GEMINI_LIVE_MODEL = normalize_gemini_live_model_name(os.getenv("GEMINI_LIVE_MODEL", ""))

# ==========================================================
# TYPED PROVIDER SETTINGS
# ==========================================================
#
# Carregamento tipado da configuração de provedores. Erros de
# configuração (variáveis ausentes, modelos vazios) aparecem no
# startup em vez de falharem silenciosamente em runtime.

class ConfigurationError(RuntimeError):
    """Erro levantado quando uma configuração essencial está ausente."""

@dataclass(frozen=True)
class ProviderSettings:
    """Configuração de um único provedor de IA."""

    name: str
    api_key: str
    model: str
    fallback_model: Optional[str] = None
    @property
    def configured(self) -> bool:
        """True quando o provedor tem chave e modelo preenchidos."""
        return bool(self.api_key and self.model)

    def require(self) -> "ProviderSettings":
        """Levanta ConfigurationError se o provedor estiver incompleto."""
        if not self.api_key:
            raise ConfigurationError(
                f"Provedor '{self.name}': variável de ambiente da API key ausente."
            )
        if not self.model:
            raise ConfigurationError(
                f"Provedor '{self.name}': modelo não configurado."
            )
        return self
@dataclass(frozen=True)
class AISettings:
    """Agrega todos os provedores e parâmetros de geração tipados."""

    openrouter: ProviderSettings
    openai: ProviderSettings
    gemini: ProviderSettings
    ollama_base_url: str
    ollama_model: str
    ollama_fallback_model: str
    temperature: float = 0.25
    context_size: int = 8192
    max_output_tokens: int = 512
    max_tool_output_tokens: int = 4096
    max_history: int = 5
    def providers(self) -> tuple:
        return (self.openrouter, self.openai, self.gemini)

    def require_any_provider(self) -> "ProviderSettings":
        """Retorna o primeiro provedor completo ou levanta ConfigurationError."""
        for provider in self.providers():
            if provider.configured:
                return provider
        raise ConfigurationError(
            "Nenhum provedor de IA configurado. Defina OPENROUTER_API_KEY, "
            "OPENAI_API_KEY ou GEMINI_API_KEY e o respectivo modelo."
        )

TEMPERATURE = 0.25
# Contexto e geração maiores para evitar respostas cortadas em perguntas simples.
CONTEXT_SIZE = 8192
# Sem limites artificiais agressivos para perguntas curtinhas.
MAX_OUTPUT_TOKENS = 512
MAX_TOOL_OUTPUT_TOKENS = 4096
MAX_HISTORY = 5

def load_ai_settings() -> AISettings:
    """Monta um AISettings tipado a partir das variáveis de ambiente."""
    return AISettings(
        openrouter=ProviderSettings(
            name="openrouter",
            api_key=OPENROUTER_API_KEY,
            model=OPENROUTER_MODEL,
            fallback_model=OPENROUTER_FALLBACK_MODEL or None,
        ),
        openai=ProviderSettings(
            name="openai",
            api_key=OPENAI_API_KEY,
            model=OPENAI_MODEL_NAME,
            fallback_model=OPENAI_FALLBACK_MODEL or None,
        ),
        gemini=ProviderSettings(
            name="gemini",
            api_key=GEMINI_API_KEY,
            model=GEMINI_MODEL_NAME or GEMINI_LIVE_MODEL,
            fallback_model=GEMINI_FALLBACK_MODEL or None,
        ),
        ollama_base_url=OLLAMA_BASE_URL,
        ollama_model=OLLAMA_MODEL,
        ollama_fallback_model=OLLAMA_FALLBACK_MODEL,
        temperature=TEMPERATURE,
        context_size=CONTEXT_SIZE,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        max_tool_output_tokens=MAX_TOOL_OUTPUT_TOKENS,
        max_history=MAX_HISTORY,
    )

# Instância única carregada no import; erros de configuração surfaceam aqui.
AI_SETTINGS = load_ai_settings()
