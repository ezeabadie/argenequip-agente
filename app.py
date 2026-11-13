"""UI de Streamlit para el agente ArgenEquip: muestra en vivo el recorrido del agente.

La lógica vive en agente.py; acá solo se dibujan los eventos que emite (razonamiento,
llamada a tool, resultado, texto) y la confirmación humana (Aprobar / Rechazar).

Ejecutar con:  streamlit run app.py
"""
from html import escape
from pathlib import Path

import streamlit as st

try:
    from agente import (
        TurnoEnCurso, decision, discard_turn, explicar_error, list_threads, modelo_actual, new_thread,
        pending_approval, resume_thread, saved_turns, select_thread, stream_turn, usar_modelo,
    )
    from argenequip_client import get_client
    from model import PROVEEDORES, modelo_por_defecto
    from tools import fecha_ddmmaaaa
except RuntimeError as e:  # por ejemplo, falta GOOGLE_API_KEY en el .env
    st.error(str(e))
    st.stop()

PASOS_EXPANDIDOS = True   # False: razonamiento y tools quedan plegados
ICONOS = {"load_skill": "📖", "buscar_cliente": "🧑‍💼", "buscar_equipos": "🔎",
          "consultar_disponibilidad": "📅", "resumen_alquileres": "📋", "crear_alquiler": "📝"}

AVATARES = {"user": "👤", "assistant": "🛠️"}

st.set_page_config(page_title="ArgenEquip - Asistente", page_icon="🛠️", initial_sidebar_state="expanded")
# El tema (colores) está en .streamlit/config.toml; los detalles de estilo, en estilos.css.
st.html(f"<style>{(Path(__file__).parent / 'estilos.css').read_text(encoding='utf-8')}</style>")


def mensaje(rol: str):
    """Burbuja de chat con el avatar de cada rol ("user" / "assistant")."""
    return st.chat_message(rol, avatar=AVATARES[rol])


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


ICONOS_ERROR = {"cuota": "⏳", "credito": "💳", "saturado": "🌐", "clave": "🔑", "otro": "⚠️"}


def render_error() -> None:
    """Aviso de la última falla del modelo (cuota agotada, saturación, clave inválida...).

    Se guarda en session_state porque `correr` termina con st.rerun(), que redibuja toda la pantalla y
    borraría un aviso mostrado en ese momento. Queda a la vista hasta que se envía otro mensaje.
    """
    e = st.session_state.get("error")
    if not e or e["hilo"] != st.session_state.config["configurable"]["thread_id"]:
        return
    nivel = st.warning if e["tipo"] in ("cuota", "credito", "saturado") else st.error
    texto = f"**{e['titulo']}**\n\n{e['detalle']}"
    if e["restaurado"]:
        texto += "\n\nTu mensaje quedó en la caja de chat para que lo reenvíes."
    nivel(texto, icon=ICONOS_ERROR[e["tipo"]])


