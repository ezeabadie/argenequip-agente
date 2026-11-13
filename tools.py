"""Tools del agente ArgenEquip (alcance actual: comandos `nuevo`, `resumen` e `historial`).

Reglas comunes a todas:
- El docstring es lo que ve el modelo para decidir cuándo usarla; los argumentos tienen tipos.
- Nunca lanzan excepciones: el decorador `@seguro` convierte cualquier falla en un texto
  "ERROR: ..." que el modelo puede leer y explicarle al socio (la consigna pide que un
  error no corte el flujo del agente).
- Hablan con el sistema a través de `get_client()` (API real o mock), nunca con la base.
- Las cuentas y los formatos se hacen acá, en código (el modelo no calcula).

La tool de ESCRITURA (`crear_alquiler`) va con confirmación humana (ver WRITE_TOOLS).
"""
import functools
from datetime import date, datetime
from pathlib import Path

from langchain.tools import tool
from pydantic import BaseModel, Field

from argenequip_client import ESTADOS_CERRADOS, ArgenEquipError, get_client

BASE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = BASE_DIR / "skills"


# --- Utilidades ---------------------------------------------------------------

def seguro(fn):
    """Captura toda excepción y la devuelve como texto, para no romper el flujo del agente."""
    @functools.wraps(fn)   # conserva nombre, docstring y firma (los usa @tool para el esquema)
    def envoltura(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ArgenEquipError as e:
            return f"ERROR: {e}"
        except Exception as e:  # último recurso: bug inesperado
            return f"ERROR inesperado en {fn.__name__}: {type(e).__name__}: {e}"
    return envoltura


def _monto(v) -> str:
    """12345.5 -> '$12.345,50' (formato argentino)."""
    s = f"{float(v):,.2f}"
    return "$" + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _fecha(texto: str, campo: str) -> date:
    """Fecha en DD-MM-AAAA (el formato del socio). Por tolerancia acepta también AAAA-MM-DD."""
    for formato in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto.strip(), formato).date()
        except (AttributeError, ValueError):
            continue
    raise ArgenEquipError(f"{campo} inválida «{texto}»: usar el formato DD-MM-AAAA (por ejemplo 24-11-2026).")


def fecha_ddmmaaaa(valor) -> str:
    """'2026-11-24' (como lo guarda el backend) o '24-11-2026' o un date -> '24-11-2026'.

    Es lo que ve el socio. Si no se puede interpretar, devuelve el texto tal cual.
    """
    try:
        return (valor if isinstance(valor, date) else _fecha(str(valor), "fecha")).strftime("%d-%m-%Y")
    except ArgenEquipError:
        return str(valor)


def _estado_de_pago(a: dict) -> str:
    """Estado de pago derivado de los montos (en código, no lo deduce el modelo)."""
    if float(a["saldoPendiente"]) <= 0:
        return "PAGADO"
    return "PAGO PARCIAL" if float(a["montoPagado"]) > 0 else "SIN PAGOS"


def _linea_alquiler(c, a: dict) -> str:
    """Un alquiler como texto (título, cliente, fechas, estado, equipos, valor, pagado, saldo y estado de pago)."""
    detalle = c.ver_alquiler(a["id"])
    equipos = ", ".join(f"{d['cantidadPrestada']} x {d['herramientaNombre']}" for d in detalle["detalles"])
    return (
        f"- #{a['id']} {a.get('titulo') or '(sin título)'} | cliente: {a['clienteNombre']} | "
        f"{fecha_ddmmaaaa(a['fechaInicio'])} a {fecha_ddmmaaaa(a['fechaFinEstimada'])} | estado: {a['estado']}\n"
        f"    equipos: {equipos}\n"
        f"    valor {_monto(a['montoTotal'])} | pagado {_monto(a['montoPagado'])} | "
        f"saldo {_monto(a['saldoPendiente'])} | pago: {_estado_de_pago(a)}"
    )


class ItemEquipo(BaseModel):
    """Un equipo y su cantidad."""
    herramienta_id: int = Field(description="ID del equipo (idHerramienta), obtenido con buscar_equipos")
    cantidad: int = Field(gt=0, description="Cantidad de unidades (mayor a 0)")


# --- Consulta: apoyo para `nuevo` -----------------------------------------------

