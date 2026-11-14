"""Cliente del sistema ArgenEquip: una interfaz y dos implementaciones.

- `HttpClient`: le pega a la API REST real (Spring Boot, en localhost).
- `MockClient`: datos en memoria con las MISMAS entradas/salidas, para correr el
  proyecto sin backend ni MySQL (demo reproducible).

Se elige con la variable de entorno ARGENEQUIP_MODE ("api" o "mock") vía `get_client()`.

Convenciones que comparten ambas implementaciones:
- Los métodos devuelven el contenido de `data` ya desenvuelto (dict o list).
- Cualquier falla se informa con `ArgenEquipError`, cuyo mensaje está en español y
  pensado para que lo lea el modelo (las tools lo devuelven tal cual).
"""
import os
import unicodedata
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from decimal import Decimal

import requests


class ArgenEquipError(Exception):
    """Error de negocio o de conexión, con un mensaje claro para el usuario/modelo."""


# Estados con los que el alquiler ya no está "vigente" (ver EstadoAlquiler del backend).
ESTADOS_CERRADOS = {"CANCELADO", "PAGADO"}


class ArgenEquipClient(ABC):
    """Operaciones que necesita el agente. Ambas implementaciones las cumplen."""

    @abstractmethod
    def buscar_clientes(self, texto: str) -> list[dict]:
        """Clientes por nombre o DNI: [{id, nombre, dni, telefono, mail, domicilios:[{id, calle, numero}]}]."""

    @abstractmethod
    def ver_cliente(self, cliente_id: int) -> dict:
        """Un cliente por su id (mismo formato que buscar_clientes). Falla con ArgenEquipError si no existe."""

    @abstractmethod
    def buscar_herramientas(self, texto: str) -> list[dict]:
        """Equipos activos: [{idHerramienta, nombre, stockTotal, stockPerdido, activo}]."""

    @abstractmethod
    def disponibilidad(self, herramienta_id: int, desde: str, hasta: str, cantidad: int) -> dict:
        """{nombreHerramienta, cantidadDisponible, cantidadSolicitada} para un rango de fechas."""

    @abstractmethod
    def crear_alquiler(self, body: dict) -> dict:
        """Crea el alquiler (body = CrearAlquilerRequest). Devuelve AlquilerMutacionResponse."""

    @abstractmethod
    def listar_alquileres(self, estado: str | None = None, cliente: str | None = None,
                          cliente_id: int | None = None) -> list[dict]:
        """Alquileres (AlquilerListaResponse), todas las páginas hasta un tope.

        `cliente` filtra por texto del nombre; `cliente_id`, por el cliente exacto (sin ambigüedad entre homónimos).
        """

    @abstractmethod
    def ver_alquiler(self, alquiler_id: int) -> dict:
        """AlquilerDetalleResponse: cliente, domicilio, detalles (con su id), pagos."""

    @abstractmethod
    def modificar_alquiler(self, body: dict) -> dict:
        """PUT de alquiler (body = ModificarAlquilerRequest). Devuelve AlquilerMutacionResponse."""

    @abstractmethod
    def registrar_pago(self, alquiler_id: int, monto: float, nro_comprobante: str | None) -> dict:
        """Registra un pago. Devuelve {id, monto, fechaHoraPago, nroComprobante, saldoRestante}."""

    @abstractmethod
    def crear_flete(self, alquiler_id: int, responsable: str, valor: float) -> dict:
        """Crea un flete PENDIENTE para el alquiler."""

    @abstractmethod
    def listar_fletes(self, estado: str | None = None, responsable: str | None = None) -> list[dict]:
        """Fletes (todas las páginas hasta un tope), filtrables por estado PENDIENTE/COBRADO."""

    @abstractmethod
    def editar_flete(self, flete_id: int, responsable: str | None, valor: float | None) -> dict:
        """Edita responsable y/o valor de un flete PENDIENTE."""

    @abstractmethod
    def cobrar_flete(self, flete_id: int) -> dict:
        """Marca un flete como COBRADO."""

    @abstractmethod
    def agregar_stock(self, herramienta_id: int, cantidad: int) -> dict:
        """Suma unidades al stock total de un equipo."""


# =============================================================================
# Implementación HTTP (backend real)
# =============================================================================

