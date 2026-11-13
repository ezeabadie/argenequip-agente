"""Agente ArgenEquip: create_agent + memoria + confirmación humana, con streaming de eventos.

`stream_turn` ejecuta un turno (o retoma uno pausado) y emite *eventos* (razonamiento,
llamada a tool, resultado, texto, pedido de aprobación) a medida que el agente avanza.
`saved_turns` reconstruye los mismos eventos desde la conversación guardada. Cada interfaz
(consola, Streamlit) solo los presenta.

Consola:    python agente.py
Streamlit:  streamlit run app.py
"""
import math
import re
import sqlite3
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from model import PROVEEDORES, get_model, modelo_por_defecto, proveedor_por_defecto
from tools import TOOLS, WRITE_TOOLS, listar_skills

# La fecha se fija al iniciar el programa (suficiente para una sesión de demo).
HOY = date.today()

SYSTEM_PROMPT = f"""Sos el asistente de ArgenEquip, un sistema de alquiler de herramientas y equipos para obra. \
Ayudás a un socio a operar el sistema. Respondé siempre en español rioplatense, de forma breve y clara.
La fecha de hoy es {HOY:%d-%m-%Y} (DD-MM-AAAA). Usala para interpretar fechas como «el lunes» o «del 24 al 28».
FECHAS: con el socio usá SIEMPRE el formato DD-MM-AAAA (día primero, por ejemplo 24-11-2026): cuando le pidas \
una fecha, pedísela en ese formato, y mostrale las fechas así. Las tools también reciben DD-MM-AAAA.

COMANDOS (el socio puede escribirlos solos o como frase libre): nuevo, resumen, historial, cancelar.
Antes de ejecutar un comando, cargá su skill con load_skill y seguí su procedimiento. Skills disponibles: {listar_skills()}.
Cuando uses una skill, nombrala en tu respuesta (por ejemplo «Siguiendo la skill nuevo…»).

REGLAS
- Nunca inventes ids (clientes, domicilios, equipos, alquileres): obtenelos siempre con las tools.
- Si hay más de un cliente o más de un domicilio posible, preguntale al socio cuál. No elijas por él.
- No calcules montos ni fechas de cabeza: el valor total lo dice el socio y los números de los \
reportes los da la tool tal cual.
- Verificá la disponibilidad de cada equipo antes de crear un alquiler.
- crear_alquiler pide confirmación al socio automáticamente. No pidas permiso por texto: llamala \
cuando tengas todos los datos y, si el socio la rechaza, preguntale qué quiere cambiar.
- Si una tool devuelve ERROR, explicalo en palabras simples y proponé cómo seguir.
- Solo están disponibles los comandos nuevo, resumen e historial. Si piden otra cosa, decí que todavía no se puede hacer desde acá.
"""

DIR = Path(__file__).parent
CONV_FILE = DIR / "conversaciones.txt"   # un thread_id por línea; el último es el activo

# Único middleware: confirmación humana en las tools de ESCRITURA. Justificación: crear un
# alquiler modifica el sistema real y es difícil de deshacer; el interrupt del grafo
# garantiza la pausa, un prompt no (el modelo podría ignorarlo).
def build_agent(model, checkpointer):
    """Arma el agente. Separado del `agent` global para poder probarlo con un modelo falso."""
    return create_agent(
        model=model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        middleware=[HumanInTheLoopMiddleware(
            interrupt_on={t.name: {"allowed_decisions": ["approve", "reject"]} for t in WRITE_TOOLS},
            description_prefix="Se necesita tu confirmación",
        )],
        checkpointer=checkpointer,   # memoria de corto plazo: un hilo (thread_id) por conversación
    )


# El checkpointer es uno solo: al cambiar de modelo las conversaciones guardadas se conservan.
CHECKPOINTER = SqliteSaver(sqlite3.connect(DIR / "memoria.db", check_same_thread=False))
_modelo = (proveedor_por_defecto(), modelo_por_defecto(proveedor_por_defecto()))   # (proveedor, nombre) en uso
agent = build_agent(get_model(*_modelo), CHECKPOINTER)


def modelo_actual() -> tuple[str, str]:
    """(proveedor, nombre) del modelo con el que está armado el agente."""
    return _modelo