def correr(entrada) -> None:
    """Ejecuta un turno (o la reanudación tras la confirmación) mostrándolo en vivo.

    Cancelación: mientras corre un mensaje nuevo se muestra «Detener». Al tocarlo, Streamlit arranca un
    script nuevo (ver el comienzo del script) mientras este sigue vivo hasta su próxima llamada a Streamlit.
    Este `finally` corre cuando eso pasa, y ahí `TurnoEnCurso.terminar()` descarta el turno si se canceló.
    La reanudación tras Aprobar no se puede cancelar: la operación ya fue autorizada.
    """
    es_mensaje = isinstance(entrada, str)
    config = st.session_state.config
    # Si el turno anterior se canceló y todavía se está terminando, se lo espera: dos turnos a la vez
    # sobre el mismo hilo de conversación se pisarían.
    anterior = st.session_state.pop("cancelado", None)
    if anterior is not None and anterior.thread_id == config["configurable"]["thread_id"]:
        with st.spinner("Terminando de cancelar la respuesta anterior…"):
            anterior.hecho.wait(timeout=120)
    st.session_state.pop("error", None)   # enviar algo nuevo borra el aviso de la falla anterior
    turno = TurnoEnCurso(config) if es_mensaje else None
    if turno:
        st.session_state.en_curso = (entrada, turno)
    try:
        with mensaje("assistant"):
            if es_mensaje:
                st.button("⏹ Detener", key="detener", help="Cancela esta respuesta y te deja editar tu mensaje")
            try:
                with st.spinner("El agente está trabajando..."):
                    render_eventos(stream_turn(entrada, config))
            except Exception as e:
                # El modelo falló (cuota agotada, saturación...). Se guarda el aviso para mostrarlo después del
                # st.rerun() y, si era un mensaje nuevo, se descarta el turno sin respuesta y el texto vuelve a
                # la caja para reenviarlo cuando se pueda.
                tipo, titulo, detalle = explicar_error(e)
                if turno:
                    discard_turn(config, turno.mensajes_antes)
                    st.session_state.restaurar = entrada
                st.session_state.error = {"tipo": tipo, "titulo": titulo, "detalle": detalle,
                                          "restaurado": turno is not None, "hilo": config["configurable"]["thread_id"]}
    finally:
        if turno:
            turno.terminar()   # corre también si Streamlit interrumpe este script
    # Termino normal (o con error ya guardado). Si Streamlit interrumpió el turno, no se llega acá
    # (corta con una excepción que no es `Exception`) y `en_curso` queda marcado.
    st.session_state.en_curso = None
    st.rerun()   # redibuja todo desde el estado guardado (incluye la confirmación pendiente)


# --- Confirmación humana ------------------------------------------------------------

def render_aprobacion(acciones: list[dict]) -> None:
    """Resumen de la escritura pendiente con botones Aprobar / Rechazar."""
    with mensaje("assistant"):
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
                    f"- **Fechas:** {fecha_ddmmaaaa(args.get('fecha_inicio'))} al {fecha_ddmmaaaa(args.get('fecha_fin'))}\n"
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
    seccion("Modelo")
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


def seccion(titulo: str) -> None:
    """Rótulo pequeño en mayúsculas para separar bloques de la barra lateral."""
    st.html(f'<div class="seccion">{escape(titulo)}</div>')


def render_sidebar() -> None:
    actual = st.session_state.config["configurable"]["thread_id"]
    with st.sidebar:
        st.html('<div class="marca-lateral">🛠️ Argen<span>Equip</span></div>'
                '<div class="sub-lateral">Asistente de alquileres</div>')
        seccion("Comandos")
        if st.button("📝 nuevo", use_container_width=True, help="Crea un alquiler paso a paso"):
            st.session_state.entrada = "nuevo"
            st.rerun()
        if st.button("📋 resumen", use_container_width=True, help="Alquileres activos con equipos, valor y estado de pago"):
            st.session_state.entrada = "resumen"
            st.rerun()
        st.caption("**cancelar**: usá «Rechazar» en la confirmación, o escribilo en el chat.")
        st.caption(f"Modo del sistema: `{type(get_client()).__name__}`")

        render_modelo()

        seccion("Conversaciones")
        if st.button("➕ Nueva conversación", disabled=not saved_turns(st.session_state.config), use_container_width=True):
            st.session_state.config = new_thread()
            st.rerun()
        for thread_id, titulo in list_threads():
            if st.button(titulo, key=thread_id, disabled=thread_id == actual, use_container_width=True):
                st.session_state.config = select_thread(thread_id)
                st.rerun()


def render_cabecera() -> None:
    """Marca arriba del chat (queda a la vista aunque se oculte la barra lateral) y qué modelo/sistema se usa."""
    proveedor, nombre = modelo_actual()
    es_api = type(get_client()).__name__ == "HttpClient"
    modo = ('<span class="chip ok">● Sistema real (API)</span>' if es_api
            else '<span class="chip demo">● Datos de demo</span>')
    st.html('<div class="cabecera">'
            '<div class="marca">🛠️ ArgenEquip<small>Asistente de alquileres</small></div>'
            f'<div class="chips">{modo}<span class="chip">{escape(PROVEEDORES.get(proveedor, proveedor))} · {escape(nombre)}</span></div>'
            '</div>')


