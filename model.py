"""Fábrica del modelo de lenguaje.

agente.py solo llama a `get_model()`: no sabe qué proveedor hay detrás. El proveedor y el
modelo se eligen en la barra lateral de la UI; sus valores iniciales salen del .env
(MODEL_PROVIDER / MODEL_NAME / OPENROUTER_MODEL).

Proveedores soportados:
- google_genai (por defecto): Gemini vía AI Studio. `include_thoughts=True` hace que devuelva
  un resumen de su razonamiento, que la UI muestra aparte de la respuesta.
- openrouter: acceso a muchos modelos con una sola clave. El modelo se indica con su slug
  (los gratuitos terminan en ":free"). Hay que elegir uno que soporte tool calling.
- groq (solo por .env, no aparece en la UI): requiere instalar `langchain-groq`.

Reintentos ante 429 (límite del free tier): `max_retries` reintenta con espera exponencial;
si igual se agota, agente.py/app.py lo detectan y la UI muestra un aviso claro.
"""
import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

load_dotenv()

MAX_RETRIES = 4

# Proveedores que ofrece la UI: id -> nombre para mostrar.
PROVEEDORES = {"google_genai": "Gemini (Google AI Studio)", "openrouter": "OpenRouter"}

# Variable del .env con la clave de cada proveedor.
CLAVES = {"google_genai": "GOOGLE_API_KEY", "openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY"}


def proveedor_por_defecto() -> str:
    return os.getenv("MODEL_PROVIDER", "google_genai")


def modelo_por_defecto(provider: str) -> str:
    """Nombre de modelo inicial de cada proveedor (editable desde la UI)."""
    if provider == "openrouter":
        return os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
    return os.getenv("MODEL_NAME", "gemini-3.5-flash")


def falta_clave(provider: str) -> str | None:
    """Mensaje de error si falta la clave del proveedor (None si está todo bien)."""
    variable = CLAVES.get(provider)
    if variable and not os.getenv(variable):
        return f"Falta {variable} en el .env (ver .env.example)."
    return None


def get_model(provider: str | None = None, name: str | None = None) -> BaseChatModel:
    provider = provider or proveedor_por_defecto()
    name = name or modelo_por_defecto(provider)

    if provider not in CLAVES:
        raise RuntimeError(f"Proveedor inválido: «{provider}» (google_genai | openrouter | groq).")
    if error := falta_clave(provider):
        raise RuntimeError(error)

    if provider == "google_genai":
        return init_chat_model(
            name, model_provider=provider, temperature=0,
            include_thoughts=True, max_retries=MAX_RETRIES,
        )

    if provider == "openrouter":
        from langchain_openrouter import ChatOpenRouter   # pip install langchain-openrouter

        return ChatOpenRouter(model=name, temperature=0, max_retries=MAX_RETRIES,
                              reasoning={"effort": "medium", "summary": "auto"})

    return init_chat_model(name, model_provider="groq", temperature=0, max_retries=MAX_RETRIES)
