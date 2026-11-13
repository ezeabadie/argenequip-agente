"""UI de Streamlit para el agente ArgenEquip: muestra en vivo el recorrido del agente.

La lógica vive en agente.py; acá solo se dibujan los eventos que emite (razonamiento,
llamada a tool, resultado, texto) y la confirmación humana (Aprobar / Rechazar).

Ejecutar con:  streamlit run app.py
"""
import streamlit as st

try:
    from agente import (
        decision, es_limite_de_cuota, list_threads, modelo_actual, new_thread, pending_approval,
        resume_thread, saved_turns, select_thread, stream_turn, usar_modelo,
    )
    from argenequip_client import get_client
    from model import PROVEEDORES, modelo_por_defecto
except RuntimeError as e:  # por ejemplo, falta GOOGLE_API_KEY en el .env
    st.error(str(e))
    st.stop()

PASOS_EXPANDIDOS = True   # False: razonamiento y tools quedan plegados
ICONOS = {"load_skill": "📖", "buscar_cliente": "🧑‍💼", "buscar_equipos": "🔎",
          "consultar_disponibilidad": "📅", "resumen_alquileres": "📋", "crear_alquiler": "📝"}

st.set_page_config(page_title="ArgenEquip - Asistente", page_icon="🛠️")


# --- Dibujo de eventos ------------------------------------------------------------

def titulo_llamada(e: dict) -> str:
    nombre, args = e["nombre"], e["args"]
    icono = ICONOS.get(nombre, "🔧")
    if nombre == "load_skill":
        return f"{icono} Skill «{args.get('nombre', '')}»"
    return f"{icono} {nombre}"


def render_eventos(eventos) -> None:
    """Dibuja los eventos a medida que llegan; sirve en vivo (generador) y para el historial (lista)."""
    llamadas = {}   # id del tool call -> contenedor st.status (queda "running" hasta el resultado)
    for e in eventos:
        tipo = e["tipo"]
        if tipo == "razonamiento":
            with st.expander("💭 Razonamiento", expanded=False):
                # Un salto de línea por renglón y sin sangrías (que markdown tomaría por código).
                st.markdown("  \n".join(l.strip() for l in e["texto"].splitlines()))
        elif tipo == "texto":
            st.markdown(e["texto"])   # st.markdown renderiza el markdown del modelo
        elif tipo == "llamada":
            status = st.status(titulo_llamada(e), expanded=PASOS_EXPANDIDOS)
            with status:
                st.caption("Argumentos")
                st.json(e["args"], expanded=False)
            llamadas[e["id"]] = status
        elif tipo == "resultado":
            status = llamadas.get(e["id"])
            if status is None:   # el resultado llegó sin su llamada (no debería pasar)
                continue
            fallo = e["estado"] == "error" or e["texto"].startswith("ERROR")
            with status:
                st.caption("Resultado")
                st.code(e["texto"][:1500], language=None)
            status.update(state="error" if fallo else "complete", expanded=PASOS_EXPANDIDOS and fallo)
        # "aprobacion" no se dibuja acá: lo hace render_aprobacion() desde el estado guardado.


def mensaje_de_error(error: Exception) -> str:
    """Texto claro para el socio según el tipo de falla del proveedor del modelo."""
    texto = f"{type(error).__name__} {error}"
    if es_limite_de_cuota(error):
        return ("⏳ **Límite de uso del modelo alcanzado (429).** Los planes gratuitos permiten pocas "
                "llamadas por minuto: esperá unos segundos y volvé a enviar el mensaje, o probá con otro proveedor.")
    if "503" in texto or "UNAVAILABLE" in texto.upper():
        return "🌐 **El modelo está saturado en este momento (503).** Reintentá en unos segundos."
    return f"El agente falló: {error}"


def correr(entrada) -> None:
    """Ejecuta un turno (o la reanudación tras la confirmación) mostrándolo en vivo."""
    with st.chat_message("assistant"):
        try:
            with st.spinner("El agente está trabajando..."):
                render_eventos(stream_turn(entrada, st.session_state.config))
        except Exception as e:
            st.warning(mensaje_de_error(e)) if es_limite_de_cuota(e) else st.error(mensaje_de_error(e))
    st.rerun()   # redibuja todo desde el estado guardado (incluye la confirmación pendiente)


# --- Confirmación humana ------------------------------------------------------------

