"""UI de Streamlit para el agente ArgenEquip: muestra en vivo el recorrido del agente.

La lógica vive en agente.py; acá solo se dibujan los eventos que emite (razonamiento,
llamada a tool, resultado, texto) y la confirmación humana (Aprobar / Rechazar).

Ejecutar con:  streamlit run app.py
"""
from datetime import date, timedelta
from html import escape
from pathlib import Path

import streamlit as st

try:
    from agente import (
        TurnoEnCurso, claves_en_uso, decision, delete_thread, describir_paso, discard_turn, explicar_error,
        list_threads, modelo_actual, new_thread, pending_approval, repair_dangling_call, resume_thread, saved_turns,
        select_thread, stream_turn, usar_modelo,
    )
    from argenequip_client import get_client
    from model import PROVEEDORES, modelo_por_defecto
    from tools import BASE_DIR, CONTRATOS_DIR, cliente_y_domicilio, fecha_ddmmaaaa
except RuntimeError as e:  # por ejemplo, falta GOOGLE_API_KEY en el .env
    st.error(str(e))
    st.stop()

ICONOS = {"load_skill": "📖", "buscar_cliente": "🧑‍💼", "buscar_equipos": "🔎",
          "consultar_disponibilidad": "📅", "resumen_alquileres": "📋", "crear_alquiler": "📝",
          "alquileres_de_cliente": "🗂️", "generar_contrato_pdf": "📄"}

# Logo de ArgenEquip (images/logoArgenEquip.jpg es el original; logo.png e icono.png son recortes para la UI).
# Si faltan los archivos, la interfaz sigue andando con un emoji.
LOGO = BASE_DIR / "images" / "logo.png"       # marca + nombre
ICONO = BASE_DIR / "images" / "icono.png"     # solo la marca (pestaña, avatar del asistente, barra lateral cerrada)
AVATARES = {"user": "👤", "assistant": str(ICONO) if ICONO.exists() else "🛠️"}

st.set_page_config(page_title="ArgenEquip - Asistente", page_icon=str(ICONO) if ICONO.exists() else "🛠️",
                   initial_sidebar_state="expanded")
# Los temas (claro y oscuro) están en .streamlit/config.toml; los detalles de estilo, en estilos.css.
# Si Streamlit está en modo claro se agrega estilos-claro.css, que solo redefine las variables de color.
# Sin dato del tema (primera carga) se asume el oscuro, que es el de siempre.
_estilos = (BASE_DIR / "estilos.css").read_text(encoding="utf-8")
if st.context.theme.type == "light":
    _estilos += (BASE_DIR / "estilos-claro.css").read_text(encoding="utf-8")
st.html(f"<style>{_estilos}</style>")
if ICONO.exists():
    st.logo(str(ICONO), size="large")   # esquina superior izquierda cuando la barra lateral está cerrada


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


def render_descarga(e: dict, abierto: bool = True) -> None:
    """Vista previa y botón para bajar el PDF que generó la tool (la primera línea del resultado dice dónde quedó).

    `abierto`: la vista previa se muestra desplegada solo para el contrato más reciente; los anteriores quedan
    plegados (cada redibujado vuelve a cargar el visor de PDF, y tener varios abiertos es pesado).
    """
    nombre = e["texto"].splitlines()[0].removeprefix("PDF generado:").strip()
    ruta = (BASE_DIR / nombre).resolve()
    # Solo se ofrecen archivos de la carpeta de contratos (el texto viene de una tool, pero se valida igual).
    if ruta.parent == CONTRATOS_DIR.resolve() and ruta.suffix == ".pdf" and ruta.exists():
        contenido = ruta.read_bytes()
        with st.expander("📄 Ver contrato", expanded=abierto):
            try:   # vista previa dentro del chat (necesita `streamlit[pdf]`); si falta, queda el botón de descarga
                st.pdf(contenido, height=560, key=f"vista-{e['id']}")
            except Exception:
                st.caption("No se pudo mostrar la vista previa: usá el botón de descarga.")
        st.download_button("⬇️ Descargar contrato (PDF)", contenido, file_name=ruta.name,
                           mime="application/pdf", key=f"pdf-{e['id']}")


