"""Fábrica del modelo de lenguaje.

agente.py solo llama a `get_model()`: no sabe qué proveedor hay detrás. Para cambiar de
modelo se editan las variables del .env (MODEL_PROVIDER / MODEL_NAME).

Proveedores soportados:
- google_genai (principal): Gemini vía AI Studio. `include_thoughts=True` hace que devuelva
  un resumen de su razonamiento, que la UI muestra aparte de la respuesta.
- openrouter / groq (respaldo): se activan con el .env; requieren instalar su paquete.

Reintentos ante 429 (límite del free tier): `max_retries` reintenta con espera exponencial;
si igual se agota, agente.py/app.py lo detectan y la UI muestra un aviso claro.
"""
import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

load_dotenv()

MAX_RETRIES = 4


def get_model() -> BaseChatModel:
    provider = os.getenv("MODEL_PROVIDER", "google_genai")
    name = os.getenv("MODEL_NAME", "gemini-3.5-flash")

    if provider == "google_genai":
        if not os.getenv("GOOGLE_API_KEY"):
            raise RuntimeError("Falta GOOGLE_API_KEY. Copiá .env.example a .env y completalo.")
        return init_chat_model(
            name, model_provider=provider, temperature=0,
            include_thoughts=True, max_retries=MAX_RETRIES,
        )

    if provider == "openrouter":
        if not os.getenv("OPENROUTER_API_KEY"):
            raise RuntimeError("Falta OPENROUTER_API_KEY en el .env.")
        from langchain_openrouter import ChatOpenRouter   # pip install langchain-openrouter

        return ChatOpenRouter(model=name, temperature=0, max_retries=MAX_RETRIES,
                              reasoning={"effort": "medium", "summary": "auto"})

    if provider == "groq":
        if not os.getenv("GROQ_API_KEY"):
            raise RuntimeError("Falta GROQ_API_KEY en el .env.")
        return init_chat_model(name, model_provider="groq", temperature=0, max_retries=MAX_RETRIES)

    raise RuntimeError(f"MODEL_PROVIDER inválido: «{provider}» (google_genai | openrouter | groq).")