class HttpClient(ArgenEquipClient):
    MAX_PAGINAS = 5      # tope de seguridad al recorrer listados paginados
    TAM_PAGINA = 50

    def __init__(self, base_url: str, usuario: str, password: str, timeout: float = 15):
        self.base_url = base_url.rstrip("/")
        self.usuario = usuario
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()   # conserva cookies por si el backend las usara
        self.token: str | None = None

    # --- Autenticación (aislada en un único método) ---------------------------

    def _login(self) -> None:
        """Obtiene el JWT.

        Verificado contra el código del backend (LoginUseCase): devuelve
        `data = {"accessToken": "..."}` en el body; no hay cookie ni refresh token.
        Por eso, cuando vence (30 min) se vuelve a hacer login en vez de /refresh.
        Si algún día el JWT viniera por cookie, el `Session` ya la guarda.
        """
        try:
            r = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"correo": self.usuario, "password": self.password},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise self._error_conexion(e)
        if r.status_code != 200:
            raise ArgenEquipError(
                f"No se pudo iniciar sesión en ArgenEquip ({r.status_code}): "
                f"{self._mensaje(r)}. Revisá ARGENEQUIP_USER / ARGENEQUIP_PASSWORD."
            )
        self.token = (r.json().get("data") or {}).get("accessToken")

    # --- Pedido genérico con reintento ante 401 -------------------------------

    def _request(self, metodo: str, ruta: str, **kwargs):
        """Hace el pedido, desenvuelve `data` y traduce los errores a ArgenEquipError."""
        if self.token is None:
            self._login()
        for intento in (1, 2):
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            try:
                r = self.session.request(
                    metodo, f"{self.base_url}{ruta}", headers=headers,
                    timeout=self.timeout, **kwargs,
                )
            except requests.RequestException as e:
                raise self._error_conexion(e)
            if r.status_code == 401 and intento == 1:
                self._login()          # token vencido: reautentica y reintenta una vez
                continue
            break

        if r.status_code >= 400:
            raise self._error_http(r)
        return r.json().get("data") if r.content else None

    @staticmethod
    def _error_conexion(e: Exception) -> ArgenEquipError:
        if isinstance(e, requests.Timeout):
            return ArgenEquipError("El sistema ArgenEquip tardó demasiado en responder (timeout). Reintentá en un momento.")
        return ArgenEquipError("No se pudo conectar con el sistema ArgenEquip. ¿Está levantado el backend?")

    @staticmethod
    def _mensaje(r: requests.Response) -> str:
        """Texto de error del backend: `message` y/o el detalle de `errors`."""
        try:
            cuerpo = r.json()
        except ValueError:
            return r.text[:200] or f"HTTP {r.status_code}"
        partes = [cuerpo.get("message") or ""]
        if cuerpo.get("errors"):
            partes.append("; ".join(f"{k}: {v}" for k, v in cuerpo["errors"].items()))
        return " ".join(p for p in partes if p) or f"HTTP {r.status_code}"

    def _error_http(self, r: requests.Response) -> ArgenEquipError:
        detalle = self._mensaje(r)
        if r.status_code == 401:
            return ArgenEquipError("El sistema rechazó la sesión (401) incluso tras reautenticar.")
        if r.status_code == 403:
            return ArgenEquipError("El usuario configurado no tiene permisos (se requiere rol SOCIO).")
        if r.status_code == 404:
            return ArgenEquipError(f"No encontrado: {detalle}")
        if r.status_code in (400, 409, 422):
            return ArgenEquipError(f"El sistema rechazó la operación: {detalle}")
        return ArgenEquipError(f"Error del sistema ArgenEquip ({r.status_code}): {detalle}")

    def _paginado(self, ruta: str, params: dict) -> list[dict]:
        """Recorre las páginas de un listado (PaginationResponse: content + pagination)."""
        items: list[dict] = []
        for pagina in range(self.MAX_PAGINAS):
            data = self._request("GET", ruta, params={**params, "page": pagina, "size": self.TAM_PAGINA})
            items += data["content"]
            if not data["pagination"]["hasNext"]:
                break
        return items

    # --- Operaciones ----------------------------------------------------------

    def buscar_clientes(self, texto):
        return self._paginado("/api/clients", {"search": texto})

    def ver_cliente(self, cliente_id):
        return self._request("GET", f"/api/clients/{cliente_id}")

    def buscar_herramientas(self, texto):
        data = self._request("GET", "/api/herramientas", params={"search": texto, "verInactivas": "false"})
        return data["herramientas"]

    def disponibilidad(self, herramienta_id, desde, hasta, cantidad):
        return self._request("POST", "/api/herramientas/disponibilidad", json={
            "herramientaId": herramienta_id, "fechaDesde": desde,
            "fechaHasta": hasta, "cantidadSolicitada": cantidad,
        })

    def crear_alquiler(self, body):
        return self._request("POST", "/api/alquileres", json=body)

    def listar_alquileres(self, estado=None, cliente=None, cliente_id=None):
        params = {k: v for k, v in {"estado": estado, "cliente": cliente, "clienteId": cliente_id}.items() if v}
        return self._paginado("/api/alquileres", params)

    def ver_alquiler(self, alquiler_id):
        return self._request("GET", f"/api/alquileres/{alquiler_id}")

    def modificar_alquiler(self, body):
        return self._request("PUT", "/api/alquileres", json=body)

    def registrar_pago(self, alquiler_id, monto, nro_comprobante):
        return self._request("POST", f"/api/alquileres/{alquiler_id}/pagos", json={
            "monto": monto,
            "fechaHoraPago": datetime.now().replace(microsecond=0).isoformat(),
            "nroComprobante": nro_comprobante,
        })

    def crear_flete(self, alquiler_id, responsable, valor):
        return self._request("POST", f"/api/alquileres/{alquiler_id}/fletes",
                             json={"responsable": responsable, "valor": valor})

    def listar_fletes(self, estado=None, responsable=None):
        params = {k: v for k, v in {"estado": estado, "responsable": responsable}.items() if v}
        return self._paginado("/api/fletes", params)

    def editar_flete(self, flete_id, responsable, valor):
        # Particularidad del backend: EditarFleteRequest exige `fleteId` en el body
        # (@NotNull + @Valid se evalúan antes de que el controller lo pise con el de la URL).
        return self._request("PUT", f"/api/fletes/{flete_id}",
                             json={"fleteId": flete_id, "responsable": responsable, "valor": valor})

    def cobrar_flete(self, flete_id):
        return self._request("POST", f"/api/fletes/{flete_id}/cobrar")

    def agregar_stock(self, herramienta_id, cantidad):
        return self._request("PUT", f"/api/herramientas/{herramienta_id}/compra",
                             params={"cantidad": cantidad})