def _fallo(resultado: dict) -> bool:
    return resultado["estado"] == "error" or resultado["texto"].startswith("ERROR")


def _caja_de_tool(llamada: dict, resultado: dict | None, en_vivo: bool):
    """Caja plegable de una tool con sus argumentos y su resultado.

    Para que no parpadee, la caja NUNCA cambia entre abierta y cerrada mientras se dibuja: nace plegada (o
    abierta, si falló) y así queda. Si ya se conoce el resultado (historial) nace directamente en su estado final;
    en vivo, nace «running» y cuando llega el resultado solo cambia el ícono.
    """
    fallo = bool(resultado) and _fallo(resultado)
    titulo = titulo_llamada(llamada)
    if resultado is None and not en_vivo:
        titulo += " · esperando tu confirmación"       # historial: la tool pidió confirmación y todavía no se ejecutó
    estado = "running" if resultado is None else ("error" if fallo else "complete")
    caja = st.status(titulo, expanded=fallo, state=estado)
    with caja:
        st.caption("Argumentos")
        st.json(llamada["args"], expanded=False)
        if resultado is not None:
            st.caption("Resultado")
            st.code(resultado["texto"][:1500], language=None)
    return caja


def render_eventos(eventos, abrir_pdf: bool = True) -> None:
    """Dibuja los eventos a medida que llegan; sirve en vivo (generador) y para el historial (lista).

    `abrir_pdf`: si la vista previa del contrato se muestra desplegada (ver render_descarga).
    """
    en_vivo = not isinstance(eventos, list)
    # En el historial se conocen todos los resultados de antemano, así cada caja nace ya terminada.
    resultados = {} if en_vivo else {e["id"]: e for e in eventos if e["tipo"] == "resultado"}
    cajas = {}   # en vivo: id de la llamada -> su caja, a la espera del resultado
    for e in eventos:
        tipo = e["tipo"]
        if tipo == "razonamiento":
            with st.expander("💭 Razonamiento", expanded=False):
                # Un salto de línea por renglón y sin sangrías (que markdown tomaría por código).
                st.markdown("  \n".join(l.strip() for l in e["texto"].splitlines()))
        elif tipo == "texto":
            st.markdown(e["texto"])   # st.markdown renderiza el markdown del modelo
        elif tipo == "llamada":
            if en_vivo:
                cajas[e["id"]] = _caja_de_tool(e, None, en_vivo=True)
            else:
                _caja_de_tool(e, resultados.get(e["id"]), en_vivo=False)
        elif tipo == "resultado":
            if en_vivo:
                caja = cajas.get(e["id"])
                if caja is not None:   # solo se agrega el resultado y se cambia el ícono: la caja no se abre ni se cierra
                    with caja:
                        st.caption("Resultado")
                        st.code(e["texto"][:1500], language=None)
                    caja.update(state="error" if _fallo(e) else "complete", expanded=_fallo(e))
            if e["texto"].startswith("PDF generado:"):
                render_descarga(e, abierto=abrir_pdf)
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


