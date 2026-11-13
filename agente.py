"""Agente ArgenEquip: create_agent + memoria + confirmación humana, con streaming de eventos.

`stream_turn` ejecuta un turno (o retoma uno pausado) y emite *eventos* (razonamiento,
llamada a tool, resultado, texto, pedido de aprobación) a medida que el agente avanza.
`saved_turns` reconstruye los mismos eventos desde la conversación guardada. Cada interfaz
(consola, Streamlit) solo los presenta.

Consola:    python agente.py
Streamlit:  streamlit run app.py
"""
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from model import get_model
from tools import TOOLS, WRITE_TOOLS, listar_skills

# La fecha se fija al iniciar el programa (suficiente para una sesión de demo).
HOY = date.today()

SYSTEM_PROMPT = f"""Sos el asistente de ArgenEquip, un sistema de alquiler de herramientas y equipos para obra. \
Ayudás a un socio a operar el sistema. Respondé siempre en español rioplatense, de forma breve y clara.
La fecha de hoy es {HOY.isoformat()} (AAAA-MM-DD). Usala para interpretar fechas como «el lunes» o «del 24 al 28».

COMANDOS (el socio puede escribirlos solos o como frase libre): nuevo, resumen, cancelar.
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
- Solo están disponibles los comandos nuevo y resumen. Si piden otra cosa, decí que todavía no se puede hacer desde acá.
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


agent = build_agent(get_model(), SqliteSaver(sqlite3.connect(DIR / "memoria.db", check_same_thread=False)))


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
    for estado in agent.stream(entrada, config, stream_mode="values"):
        yield from eventos(estado["messages"][vistos:])
        vistos = len(estado["messages"])
        if "__interrupt__" in estado:
            acciones = estado["__interrupt__"][0].value["action_requests"]
            yield {"tipo": "aprobacion", "acciones": acciones}


def decision(aprobar: bool, motivo: str = "") -> Command:
    """Respuesta del socio al pedido de confirmación, lista para pasar a stream_turn."""
    if aprobar:
        return Command(resume={"decisions": [{"type": "approve"}]})
    mensaje = motivo or "El socio rechazó la operación. No la ejecutes; preguntale qué quiere cambiar."
    return Command(resume={"decisions": [{"type": "reject", "message": mensaje}]})


def saved_turns(config: RunnableConfig) -> list[tuple[str, list[dict]]]:
    """La conversación guardada como turnos: (pregunta, eventos del agente)."""
    turnos: list[tuple[str, list[dict]]] = []
    for m in saved_state(config).get("messages", []):
        if m.type == "human":
            turnos.append((m.text, []))
        else:
            turnos[-1][1].extend(eventos([m]))
    return turnos


def es_limite_de_cuota(error: Exception) -> bool:
    """¿El error es un 429 / cuota agotada del proveedor? (la UI muestra un aviso específico)."""
    texto = f"{type(error).__name__} {error}".lower()
    return any(s in texto for s in ("429", "resource_exhausted", "rate limit", "quota"))


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
            print("Aviso: límite de cuota del modelo (429), esperá un momento." if es_limite_de_cuota(err)
                  else f"El agente falló: {err}")
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