def usar_modelo(proveedor: str, nombre: str) -> None:
    """Cambia el modelo del agente. Si falla (p. ej. falta la clave) deja el anterior y lanza RuntimeError."""
    global agent, _modelo
    if (proveedor, nombre) == _modelo:
        return
    agent = build_agent(get_model(proveedor, nombre), CHECKPOINTER)   # si falla, no se toca nada
    _modelo = (proveedor, nombre)


# --- Conversaciones (patrón de la cátedra) -----------------------------------

def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def _thread_ids() -> list[str]:
    return CONV_FILE.read_text().split() if CONV_FILE.exists() else []


def select_thread(thread_id: str) -> RunnableConfig:
    """Marca una conversación como la activa: pasa a ser la última del archivo."""
    ids = [i for i in _thread_ids() if i != thread_id]
    CONV_FILE.write_text("\n".join(ids + [thread_id]))
    return _config(thread_id)


def new_thread() -> RunnableConfig:
    return select_thread(str(uuid4()))


def resume_thread() -> RunnableConfig:
    """Retoma la última conversación usada; si no hay ninguna, crea una nueva."""
    ids = _thread_ids()
    return select_thread(ids[-1]) if ids else new_thread()


def saved_state(config: RunnableConfig) -> dict:
    """Estado guardado de la conversación (vacío si es nueva)."""
    return agent.get_state(config).values


def list_threads(limit: int = 5) -> list[tuple[str, str]]:
    """Últimas conversaciones con mensajes, la más reciente primero: (thread_id, título)."""
    conversaciones = []
    for thread_id in reversed(_thread_ids()):
        mensajes = saved_state(_config(thread_id)).get("messages", [])
        if mensajes:
            conversaciones.append((thread_id, mensajes[0].text[:40]))
        if len(conversaciones) == limit:
            break
    return conversaciones


# --- Eventos: lo único que las interfaces necesitan conocer --------------------

def _razonamiento(m) -> str:
    """Razonamiento de un mensaje del agente (bloques estándar `reasoning`; Gemini los trae)."""
    bloques = [b["reasoning"] for b in m.content_blocks if b["type"] == "reasoning" and b.get("reasoning")]
    return "\n\n".join(bloques) or m.additional_kwargs.get("reasoning_content", "")


def eventos(mensajes: list) -> Iterator[dict]:
    """Traduce mensajes del grafo a eventos para mostrar."""
    for m in mensajes:
        if m.type == "ai":
            if razonamiento := _razonamiento(m):
                yield {"tipo": "razonamiento", "texto": razonamiento}
            if m.text:
                yield {"tipo": "texto", "texto": m.text}
            for call in m.tool_calls:
                yield {"tipo": "llamada", "id": call["id"], "nombre": call["name"], "args": call["args"]}
        elif m.type == "tool":
            yield {"tipo": "resultado", "id": m.tool_call_id, "texto": m.text, "estado": m.status}


def pending_approval(config: RunnableConfig) -> list[dict] | None:
    """Acciones esperando confirmación del socio (None si no hay ninguna pausa)."""
    for tarea in agent.get_state(config).tasks:
        for intr in tarea.interrupts:
            return intr.value["action_requests"]
    return None


def stream_turn(entrada: str | Command, config: RunnableConfig) -> Iterator[dict]:
    """Ejecuta un turno (entrada = texto del socio) o retoma uno pausado (entrada = Command).

    Emite eventos a medida que ocurren. Si el agente necesita confirmación, emite un evento
    "aprobacion" y termina: la interfaz decide y vuelve a llamar con `decision()`.
    """
    vistos = len(saved_state(config).get("messages", []))
    if isinstance(entrada, str):
        entrada = {"messages": [{"role": "user", "content": entrada}]}
    # stream_mode="values": tras cada paso llega el estado completo; solo se emiten los
    # mensajes que no se habían visto todavía.
    # durability="sync": cada paso queda guardado ANTES de seguir; así, si la interfaz interrumpe el
    # turno entre dos pasos, el estado guardado es exactamente el de ese momento (ver discard_turn).
    for estado in agent.stream(entrada, config, stream_mode="values", durability="sync"):
        yield from eventos(estado["messages"][vistos:])
        vistos = len(estado["messages"])
        if "__interrupt__" in estado:
            acciones = estado["__interrupt__"][0].value["action_requests"]
            yield {"tipo": "aprobacion", "acciones": acciones}