def con_progreso(eventos, inicial: str):
    """Pasa los eventos del agente tal cual y, mientras espera el siguiente, muestra qué está haciendo.

    El spinner solo está activo durante la espera (dentro de `next`); entre un evento y el siguiente no hay
    ninguno. Las tools se ejecutan en milisegundos: lo que dura de verdad es la espera al modelo, y ahí el texto
    dice qué paso acaba de terminar («Paso 2 hecho: Buscando el equipo «taladro» · pensando el siguiente…»), para
    que con un modelo lento no se confunda con un cuelgue.
    """
    pasos = 0
    ultimo = ""
    texto = inicial
    iterador = iter(eventos)
    while True:
        with st.spinner(texto):
            try:
                e = next(iterador)
            except StopIteration:
                return
        yield e
        if e["tipo"] == "llamada":          # el modelo pidió una tool: ahora se ejecuta
            pasos += 1
            ultimo = describir_paso(e["nombre"], e["args"])
            texto = f"Paso {pasos} · {ultimo}…"
        elif e["tipo"] == "resultado" and pasos:   # la tool terminó: el modelo piensa qué sigue
            texto = f"Paso {pasos} hecho: {ultimo} · pensando el siguiente…"
        elif e["tipo"] == "resultado":             # (reanudación tras una confirmación: la llamada fue antes)
            texto = "Operación ejecutada · esperando la respuesta del modelo…"


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
    if es_mensaje:
        repair_dangling_call(config)   # si un turno anterior quedó cortado, se arregla ANTES de marcar dónde empieza este
    turno = TurnoEnCurso(config) if es_mensaje else None
    if turno:
        st.session_state.en_curso = (entrada, turno)
    try:
        with mensaje("assistant"):
            if es_mensaje:
                st.button("⏹ Detener", key="detener", help="Cancela esta respuesta y te deja editar tu mensaje")
            try:
                render_eventos(con_progreso(stream_turn(entrada, config),
                                            "Pensando…" if es_mensaje else "Ejecutando lo que decidiste…"))
            except Exception as e:
                # El modelo falló (cuota agotada, saturación...). Se guarda el aviso para mostrarlo después del
                # st.rerun() y, si era un mensaje nuevo, se descarta el turno sin respuesta y el texto vuelve a
                # la caja para reenviarlo cuando se pueda.
                tipo, titulo, detalle = explicar_error(e)
                if not es_mensaje:   # reanudación tras Aprobar/Rechazar: primero corre la tool y recién después responde el modelo
                    detalle += (" La operación que aprobaste ya se ejecutó (mirá su resultado en la conversación); "
                                "lo que falló fue la respuesta del modelo.")
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

def render_aprobacion(acciones: list[dict], procesando: str | None = None) -> None:
    """Resumen de la escritura pendiente con botones Aprobar / Rechazar.

    Con `procesando` (un texto) los botones se reemplazan por ese aviso: durante la ejecución de lo aprobado la
    tarjeta vieja seguiría en pantalla, atenuada pero clickeable, y un segundo clic interrumpe la corrida a medias.
    """
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
                equipos = "\n".join(f"- {e['cantidad']} × {nombres.get(e['herramienta_id'], 'equipo #' + str(e['herramienta_id']))}"
                                    for e in args.get("equipos", []))
                cliente, domicilio = cliente_y_domicilio(args.get("cliente_id"), args.get("domicilio_id"))
                monto = f"${args['monto_total']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                st.markdown(
                    f"### Nuevo alquiler\n"
                    f"- **Título:** {args.get('titulo')}\n"
                    f"- **Cliente:** {cliente}\n"
                    f"- **Domicilio de entrega:** {domicilio}\n"
                    f"- **Fechas:** {fecha_ddmmaaaa(args.get('fecha_inicio'))} al {fecha_ddmmaaaa(args.get('fecha_fin'))}\n"
                    f"- **Valor total:** {monto}\n"
                    f"- **Equipos:**\n{equipos}"
                )
            else:
                st.json({"operación": a["name"], **args})
        if procesando:
            st.info(procesando, icon="⏳")
            return
        c1, c2 = st.columns(2)
        if c1.button("✅ Aprobar", type="primary", use_container_width=True):
            st.session_state.entrada = decision(True)
            st.rerun()
        if c2.button("❌ Rechazar (cancelar)", use_container_width=True):
            st.session_state.entrada = decision(False)
            st.rerun()


# --- Sesión y barra lateral ------------------------------------------------------------

