import os
from typing import Optional
from pathlib import Path
from pydantic_settings import BaseSettings

WORKSPACE_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
BACKEND_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    app_name: str = "SaarthiAI"
    environment: str = "development"
    data_path: str = "app/data/schemes.json"
    vector_store_path: str = "app/vector_store/index.faiss"

    offline_only: bool = False

    # Groq (OpenAI-compatible API) - the LLM used for grounded answer generation.
    # Leave GROQ_API_KEY blank to use the offline answer path only. Never commit a real key.
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = 20.0
    # Answer translation providers, tried in order: "machine" (Azure Translator / free Google endpoint) and "groq"
    # (the configured Groq model, used only when Groq is configured). The first translation that passes validation wins.
    translation_provider_order: str = "machine,groq"
    # "direct": for a non-English request Groq writes the answer in that language in ONE call (validated); the
    # English-then-translate path is only the fallback. "translate": always English first, then translate (old path).
    answer_generation_mode: str = "direct"
    # Translate the recommendation-card descriptions with Groq as well (an extra Groq call per question; off by default
    # to stay inside Groq's per-minute token limit). Machine translation is always tried for the cards.
    translate_cards_with_llm: bool = False

    # Legacy Azure OpenAI settings: no longer used by the answer path (kept for rollback / test patch targets).
    azure_openai_endpoint: Optional[str] = None
    azure_openai_api_key: Optional[str] = None
    azure_openai_deployment: Optional[str] = None
    azure_openai_api_version: str = "2025-01-01-preview"

    azure_translator_key: Optional[str] = None
    azure_translator_endpoint: str = "https://api.cognitive.microsofttranslator.com"
    azure_translator_region: str = "eastus"

    azure_speech_key: Optional[str] = None
    azure_speech_region: str = "eastus"

    brightdata_api_token: Optional[str] = None
    brightdata_serp_zone: Optional[str] = None
    brightdata_dc_host: Optional[str] = None
    brightdata_dc_port: Optional[int] = None
    brightdata_dc_user: Optional[str] = None
    brightdata_dc_pass: Optional[str] = None

    data_gov_api_key: Optional[str] = None

    class Config:
        env_file = (str(WORKSPACE_ENV_FILE), str(BACKEND_ENV_FILE), ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