def message_count(config: RunnableConfig) -> int:
    """Cantidad de mensajes guardados de la conversación (marca de dónde empieza un turno)."""
    return len(saved_state(config).get("messages", []))


class TurnoEnCurso:
    """Un turno del agente que se está ejecutando y que el socio puede cancelar.

    Al tocar «Detener», Streamlit arranca un script nuevo pero el viejo sigue vivo hasta su próxima
    llamada a Streamlit: antes termina el paso del agente que estaba haciendo (p. ej. la llamada al
    modelo) y lo guarda. Si el script nuevo descartara el turno enseguida, ese guardado tardío lo
    haría reaparecer; y si esperara, la pantalla quedaría bloqueada hasta que el modelo responda.

    Por eso el descarte lo hace **quien llegue segundo**, una sola vez (con un lock):
    - `cancelar()` (script nuevo): si el viejo ya terminó, descarta él; si no, solo deja marcado.
    - `terminar()` (script viejo, al terminar o al ser interrumpido): si hay marca, descarta él.
    Así el script nuevo responde al instante y el descarte siempre ocurre DESPUÉS del último guardado.
    """

    def __init__(self, config: RunnableConfig):
        self.config = config
        self.mensajes_antes = message_count(config)   # dónde empieza este turno en el historial
        self.hecho = threading.Event()                # se activa cuando el turno ya terminó y, si se canceló, se descartó
        self._lock = threading.Lock()
        self._terminado = False
        self._cancelado = False

    @property
    def thread_id(self) -> str:
        return self.config["configurable"]["thread_id"]

    def cancelar(self) -> None:
        with self._lock:
            if not self._terminado:
                self._cancelado = True     # el script viejo lo descartará en terminar()
                return
        discard_turn(self.config, self.mensajes_antes)
        self.hecho.set()

    def terminar(self) -> None:
        with self._lock:
            self._terminado = True
            descartar = self._cancelado
        if descartar:
            discard_turn(self.config, self.mensajes_antes)
        self.hecho.set()


def discard_turn(config: RunnableConfig, mensajes_antes: int) -> None:
    """Descarta un turno interrumpido: borra del historial los mensajes agregados desde `mensajes_antes`.

    Si el socio cancela mientras el agente trabaja, el turno puede quedar a medias (por ejemplo, una
    llamada a una tool sin su resultado, que los proveedores rechazan). Se borra entero, como si el
    mensaje nunca se hubiera enviado, y el socio puede editarlo y reenviarlo.
    """
    sobrantes = saved_state(config).get("messages", [])[mensajes_antes:]
    if sobrantes:
        agent.update_state(config, {"messages": [RemoveMessage(id=m.id) for m in sobrantes]})


def decision(aprobar: bool, motivo: str = "") -> Command:
    """Respuesta del socio al pedido de confirmación, lista para pasar a stream_turn."""
    if aprobar:
        return Command(resume={"decisions": [{"type": "approve"}]})
    mensaje = motivo or "El socio rechazó la operación. No la ejecutes; preguntale qué quiere cambiar."
    return Command(resume={"decisions": [{"type": "reject", "message": mensaje}]})


def saved_turns(config: RunnableConfig, hasta: int | None = None) -> list[tuple[str, list[dict]]]:
    """La conversación guardada como turnos: (pregunta, eventos del agente).

    `hasta` limita a los primeros N mensajes (se usa mientras un turno cancelado todavía se está descartando).
    """
    turnos: list[tuple[str, list[dict]]] = []
    for m in saved_state(config).get("messages", [])[:hasta]:
        if m.type == "human":
            turnos.append((m.text, []))
        else:
            turnos[-1][1].extend(eventos([m]))
    return turnos


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


def es_limite_de_cuota(error: Exception) -> bool:
    """¿El error es un 429 / cuota agotada del proveedor?"""
    return clasificar_error(error) == "cuota"