def render_modelo() -> None:
    """Selector de proveedor: reconstruye el agente (la memoria de las conversaciones se conserva).

    El modelo de cada proveedor no se elige acá: sale del .env (MODEL_NAME / OPENROUTER_MODEL / GROQ_MODEL). Se
    muestra debajo del selector como campo de solo lectura (y también en la cabecera).
    """
    seccion("Modelo")
    ids = list(PROVEEDORES)
    en_uso = modelo_actual()
    proveedor = st.selectbox("Proveedor", ids, format_func=PROVEEDORES.get, key="proveedor",
                             index=ids.index(en_uso[0]) if en_uso[0] in ids else 0)
    try:
        usar_modelo(proveedor, modelo_por_defecto(proveedor))
    except Exception as e:   # falta la clave, paquete sin instalar...
        st.error(f"No se pudo usar ese proveedor: {e}")
    # El modelo en uso, solo para mirar (deshabilitado: no se edita desde la pantalla).
    st.text_input("Modelo", value=modelo_actual()[1], disabled=True, key=f"modelo_{modelo_actual()[0]}")
    n_clave, n_claves = claves_en_uso()
    if n_claves > 1:   # solo el número: las claves nunca se muestran
        st.caption(f"🔑 Clave {n_clave} de {n_claves} (cambia sola cuando una se queda sin cupo)")


def seccion(titulo: str) -> None:
    """Rótulo pequeño en mayúsculas para separar bloques de la barra lateral."""
    st.html(f'<div class="seccion">{escape(titulo)}</div>')


def etiqueta_de_fecha(fecha: date | None) -> str:
    """«Hoy», «Ayer» o la fecha en DD-MM-AAAA (los grupos de la lista de conversaciones)."""
    hoy = date.today()
    if fecha is None:
        return "Anteriores"
    if fecha == hoy:
        return "Hoy"
    if fecha == hoy - timedelta(days=1):
        return "Ayer"
    return fecha_ddmmaaaa(fecha)


def render_conversaciones(actual: str) -> None:
    """Conversaciones agrupadas por día, con un botón para borrar cada una (pide confirmación: no se puede deshacer)."""
    por_borrar = st.session_state.get("borrando")
    grupo = None
    for thread_id, titulo, fecha in list_threads():
        etiqueta = etiqueta_de_fecha(fecha)
        if etiqueta != grupo:
            grupo = etiqueta
            st.html(f'<div class="grupo">{escape(etiqueta)}</div>')
        if por_borrar == thread_id:
            st.caption(f"¿Borrar «{titulo}»? No se puede deshacer.")
            c1, c2 = st.columns(2)
            if c1.button("Sí, borrar", key=f"si-{thread_id}", type="primary", use_container_width=True):
                delete_thread(thread_id)
                st.session_state.pop("borrando", None)
                if thread_id == actual:   # se borró la que estaba abierta: se pasa a una conversación nueva
                    st.session_state.config = new_thread()
                st.rerun()
            if c2.button("No", key=f"no-{thread_id}", use_container_width=True):
                st.session_state.pop("borrando", None)
                st.rerun()
        else:
            titulo_col, borrar_col = st.columns([5, 1], vertical_alignment="center")
            if titulo_col.button(titulo, key=thread_id, disabled=thread_id == actual, use_container_width=True):
                st.session_state.config = select_thread(thread_id)
                st.rerun()
            if borrar_col.button("🗑", key=f"del-{thread_id}", help="Borrar esta conversación"):
                st.session_state.borrando = thread_id
                st.rerun()


def render_sidebar() -> None:
    actual = st.session_state.config["configurable"]["thread_id"]
    with st.sidebar:
        # Logo arriba de todo, en la esquina (el nombre «argenequip» ya es parte del logo).
        if LOGO.exists():
            st.image(str(LOGO), width=150)
        else:
            st.html('<div class="marca-lateral">Argen<span>Equip</span></div>')
        st.html('<div class="sub-lateral">Asistente de alquileres</div>')
        render_modelo()

        seccion("Conversaciones")
        if st.button("➕ Nueva conversación", disabled=not saved_turns(st.session_state.config), use_container_width=True):
            st.session_state.config = new_thread()
            st.rerun()
        render_conversaciones(actual)


