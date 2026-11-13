"""Fábrica del modelo de lenguaje.

agente.py solo llama a `get_models()`: no sabe qué proveedor hay detrás. El proveedor y el
modelo se eligen en la barra lateral de la UI; sus valores iniciales salen del .env
(MODEL_PROVIDER / MODEL_NAME / OPENROUTER_MODEL).

Proveedores soportados:
- google_genai (por defecto): Gemini vía AI Studio. `include_thoughts=True` hace que devuelva
  un resumen de su razonamiento, que la UI muestra aparte de la respuesta.
- openrouter: acceso a muchos modelos con una sola clave. El modelo se indica con su slug
  (los gratuitos terminan en ":free"). Hay que elegir uno que soporte tool calling.
- groq: inferencia muy rápida de modelos abiertos (Llama, gpt-oss...). Hay que elegir uno con soporte de tools.

Varias claves: en el .env, `GOOGLE_API_KEY=clave1,clave2` (separadas por coma). Se arma un modelo por
clave y `RotarClaves` pasa a la siguiente cuando la que está en uso se queda sin cupo.

Reintentos ante 429 (límite del free tier): `max_retries` reintenta con espera exponencial;
si igual se agota, agente.py/app.py lo detectan y la UI muestra un aviso claro.
"""
import os
import re

from dotenv import load_dotenv
from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.errors import GraphBubbleUp

load_dotenv()

MAX_RETRIES = 4

# Proveedores que ofrece la UI: id -> nombre para mostrar.
PROVEEDORES = {"google_genai": "Gemini (Google AI Studio)", "openrouter": "OpenRouter", "groq": "Groq"}

# Variable del .env con la clave de cada proveedor.
CLAVES = {"google_genai": "GOOGLE_API_KEY", "openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY"}


def proveedor_por_defecto() -> str:
    return os.getenv("MODEL_PROVIDER", "google_genai")


def modelo_por_defecto(provider: str) -> str:
    """Nombre de modelo inicial de cada proveedor (editable desde la UI)."""
    if provider == "openrouter":
        return os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
    if provider == "groq":
        return os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    return os.getenv("MODEL_NAME", "gemini-3.5-flash")


def claves(provider: str) -> list[str]:
    """Claves del proveedor: una o varias separadas por coma en la variable del .env. Nunca se muestran."""
    valor = os.getenv(CLAVES.get(provider, ""), "")
    return [c.strip().strip("\"'") for c in re.split(r"[,\s]+", valor) if c.strip().strip("\"'")]


def falta_clave(provider: str) -> str | None:
    """Mensaje de error si falta la clave del proveedor (None si está todo bien)."""
    variable = CLAVES.get(provider)
    if variable and not claves(provider):
        return f"Falta {variable} en el .env (ver .env.example)."
    return None


def clasificar_error(error: Exception) -> str:
    """Qué le pasó al modelo: "cuota", "credito", "saturado", "clave" u "otro" (se mira el nombre y el texto del error)."""
    texto = f"{type(error).__name__} {error}".lower()

    def hay(*pistas: str) -> bool:
        return any(p in texto for p in pistas)

    if hay("429", "resource_exhausted", "rate limit", "ratelimit", "quota", "too many requests", "toomanyrequests"):
        return "cuota"
    if hay("402", "payment required", "insufficient credit", "insufficient funds"):
        return "credito"
    if hay("503", "unavailable", "overloaded"):
        return "saturado"
    if hay("401", "403", "api key", "api_key", "unauthorized", "permission_denied", "authentication"):
        return "clave"
    return "otro"


def _construir(provider: str, name: str, clave: str, max_retries: int) -> BaseChatModel:
    """Un modelo usando UNA clave concreta (se pasa explícita: la variable de entorno puede traer varias)."""
    if provider == "google_genai":
        return init_chat_model(
            name, model_provider=provider, temperature=0, api_key=clave,
            include_thoughts=True, max_retries=max_retries,
        )

    if provider == "openrouter":
        from langchain_openrouter import ChatOpenRouter   # pip install langchain-openrouter

        return ChatOpenRouter(model=name, temperature=0, max_retries=max_retries, openrouter_api_key=clave,
                              reasoning={"effort": "medium", "summary": "auto"})

    return init_chat_model(name, model_provider="groq", temperature=0, max_retries=max_retries, api_key=clave)


def get_models(provider: str | None = None, name: str | None = None) -> list[BaseChatModel]:
    """Un modelo por cada clave del proveedor, en el orden del .env."""
    provider = provider or proveedor_por_defecto()
    name = name or modelo_por_defecto(provider)

    if provider not in CLAVES:
        raise RuntimeError(f"Proveedor inválido: «{provider}» (google_genai | openrouter | groq).")
    if error := falta_clave(provider):
        raise RuntimeError(error)

    lista = claves(provider)
    # Con varias claves no tiene sentido reintentar mucho la misma: si no hay cupo, se pasa a la siguiente.
    reintentos = MAX_RETRIES if len(lista) == 1 else 1
    return [_construir(provider, name, c, reintentos) for c in lista]


def get_model(provider: str | None = None, name: str | None = None) -> BaseChatModel:
    """El modelo con la primera clave (para quien no necesita rotar)."""
    return get_models(provider, name)[0]


class RotarClaves(AgentMiddleware):
    """Si la clave en uso se queda sin cupo, el pedido se reintenta con la siguiente.

    Recuerda cuál es la clave que anda (`actual`): no vuelve a probar en cada mensaje una que ya
    está agotada. Solo rota ante errores de CUOTA; cualquier otro error se propaga tal cual.
    Si todas las claves están agotadas, se propaga el último error de cuota.
    """

    def __init__(self, modelos: list[BaseChatModel]):
        super().__init__()
        self.modelos = modelos
        self.actual = 0

    def wrap_model_call(self, request, handler):
        ultimo: Exception | None = None
        for _ in range(len(self.modelos)):
            try:
                return handler(request.override(model=self.modelos[self.actual]))
            except GraphBubbleUp:      # pausas del grafo (confirmación humana): no son errores
                raise
            except Exception as e:
                if clasificar_error(e) != "cuota":
                    raise
                ultimo = e
                self.actual = (self.actual + 1) % len(self.modelos)
        raise ultimo