def render_aprobacion(acciones: list[dict]) -> None:
    """Resumen de la escritura pendiente con botones Aprobar / Rechazar."""
    with st.chat_message("assistant"):
        for a in acciones:
            args = a["args"]
            st.warning("**Confirmación requerida** · el asistente quiere ejecutar una operación que modifica el sistema.")
            if a["name"] == "crear_alquiler":
                nombres = {}
                try:   # nombres de equipos para mostrar algo más legible que un id
                    nombres = {h["idHerramienta"]: h["nombre"] for h in get_client().buscar_herramientas("")}
                except Exception:
                    pass
                equipos = "\n".join(f"- {e['cantidad']} x {nombres.get(e['herramienta_id'], 'equipo')} (id {e['herramienta_id']})"
                                    for e in args.get("equipos", []))
                monto = f"${args['monto_total']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                st.markdown(
                    f"### Nuevo alquiler\n"
                    f"- **Título:** {args.get('titulo')}\n"
                    f"- **Cliente (id):** {args.get('cliente_id')} · **Domicilio (id):** {args.get('domicilio_id')}\n"
                    f"- **Fechas:** {args.get('fecha_inicio')} al {args.get('fecha_fin')}\n"
                    f"- **Valor total:** {monto}\n"
                    f"- **Equipos:**\n{equipos}"
                )
            else:
                st.json({"operación": a["name"], **args})
        c1, c2 = st.columns(2)
        if c1.button("✅ Aprobar", type="primary", use_container_width=True):
            st.session_state.entrada = decision(True)
            st.rerun()
        if c2.button("❌ Rechazar (cancelar)", use_container_width=True):
            st.session_state.entrada = decision(False)
            st.rerun()


# --- Sesión y barra lateral ------------------------------------------------------------

def render_modelo() -> None:
    """Selector de proveedor y modelo: reconstruye el agente (la memoria de las conversaciones se conserva)."""
    st.header("Modelo")
    ids = list(PROVEEDORES)
    en_uso = modelo_actual()
    proveedor = st.selectbox("Proveedor", ids, format_func=PROVEEDORES.get, key="proveedor",
                             index=ids.index(en_uso[0]) if en_uso[0] in ids else 0)
    # Un campo de texto por proveedor, así cada uno recuerda su propio modelo.
    nombre = st.text_input("Modelo", key=f"modelo_{proveedor}",
                           value=en_uso[1] if proveedor == en_uso[0] else modelo_por_defecto(proveedor),
                           help="Identificador del modelo. En OpenRouter, el slug (los gratuitos terminan en :free); "
                                "tiene que soportar tool calling.").strip() or modelo_por_defecto(proveedor)
    try:
        usar_modelo(proveedor, nombre)
    except Exception as e:   # falta la clave, paquete sin instalar, nombre inválido...
        st.error(f"No se pudo usar ese modelo: {e}")
    st.caption(f"En uso: `{modelo_actual()[1]}`")


def render_sidebar() -> None:
    actual = st.session_state.config["configurable"]["thread_id"]
    with st.sidebar:
        st.header("Comandos")
        if st.button("📝 nuevo", use_container_width=True, help="Crea un alquiler paso a paso"):
            st.session_state.entrada = "nuevo"
            st.rerun()
        if st.button("📋 resumen", use_container_width=True, help="Alquileres activos con equipos, valor y estado de pago"):
            st.session_state.entrada = "resumen"
            st.rerun()
        st.caption("**cancelar**: usá «Rechazar» en la confirmación, o escribilo en el chat.")
        st.caption(f"Modo del sistema: `{type(get_client()).__name__}`")

        render_modelo()

        st.header("Conversaciones")
        if st.button("➕ Nueva conversación", disabled=not saved_turns(st.session_state.config)):
            st.session_state.config = new_thread()
            st.rerun()
        for thread_id, titulo in list_threads():
            if st.button(titulo, key=thread_id, disabled=thread_id == actual, use_container_width=True):
                st.session_state.config = select_thread(thread_id)
                st.rerun()


if "config" not in st.session_state:
    st.session_state.config = resume_thread()
config = st.session_state.config

st.title("🛠️ ArgenEquip · Asistente")
render_sidebar()

# Streamlit re-ejecuta todo el script en cada interacción: se redibuja la conversación guardada.
for pregunta, eventos in saved_turns(config):
    st.chat_message("user").markdown(pregunta)
    with st.chat_message("assistant"):
        render_eventos(eventos)

acciones = pending_approval(config)

# Entrada pendiente: un botón de la barra lateral, o la decisión Aprobar/Rechazar.
entrada = st.session_state.pop("entrada", None)
if isinstance(entrada, str):
    st.chat_message("user").markdown(entrada)

if entrada is not None:
    correr(entrada)
elif acciones:
    render_aprobacion(acciones)

if prompt := st.chat_input("Escribí un comando o tu consulta...", disabled=bool(acciones)):
    st.chat_message("user").markdown(prompt)
    correr(prompt)