def _espera_sugerida(error: Exception) -> str | None:
    """'12 h 39 min' si el proveedor dice cuándo se libera el cupo (Gemini: «Please retry in 12h39m48s»)."""
    m = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", str(error), re.IGNORECASE)
    if not m or not any(m.groups()):
        return None
    horas, minutos, segundos = int(m[1] or 0), int(m[2] or 0), float(m[3] or 0)
    if horas:
        return f"{horas} h {minutos} min" if minutos else f"{horas} h"
    if minutos:
        return f"{minutos} min"
    return f"{max(1, math.ceil(segundos))} s"


def explicar_error(error: Exception) -> tuple[str, str, str]:
    """(tipo, título, detalle) en español para mostrarle al socio por qué falló el modelo y qué puede hacer."""
    tipo = clasificar_error(error)
    proveedor, nombre = modelo_actual()
    quien = f"{PROVEEDORES.get(proveedor, proveedor)} · {nombre}"
    if tipo == "cuota":
        texto = str(error).lower()
        causa = ("Es el límite DIARIO del plan gratuito. " if ("per day" in texto or "perday" in texto)
                 else "El plan gratuito permite pocas llamadas por minuto. ")
        espera = _espera_sugerida(error)
        cuando = f"Se libera en aproximadamente {espera}. " if espera else "Esperá un momento y reintentá. "
        return (tipo, "Se agotó el uso del modelo",
                f"{quien} rechazó el pedido por límite de uso. {causa}{cuando}"
                "Mientras tanto podés elegir otro proveedor o modelo en la barra lateral (sección Modelo).")
    if tipo == "credito":
        return (tipo, "Sin crédito en el proveedor",
                f"{quien} pide saldo para seguir. Cargá crédito en su cuenta o elegí un modelo gratuito en la barra lateral.")
    if tipo == "saturado":
        return (tipo, "El modelo está saturado",
                f"{quien} no pudo atender el pedido ahora. Reintentá en unos segundos o elegí otro modelo en la barra lateral.")
    if tipo == "clave":
        return (tipo, "Problema con la clave del proveedor",
                f"{quien} rechazó las credenciales. Revisá la clave en el .env (GOOGLE_API_KEY / OPENROUTER_API_KEY) "
                "y reiniciá la aplicación.")
    return (tipo, "El agente falló", f"{type(error).__name__}: {str(error)[:300]}")


# --- Consola -----------------------------------------------------------------

def print_evento(e: dict) -> None:
    tipo = e["tipo"]
    if tipo == "razonamiento":
        print(f"[razonamiento] {e['texto']}\n")
    elif tipo == "llamada":
        print(f"[tool] {e['nombre']}({e['args']})")
    elif tipo == "resultado":
        print(f"[resultado] {e['texto'][:300]}\n")
    elif tipo == "aprobacion":
        for a in e["acciones"]:
            print(f"[CONFIRMAR] {a['name']}({a['args']})")
    elif tipo == "texto":
        print(f"Agente: {e['texto']}\n")


def _correr(entrada, config) -> None:
    """Corre un turno por consola y, mientras haya confirmaciones pendientes, las pregunta."""
    while True:
        try:
            for e in stream_turn(entrada, config):
                print_evento(e)
        except Exception as err:
            _, titulo, detalle = explicar_error(err)
            print(f"\n[ERROR] {titulo}. {detalle}\n")
            return
        if pending_approval(config) is None:
            return
        entrada = decision(input("¿Aprobás? (s/n): ").strip().lower() in {"s", "si", "sí", "y"})


def main() -> None:
    print("Agente ArgenEquip listo. Escribí 'salir' para terminar.\n")
    config = resume_thread()
    for pregunta, evs in saved_turns(config):
        print(f"Vos: {pregunta}")
        for e in evs:
            print_evento(e)
    if pending_approval(config):
        _correr(decision(input("Hay una confirmación pendiente. ¿Aprobás? (s/n): ").strip().lower() in {"s", "si", "sí"}), config)
    while True:
        user_input = input("Vos: ").strip()
        if user_input.lower() in {"salir", "exit", "quit"}:
            break
        if user_input:
            _correr(user_input, config)


if __name__ == "__main__":
    main()