def render_bienvenida() -> None:
    """Pantalla inicial (conversación vacía): qué se puede hacer, con atajos."""
    st.html('<div class="hero"><h1>¿Qué querés hacer hoy?</h1>'
            '<p>Creá alquileres y consultá el estado de tu negocio escribiendo en lenguaje natural, '
            'o empezá con uno de estos atajos.</p></div>')
    c1, c2 = st.columns(2)
    if c1.button("**📝 Nuevo alquiler**  \nArmalo paso a paso: cliente, equipos, fechas y confirmación.",
                 key="hero_nuevo", use_container_width=True):
        st.session_state.entrada = "nuevo"
        st.rerun()
    if c2.button("**📋 Resumen**  \nAlquileres activos con equipos, valor y estado de pago.",
                 key="hero_resumen", use_container_width=True):
        st.session_state.entrada = "resumen"
        st.rerun()
    st.html('<div class="ejemplo">También podés escribir, por ejemplo: '
            '<q>Quiero alquilarle a Pérez una hormigonera del 24 al 28 por 120000</q> o '
            '<q>Listame todos los alquileres de Juan Pérez</q></div>')


if "config" not in st.session_state:
    st.session_state.config = resume_thread()
config = st.session_state.config

# ¿Quedó un turno a medias? Pasa si el socio tocó «Detener» (o cualquier otro control) mientras el agente
# trabajaba: Streamlit cortó el script anterior antes de que borrara `en_curso`. El turno se descarta del
# historial y el mensaje vuelve a la caja de chat para que se pueda editar y reenviar.
# NO se espera a que el script anterior termine (puede tardar lo que tarde el modelo en responder): la
# pantalla responde al instante y el descarte lo completa `TurnoEnCurso` apenas el otro script termina.
interrumpido = st.session_state.pop("en_curso", None)
if interrumpido:
    texto, turno = interrumpido
    turno.cancelar()
    st.session_state.cancelado = turno
    st.session_state["chat"] = texto   # precarga la caja (la key es la de st.chat_input de más abajo)
    st.toast("Respuesta cancelada. Editá tu mensaje y reenviálo cuando quieras.", icon="⏹️")

# Si el modelo falló en el turno anterior, su mensaje vuelve a la caja (ver `correr`). Se hace acá, antes de
# dibujar la caja: Streamlit no deja cambiar el valor de un widget después de dibujarlo.
restaurar = st.session_state.pop("restaurar", None)
if restaurar:
    st.session_state["chat"] = restaurar

# Mientras ese turno cancelado se termina de descartar, el historial se muestra solo hasta antes de él.
cancelado = st.session_state.get("cancelado")
if cancelado is not None and cancelado.hecho.is_set():
    st.session_state.pop("cancelado")
    cancelado = None
en_descarte = cancelado is not None and cancelado.thread_id == config["configurable"]["thread_id"]

acciones = None if en_descarte else pending_approval(config)

# Entrada pendiente: un botón de la barra lateral o la decisión Aprobar/Rechazar (más abajo, un mensaje escrito).
entrada = st.session_state.pop("entrada", None)

# La caja de chat es LO PRIMERO que se dibuja (aunque en pantalla va abajo). `submit_mode="disable"` hace que
# el navegador la bloquee en el instante en que se envía un mensaje y la libere cuando el turno termina; si
# el turno lo disparó un atajo o Aprobar/Rechazar, la bloquea `disabled`.
prompt = st.chat_input("Escribí un comando o tu consulta...", key="chat", submit_mode="disable",
                       disabled=bool(acciones) or entrada is not None)
if isinstance(prompt, str):
    entrada = prompt

render_sidebar()
render_cabecera()
if entrada is None:
    render_error()   # si está por correr un turno, el aviso viejo ya no corresponde

# Streamlit re-ejecuta todo el script en cada interacción: se redibuja la conversación guardada.
turnos = saved_turns(config, hasta=cancelado.mensajes_antes if en_descarte else None)
if not turnos and entrada is None and not acciones:
    render_bienvenida()
for pregunta, eventos in turnos:
    mensaje("user").markdown(pregunta)
    with mensaje("assistant"):
        render_eventos(eventos)

if isinstance(entrada, str):
    mensaje("user").markdown(entrada)

if entrada is not None:
    correr(entrada)
elif acciones:
    render_aprobacion(acciones)