@tool
@seguro
def buscar_cliente(texto: str) -> str:
    """Busca clientes por nombre o DNI. Devuelve id, nombre, DNI y domicilios (con su id).

    Indica explícitamente si hay 0 resultados o más de uno: en ese caso hay que
    preguntarle al socio cuál es, nunca elegir por él. Si el cliente tiene varios
    domicilios, también hay que preguntar cuál usar.
    """
    clientes = get_client().buscar_clientes(texto)
    if not clientes:
        return f"Sin resultados: no hay clientes que coincidan con «{texto}»."
    filas = []
    for c in clientes:
        doms = "; ".join(f"[id {d['id']}] {d['calle']} {d['numero']}" for d in c["domicilios"]) or "sin domicilios cargados"
        filas.append(f"- id {c['id']}: {c['nombre']} (DNI {c['dni']}) | domicilios: {doms}")
    aviso = (f"HAY {len(clientes)} CLIENTES: preguntale al socio cuál es antes de seguir.\n"
             if len(clientes) > 1 else "1 resultado.\n")
    return aviso + "\n".join(filas)


@tool
@seguro
def buscar_equipos(texto: str = "") -> str:
    """Busca equipos ACTIVOS por nombre (texto vacío = todos) y muestra su stock.

    `stock útil` = stock total - perdido. No considera alquileres en curso: para saber si
    hay unidades libres en unas fechas usá consultar_disponibilidad.
    """
    equipos = get_client().buscar_herramientas(texto)
    if not equipos:
        return f"Sin resultados: no hay equipos activos que coincidan con «{texto}»."
    return "\n".join(
        f"- id {e['idHerramienta']}: {e['nombre']} | stock total {e['stockTotal']}, perdido {e['stockPerdido']}, "
        f"stock útil {e['stockTotal'] - e['stockPerdido']}"
        for e in equipos
    )


@tool
@seguro
def consultar_disponibilidad(herramienta_id: int, fecha_desde: str, fecha_hasta: str, cantidad: int = 1) -> str:
    """Verifica cuántas unidades de UN equipo están libres entre dos fechas (formato DD-MM-AAAA, ej. 24-11-2026).

    Es por equipo: si el pedido tiene varios equipos, llamala una vez por cada uno.
    """
    desde, hasta = _fecha(fecha_desde, "fecha_desde"), _fecha(fecha_hasta, "fecha_hasta")
    d = get_client().disponibilidad(herramienta_id, desde.isoformat(), hasta.isoformat(), cantidad)   # el backend usa ISO
    estado = "ALCANZA" if d["cantidadDisponible"] >= cantidad else "NO ALCANZA"
    return (f"{d['nombreHerramienta']} del {fecha_ddmmaaaa(desde)} al {fecha_ddmmaaaa(hasta)}: "
            f"disponibles {d['cantidadDisponible']}, solicitados {cantidad} -> {estado}.")


# --- Comando `resumen` ------------------------------------------------------------

@tool
@seguro
def resumen_alquileres() -> str:
    """Comando «resumen»: muestra todos los alquileres ACTIVOS (vigentes) con sus equipos, valor y estado de pago.

    Excluye los cancelados y los totalmente pagados. No recibe argumentos.
    """
    c = get_client()
    vigentes = [a for a in c.listar_alquileres() if a["estado"] not in ESTADOS_CERRADOS]
    if not vigentes:
        return "No hay alquileres activos."
    filas = [_linea_alquiler(c, a) for a in vigentes[:20]]   # tope para no saturar el contexto del modelo
    total = sum(float(a["saldoPendiente"]) for a in vigentes)
    aviso = f" (se muestran los primeros 20 de {len(vigentes)})" if len(vigentes) > 20 else ""
    return (f"{len(vigentes)} alquiler(es) activo(s){aviso}:\n" + "\n".join(filas)
            + f"\nSaldo pendiente total: {_monto(total)}")


# --- Comando `historial` -----------------------------------------------------------