# =============================================================================
# Implementación mock (en memoria)
# =============================================================================

def _d(valor) -> Decimal:
    return Decimal(str(valor))


class MockClient(ArgenEquipClient):
    """Simula el backend con los mismos contratos y las mismas reglas de validación."""

    def __init__(self):
        # Dos "Pérez" con distintos domicilios, para probar la desambiguación.
        self.clientes = [
            {"id": 1, "nombre": "Juan Pérez", "dni": "20111222", "telefono": "261-5550001", "mail": "juan.perez@example.com",
             "domicilios": [{"id": 11, "calle": "San Martín", "numero": "450", "latitud": -32.9, "longitud": -68.8},
                            {"id": 12, "calle": "Las Heras", "numero": "88", "latitud": -32.9, "longitud": -68.8}]},
            {"id": 2, "nombre": "Carlos Pérez", "dni": "27333444", "telefono": "261-5550002", "mail": "carlos.perez@example.com",
             "domicilios": [{"id": 21, "calle": "Belgrano", "numero": "1200", "latitud": -32.9, "longitud": -68.8}]},
            {"id": 3, "nombre": "María Gómez", "dni": "30555666", "telefono": "261-5550003", "mail": "maria.gomez@example.com",
             "domicilios": [{"id": 31, "calle": "Mitre", "numero": "35", "latitud": -32.9, "longitud": -68.8}]},
        ]
        self.herramientas = [
            {"idHerramienta": 1, "nombre": "Hormigonera", "stockTotal": 3, "stockPerdido": 0, "activo": True},
            {"idHerramienta": 2, "nombre": "Andamio", "stockTotal": 10, "stockPerdido": 1, "activo": True},
            {"idHerramienta": 3, "nombre": "Amoladora", "stockTotal": 5, "stockPerdido": 0, "activo": True},
            {"idHerramienta": 4, "nombre": "Taladro", "stockTotal": 4, "stockPerdido": 0, "activo": True},
            {"idHerramienta": 5, "nombre": "Generador", "stockTotal": 2, "stockPerdido": 0, "activo": False},
        ]
        self.alquileres: dict[int, dict] = {}
        self.fletes: dict[int, dict] = {}
        self._sig = {"alquiler": 1, "detalle": 1, "pago": 1, "flete": 1}
        # Alquiler preexistente que ocupa stock (permite simular "stock insuficiente").
        hoy = date.today()
        self._alta_alquiler(clienteId=3, domicilioId=31, titulo="Obra Mitre",
                            fechaInicio=hoy.isoformat(), fechaFinEstimada=(hoy + timedelta(days=30)).isoformat(),
                            montoTotal=200000, descripcion=None,
                            detalles=[{"herramientaId": 1, "cantidadPrestada": 2}])

    # --- utilidades internas --------------------------------------------------

    def _herramienta(self, hid: int) -> dict:
        for h in self.herramientas:
            if h["idHerramienta"] == hid:
                return h
        raise ArgenEquipError(f"No encontrado: Herramienta con id {hid}")

    def _alquiler(self, aid: int) -> dict:
        if aid not in self.alquileres:
            raise ArgenEquipError(f"No encontrado: Alquiler con id {aid}")
        return self.alquileres[aid]

    @staticmethod
    def _fecha(texto: str) -> date:
        try:
            return date.fromisoformat(texto)
        except (TypeError, ValueError):
            raise ArgenEquipError(f"El sistema rechazó la operación: fecha inválida «{texto}» (formato AAAA-MM-DD)")

    def _disponible(self, hid: int, desde: date, hasta: date, excluir: int | None = None) -> int:
        """Stock total - perdido - lo prestado en alquileres vigentes que se solapan con el rango."""
        h = self._herramienta(hid)
        ocupado = 0
        for a in self.alquileres.values():
            if a["id"] == excluir or a["estado"] in ESTADOS_CERRADOS:
                continue
            if date.fromisoformat(a["fechaInicio"]) <= hasta and date.fromisoformat(a["fechaFinEstimada"]) >= desde:
                ocupado += sum(d["cantidadPrestada"] for d in a["detalles"] if d["herramientaId"] == hid)
        return max(h["stockTotal"] - h["stockPerdido"] - ocupado, 0)

    def _validar_stock(self, items: dict[int, int], desde: date, hasta: date, excluir: int | None = None) -> None:
        faltan = []
        for hid, cant in items.items():
            disp = self._disponible(hid, desde, hasta, excluir)
            if cant > disp:
                faltan.append(f"{self._herramienta(hid)['nombre']} (solicitado: {cant}, disponible: {disp})")
        if faltan:
            raise ArgenEquipError("El sistema rechazó la operación: No hay disponibilidad para las siguientes herramientas: "
                                  + "; ".join(faltan))

    def _mutacion(self, a: dict) -> dict:
        """AlquilerMutacionResponse."""
        return {k: a[k] for k in ("id", "fechaInicio", "fechaFinEstimada", "montoTotal", "montoPagado",
                                  "saldoPendiente", "estado", "titulo", "clienteNombre", "descripcion")}

    def _recalcular(self, a: dict) -> None:
        a["montoPagado"] = float(sum(_d(p["monto"]) for p in a["pagos"]))
        a["saldoPendiente"] = float(_d(a["montoTotal"]) - _d(a["montoPagado"]))

    def _alta_alquiler(self, **b) -> dict:
        """Lógica común de alta (la usa crear_alquiler y la semilla)."""
        cliente = next((c for c in self.clientes if c["id"] == b["clienteId"]), None)
        if cliente is None:
            raise ArgenEquipError(f"No encontrado: Cliente con id {b['clienteId']}")
        dom = next((d for d in cliente["domicilios"] if d["id"] == b["domicilioId"]), None)
        if dom is None:
            raise ArgenEquipError(f"El sistema rechazó la operación: el domicilio {b['domicilioId']} no pertenece al cliente {cliente['nombre']}")
        ini, fin = self._fecha(b["fechaInicio"]), self._fecha(b["fechaFinEstimada"])
        errores = []
        if ini > fin:
            errores.append("La fecha de inicio debe ser anterior o igual a la fecha de fin estimada")
        if not b.get("detalles"):
            errores.append("Debe incluir al menos un detalle")
        if not b.get("titulo"):
            errores.append("El título es obligatorio")
        if b.get("montoTotal") is None or _d(b["montoTotal"]) <= 0:
            errores.append("El monto total debe ser mayor a 0")
        if errores:
            raise ArgenEquipError("El sistema rechazó la operación: " + "; ".join(errores))
        pedidos: dict[int, int] = {}
        for d in b["detalles"]:
            self._herramienta(d["herramientaId"])
            if d["cantidadPrestada"] <= 0:
                raise ArgenEquipError("El sistema rechazó la operación: La cantidad prestada debe ser mayor a 0")
            pedidos[d["herramientaId"]] = pedidos.get(d["herramientaId"], 0) + d["cantidadPrestada"]
        self._validar_stock(pedidos, ini, fin)

        aid = self._sig["alquiler"]; self._sig["alquiler"] += 1
        detalles = []
        for d in b["detalles"]:
            detalles.append({"id": self._sig["detalle"], "herramientaId": d["herramientaId"],
                             "herramientaNombre": self._herramienta(d["herramientaId"])["nombre"],
                             "cantidadPrestada": d["cantidadPrestada"], "cantidadDevueltaSana": 0,
                             "cantidadDevueltaDanada": 0, "cantidadPerdida": 0})
            self._sig["detalle"] += 1
        a = {"id": aid, "fechaInicio": b["fechaInicio"], "fechaFinEstimada": b["fechaFinEstimada"], "fechaFinReal": None,
             "montoTotal": float(_d(b["montoTotal"])), "montoPagado": 0.0, "saldoPendiente": float(_d(b["montoTotal"])),
             "urlDocumento": b.get("urlDocumento"), "titulo": b["titulo"], "descripcion": b.get("descripcion"),
             # Como el backend: si empieza hoy o antes queda ACTIVO; si es futuro, PENDIENTE.
             "estado": "ACTIVO" if ini <= date.today() else "PENDIENTE",
             "clienteId": cliente["id"], "clienteNombre": cliente["nombre"], "clienteMail": cliente["mail"],
             "domicilioId": dom["id"], "domicilioCalle": dom["calle"], "domicilioNumero": dom["numero"],
             "detalles": detalles, "pagos": []}
        self.alquileres[aid] = a
        return a

    # --- Operaciones ----------------------------------------------------------

    @staticmethod
    def _sin_tildes(texto: str) -> str:
        """'Juan Pérez' -> 'juan perez'. El backend real busca sin distinguir tildes ni mayúsculas; el mock, igual."""
        return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower().strip()

    def buscar_clientes(self, texto):
        t = self._sin_tildes(texto)
        return [c for c in self.clientes if t in self._sin_tildes(c["nombre"]) or t in c["dni"]]

    def ver_cliente(self, cliente_id):
        for c in self.clientes:
            if c["id"] == cliente_id:
                return c
        raise ArgenEquipError(f"No encontrado: Cliente con id {cliente_id}")

    def buscar_herramientas(self, texto):
        t = self._sin_tildes(texto)
        return [h for h in self.herramientas if h["activo"] and t in self._sin_tildes(h["nombre"])]

    def disponibilidad(self, herramienta_id, desde, hasta, cantidad):
        h = self._herramienta(herramienta_id)
        d, a = self._fecha(desde), self._fecha(hasta)
        if d > a:
            raise ArgenEquipError("El sistema rechazó la operación: La fecha desde debe ser anterior o igual a la fecha hasta")
        return {"nombreHerramienta": h["nombre"], "cantidadDisponible": self._disponible(herramienta_id, d, a),
                "cantidadSolicitada": cantidad}

    def crear_alquiler(self, body):
        return self._mutacion(self._alta_alquiler(**body))

    def listar_alquileres(self, estado=None, cliente=None, cliente_id=None):
        out = []
        for a in self.alquileres.values():
            if estado and a["estado"] != estado:
                continue
            if cliente and cliente.lower() not in a["clienteNombre"].lower():
                continue
            if cliente_id and a["clienteId"] != cliente_id:
                continue
            lista = self._mutacion(a)
            lista["fechaFinReal"] = a["fechaFinReal"]
            lista["cantidadDetalles"] = len(a["detalles"])
            out.append(lista)
        return out

    def ver_alquiler(self, alquiler_id):
        a = self._alquiler(alquiler_id)
        return {**a, "detalles": [dict(d) for d in a["detalles"]], "pagos": [dict(p) for p in a["pagos"]]}

    def modificar_alquiler(self, body):
        a = self._alquiler(body["alquilerId"])
        if a["estado"] in ESTADOS_CERRADOS:
            raise ArgenEquipError("El sistema rechazó la operación: Solo se pueden modificar alquileres en estado "
                                  "PENDIENTE, ACTIVO, VENCIDO, PAGADO_SIN_FINALIZAR o FINALIZADO_PENDIENTE_PAGO")
        ini = self._fecha(body.get("fechaInicio") or a["fechaInicio"])
        fin = self._fecha(body.get("fechaFinEstimada") or a["fechaFinEstimada"])
        if ini > fin:
            raise ArgenEquipError("El sistema rechazó la operación: La fecha de inicio debe ser anterior o igual a la fecha de fin estimada")
        if body.get("montoTotal") is not None and _d(body["montoTotal"]) <= 0:
            raise ArgenEquipError("El sistema rechazó la operación: El monto total debe ser mayor a 0")

        # Se trabaja sobre una copia de los detalles y recién se confirma si todo valida.
        detalles = [dict(d) for d in a["detalles"]]
        por_id = {d["id"]: d for d in detalles}
        for did in body.get("detallesEliminar") or []:
            if did not in por_id:
                raise ArgenEquipError(f"El sistema rechazó la operación: Detalle de alquiler no encontrado: {did}")
        for m in body.get("detallesModificar") or []:
            d = por_id.get(m["detalleId"])
            if d is None:
                raise ArgenEquipError(f"El sistema rechazó la operación: Detalle de alquiler no encontrado: {m['detalleId']}")
            d["cantidadPrestada"] = m["nuevaCantidadPrestada"]
        nuevos = []
        for n in body.get("detallesNuevos") or []:
            h = self._herramienta(n["herramientaId"])
            nuevos.append({"id": self._sig["detalle"], "herramientaId": h["idHerramienta"], "herramientaNombre": h["nombre"],
                           "cantidadPrestada": n["cantidadPrestada"], "cantidadDevueltaSana": 0,
                           "cantidadDevueltaDanada": 0, "cantidadPerdida": 0})
            self._sig["detalle"] += 1
        eliminar = set(body.get("detallesEliminar") or [])
        detalles = [d for d in detalles if d["id"] not in eliminar] + nuevos

        pedidos: dict[int, int] = {}
        for d in detalles:
            pedidos[d["herramientaId"]] = pedidos.get(d["herramientaId"], 0) + d["cantidadPrestada"]
        self._validar_stock(pedidos, ini, fin, excluir=a["id"])

        a["detalles"] = detalles
        a["fechaInicio"], a["fechaFinEstimada"] = ini.isoformat(), fin.isoformat()
        for campo in ("titulo", "descripcion", "urlDocumento"):
            if body.get(campo) is not None:
                a[campo] = body[campo]
        if body.get("montoTotal") is not None:
            a["montoTotal"] = float(_d(body["montoTotal"]))
        self._recalcular(a)
        return self._mutacion(a)

    def registrar_pago(self, alquiler_id, monto, nro_comprobante):
        a = self._alquiler(alquiler_id)
        if a["estado"] in {"PAGADO", "PAGADO_SIN_FINALIZAR", "CANCELADO"}:
            raise ArgenEquipError(f"El sistema rechazó la operación: No se pueden registrar pagos para un alquiler en estado {a['estado']}")
        if _d(monto) <= 0:
            raise ArgenEquipError("El sistema rechazó la operación: El monto debe ser mayor a cero")
        if _d(monto) > _d(a["saldoPendiente"]):
            raise ArgenEquipError(f"El sistema rechazó la operación: El monto supera el saldo deudor actual (${a['saldoPendiente']:.2f}).")
        pago = {"id": self._sig["pago"], "monto": float(_d(monto)),
                "fechaHoraPago": datetime.now().replace(microsecond=0).isoformat(),
                "nroComprobante": nro_comprobante or f"COMP-{self._sig['pago']:04d}"}
        self._sig["pago"] += 1
        a["pagos"].append(pago)
        self._recalcular(a)
        if a["saldoPendiente"] == 0:
            a["estado"] = "PAGADO" if a["estado"] == "FINALIZADO_PENDIENTE_PAGO" else "PAGADO_SIN_FINALIZAR"
        return {**pago, "saldoRestante": a["saldoPendiente"]}

    def crear_flete(self, alquiler_id, responsable, valor):
        a = self._alquiler(alquiler_id)
        if not responsable or not responsable.strip():
            raise ArgenEquipError("El sistema rechazó la operación: El responsable es obligatorio")
        if _d(valor) <= 0:
            raise ArgenEquipError("El sistema rechazó la operación: El valor debe ser mayor a 0")
        f = {"id": self._sig["flete"], "responsable": responsable, "valor": float(_d(valor)),
             "estado": "PENDIENTE", "alquilerId": a["id"], "alquilerTitulo": a["titulo"]}
        self._sig["flete"] += 1
        self.fletes[f["id"]] = f
        return dict(f)

    def _flete(self, fid: int) -> dict:
        if fid not in self.fletes:
            raise ArgenEquipError(f"No encontrado: Flete con id {fid}")
        return self.fletes[fid]

    def listar_fletes(self, estado=None, responsable=None):
        return [dict(f) for f in self.fletes.values()
                if (not estado or f["estado"] == estado)
                and (not responsable or responsable.lower() in f["responsable"].lower())]

    def editar_flete(self, flete_id, responsable, valor):
        f = self._flete(flete_id)
        if f["estado"] != "PENDIENTE":
            raise ArgenEquipError("El sistema rechazó la operación: Solo se pueden editar fletes en estado PENDIENTE")
        if valor is not None and _d(valor) <= 0:
            raise ArgenEquipError("El sistema rechazó la operación: El valor debe ser mayor a 0")
        if responsable:
            f["responsable"] = responsable
        if valor is not None:
            f["valor"] = float(_d(valor))
        return dict(f)

    def cobrar_flete(self, flete_id):
        f = self._flete(flete_id)
        if f["estado"] == "COBRADO":
            raise ArgenEquipError("El sistema rechazó la operación: El flete ya está cobrado")
        f["estado"] = "COBRADO"
        return dict(f)

    def agregar_stock(self, herramienta_id, cantidad):
        h = self._herramienta(herramienta_id)
        if cantidad <= 0:
            raise ArgenEquipError("El sistema rechazó la operación: La cantidad debe ser mayor a 0")
        h["stockTotal"] += cantidad
        return dict(h)


# =============================================================================
# Fábrica
# =============================================================================

_cliente: ArgenEquipClient | None = None


def get_client() -> ArgenEquipClient:
    """Cliente único del proceso, según ARGENEQUIP_MODE (por defecto "mock")."""
    global _cliente
    if _cliente is None:
        modo = os.getenv("ARGENEQUIP_MODE", "mock").lower()
        if modo == "mock":
            _cliente = MockClient()
        elif modo == "api":
            faltan = [v for v in ("ARGENEQUIP_BASE_URL", "ARGENEQUIP_USER", "ARGENEQUIP_PASSWORD") if not os.getenv(v)]
            if faltan:
                raise RuntimeError(f"Modo api: faltan variables en el .env: {', '.join(faltan)}")
            _cliente = HttpClient(os.environ["ARGENEQUIP_BASE_URL"], os.environ["ARGENEQUIP_USER"],
                                  os.environ["ARGENEQUIP_PASSWORD"])
        else:
            raise RuntimeError(f"ARGENEQUIP_MODE inválido: «{modo}» (usar 'api' o 'mock').")
    return _cliente