def render_cabecera() -> None:
    """Qué sistema (real o de demo) y qué modelo se están usando. El logo está en la barra lateral."""
    proveedor, nombre = modelo_actual()
    es_api = type(get_client()).__name__ == "HttpClient"
    modo = ('<span class="chip ok">● Sistema real (API)</span>' if es_api
            else '<span class="chip demo">● Datos de demo</span>')
    # Botón de tema: Streamlit no deja cambiar el tema desde Python, pero sí recargar la página con
    # ?embed_options=light_theme / dark_theme. Al recargar se retoma la última conversación (resume_thread).
    claro = st.context.theme.type == "light"
    cambiar = ('<a class="chip tema" href="?embed_options=dark_theme" target="_self" title="Cambiar a modo oscuro">🌙 Oscuro</a>' if claro
               else '<a class="chip tema" href="?embed_options=light_theme" target="_self" title="Cambiar a modo claro">☀️ Claro</a>')
    st.html('<div class="cabecera">'
            f'<div class="chips">{modo}<span class="chip">{escape(PROVEEDORES.get(proveedor, proveedor))} · {escape(nombre)}</span>{cambiar}</div>'
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
            '<q>Listame todos los alquileres de Juan Pérez</q> o '
            '<q>Generame el contrato del alquiler 12</q></div>')


@st.cache_resource
def _arranque() -> dict:
    """Estado que vive mientras dure el servidor de Streamlit (no se pierde al recargar la página)."""
    return {"conversacion_nueva": False}


if "config" not in st.session_state:
    arranque = _arranque()
    if not arranque["conversacion_nueva"]:
        # Primera sesión desde que se levantó el servidor: se empieza en una conversación nueva (las anteriores
        # siguen en la lista de la barra lateral).
        arranque["conversacion_nueva"] = True
        st.session_state.config = new_thread()
    else:
        # Recarga de la página o pestaña nueva con el servidor ya andando: se sigue con la última conversación.
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

# Streamlit re-ejecuta todo el script en cada interacción: se redibuja la conversación guardada.
turnos = saved_turns(config, hasta=cancelado.mensajes_antes if en_descarte else None)
if not turnos and entrada is None and not acciones:
    render_bienvenida()
for i, (pregunta, eventos) in enumerate(turnos):
    mensaje("user").markdown(pregunta)
    with mensaje("assistant"):
        render_eventos(eventos, abrir_pdf=(i == len(turnos) - 1))   # solo el contrato más reciente, desplegado

# El aviso de una falla del modelo va justo arriba de la caja de chat, no arriba de todo: con una conversación
# larga (y la vista abajo, donde está la confirmación) un aviso en la cabecera pasaría inadvertido.
if entrada is None:   # si está por correr un turno, el aviso viejo ya no corresponde
    render_error()

if isinstance(entrada, str):
    mensaje("user").markdown(entrada)

if entrada is not None:
    if not isinstance(entrada, str) and acciones:
        # Aprobar / Rechazar: se redibuja la tarjeta SIN botones en el mismo lugar, así la vieja (que Streamlit
        # deja en pantalla hasta que termine la corrida) no se puede volver a tocar.
        aprobo = entrada.resume["decisions"][0]["type"] == "approve"
        render_aprobacion(acciones, procesando=("Aprobado: se está ejecutando la operación; puede tardar unos segundos "
                                                "(el modelo todavía tiene que responder). No toques nada hasta que termine."
                                                if aprobo else "Rechazado: se está avisando al agente…"))
    correr(entrada)
elif acciones:
    render_aprobacion(acciones)