@tool
@seguro
def alquileres_de_cliente(cliente_id: int) -> str:
    """Comando «historial»: lista TODOS los alquileres de un cliente, en cualquier estado
    (activos, terminados, pagados y cancelados), del más reciente al más antiguo.

    Recibe el id del cliente, que sale de buscar_cliente (si hay homónimos, hay que preguntarle al
    socio cuál es). Los totales y el estado de pago vienen calculados: no se recalculan.
    """
    c = get_client()
    alquileres = c.listar_alquileres(cliente_id=cliente_id)
    if not alquileres:
        return f"El cliente {cliente_id} no tiene alquileres registrados."
    alquileres.sort(key=lambda a: str(a["fechaInicio"]), reverse=True)
    filas = [_linea_alquiler(c, a) for a in alquileres[:30]]   # tope para no saturar el contexto del modelo
    por_estado: dict[str, int] = {}
    for a in alquileres:
        por_estado[a["estado"]] = por_estado.get(a["estado"], 0) + 1
    vigentes = [a for a in alquileres if a["estado"] != "CANCELADO"]   # un alquiler cancelado no se factura
    valor = sum(float(a["montoTotal"]) for a in vigentes)
    pagado = sum(float(a["montoPagado"]) for a in vigentes)
    saldo = sum(float(a["saldoPendiente"]) for a in vigentes)
    aviso = " (se muestran los 30 más recientes)" if len(alquileres) > 30 else ""
    estados = ", ".join(f"{n} {e}" for e, n in sorted(por_estado.items()))
    return (f"{len(alquileres)} alquiler(es) de {alquileres[0]['clienteNombre']}{aviso} [{estados}]:\n"
            + "\n".join(filas)
            + f"\nTotales (sin contar cancelados): valor {_monto(valor)} | pagado {_monto(pagado)} | saldo {_monto(saldo)}")


# --- ESCRITURA (con confirmación humana) ------------------------------------------

@tool
@seguro
def crear_alquiler(cliente_id: int, domicilio_id: int, titulo: str, fecha_inicio: str, fecha_fin: str,
                   monto_total: float, equipos: list[ItemEquipo], descripcion: str | None = None) -> str:
    """ESCRITURA (comando «nuevo»): crea un alquiler y reserva el stock. Fechas DD-MM-AAAA (ej. 24-11-2026); monto_total lo indica el socio.

    Usala solo con ids obtenidos de buscar_cliente y buscar_equipos, y tras verificar la
    disponibilidad de cada equipo. El socio debe confirmar antes de que se ejecute.
    """
    ini, fin = _fecha(fecha_inicio, "fecha_inicio"), _fecha(fecha_fin, "fecha_fin")
    if ini > fin:
        return "ERROR: la fecha de inicio es posterior a la de fin."
    a = get_client().crear_alquiler({
        "clienteId": cliente_id, "domicilioId": domicilio_id, "titulo": titulo,
        "fechaInicio": ini.isoformat(), "fechaFinEstimada": fin.isoformat(), "montoTotal": monto_total,   # el backend usa ISO
        "descripcion": descripcion, "urlDocumento": None,
        "detalles": [{"herramientaId": e.herramienta_id, "cantidadPrestada": e.cantidad} for e in equipos],
    })
    return (f"Alquiler creado: #{a['id']} {a['titulo']} | cliente: {a['clienteNombre']} | "
            f"{fecha_ddmmaaaa(a['fechaInicio'])} a {fecha_ddmmaaaa(a['fechaFinEstimada'])} | estado: {a['estado']} | "
            f"valor {_monto(a['montoTotal'])}, saldo {_monto(a['saldoPendiente'])}")


# --- Skills (conocimiento bajo demanda, patrón de la cátedra) -------------------------

def _descripcion(ruta: Path) -> str:
    """Valor de `description:` en el encabezado de una skill."""
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.startswith("description:"):
            return linea.removeprefix("description:").strip()
    return ""


def listar_skills() -> str:
    """'nombre: descripción' de cada skill, para el system prompt."""
    return "; ".join(f"{p.stem}: {_descripcion(p)}" for p in sorted(SKILLS_DIR.glob("*.md")))


@tool
def load_skill(nombre: str) -> str:
    """Carga las instrucciones detalladas de una skill (las disponibles figuran en el prompt).

    Usala antes de ejecutar un comando para el que exista una skill, y nombrala al responder.
    """
    skills = {p.stem: p for p in SKILLS_DIR.glob("*.md")}
    if nombre not in skills:
        return f"No existe la skill «{nombre}». Disponibles: {', '.join(skills) or 'ninguna'}."
    return skills[nombre].read_text(encoding="utf-8")


# --- Registro ---------------------------------------------------------------------

READ_TOOLS = [buscar_cliente, buscar_equipos, consultar_disponibilidad, resumen_alquileres, alquileres_de_cliente,
              load_skill]
WRITE_TOOLS = [crear_alquiler]   # las únicas con confirmación humana (HITL)
TOOLS = READ_TOOLS + WRITE_TOOLS
