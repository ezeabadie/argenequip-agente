"""HttpClient contra un servidor simulado: login, reintento ante 401 y traducción de errores."""
import re

import pytest
import requests

from argenequip_client import ArgenEquipError, HttpClient


class Resp:
    def __init__(self, status=200, data=None, message=None, errors=None):
        self.status_code = status
        self._json = {k: v for k, v in {"data": data, "message": message, "errors": errors}.items() if v is not None}
        self.content = b"x"
        self.text = ""

    def json(self):
        return self._json


class FakeSession:
    """Registra pedidos y responde según un guion (lista de respuestas o excepciones)."""
    def __init__(self, guion):
        self.guion, self.pedidos = list(guion), []

    def _next(self):
        r = self.guion.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def post(self, url, **kw):          # login
        self.pedidos.append(("POST", url, kw))
        return self._next()

    def request(self, metodo, url, **kw):
        self.pedidos.append((metodo, url, kw))
        return self._next()


def cliente_con(guion):
    c = HttpClient("http://x:8080/", "socio@example.com", "pw")
    c.session = FakeSession(guion)
    return c


def test_login_y_unwrap():
    c = cliente_con([Resp(data={"accessToken": "T1"}),
                     Resp(data={"herramientas": [{"idHerramienta": 1}], "cantHerramientas": 1})])
    assert c.buscar_herramientas("a") == [{"idHerramienta": 1}]
    assert c.session.pedidos[1][2]["headers"] == {"Authorization": "Bearer T1"}


def test_ver_cliente_pide_el_cliente_por_su_id():
    c = cliente_con([Resp(data={"accessToken": "T1"}),
                     Resp(data={"id": 7, "nombre": "Juan Pérez", "domicilios": [{"id": 1, "calle": "San Martín", "numero": "450"}]})])
    assert c.ver_cliente(7)["nombre"] == "Juan Pérez"
    metodo, url, _ = c.session.pedidos[1]
    assert metodo == "GET" and url.endswith("/api/clients/7")


def test_reautentica_una_vez_ante_401():
    c = cliente_con([Resp(data={"accessToken": "T1"}), Resp(401), Resp(data={"accessToken": "T2"}),
                     Resp(data={"herramientas": [], "cantHerramientas": 0})])
    assert c.buscar_herramientas("") == []
    assert c.session.pedidos[-1][2]["headers"]["Authorization"] == "Bearer T2"


def test_401_persistente_no_entra_en_bucle():
    c = cliente_con([Resp(data={"accessToken": "T"}), Resp(401), Resp(data={"accessToken": "T"}), Resp(401)])
    with pytest.raises(ArgenEquipError, match="401"):
        c.ver_alquiler(1)


@pytest.mark.parametrize("resp,texto", [
    (Resp(404, message="Alquiler no encontrado"), "No encontrado"),
    (Resp(400, message="Validación", errors={"montoTotal": "debe ser mayor a 0"}), "montoTotal: debe ser mayor a 0"),
    (Resp(409, message="Solapamiento"), "rechazó la operación"),
    (Resp(403), "SOCIO"),
    (Resp(500, message="boom"), "(500)"),
])
def test_errores_http(resp, texto):
    c = cliente_con([Resp(data={"accessToken": "T"}), resp])
    with pytest.raises(ArgenEquipError, match=re.escape(texto)):
        c.ver_alquiler(1)


def test_timeout_y_conexion():
    c = cliente_con([Resp(data={"accessToken": "T"}), requests.Timeout()])
    with pytest.raises(ArgenEquipError, match="timeout"):
        c.ver_alquiler(1)
    c = cliente_con([Resp(data={"accessToken": "T"}), requests.ConnectionError()])
    with pytest.raises(ArgenEquipError, match="backend"):
        c.ver_alquiler(1)


def test_login_fallido():
    c = cliente_con([Resp(400, message="Credenciales incorrectas")])
    with pytest.raises(ArgenEquipError, match="iniciar sesión"):
        c.ver_alquiler(1)


def test_paginado_recorre_paginas():
    def pag(n, hay):
        return Resp(data={"content": [{"id": n}], "pagination": {"hasNext": hay}})
    c = cliente_con([Resp(data={"accessToken": "T"}), pag(1, True), pag(2, False)])
    assert c.listar_alquileres() == [{"id": 1}, {"id": 2}]


def test_editar_flete_manda_fleteid_en_el_body():
    c = cliente_con([Resp(data={"accessToken": "T"}), Resp(data={"id": 7})])
    c.editar_flete(7, "Pedro", None)
    assert c.session.pedidos[1][2]["json"]["fleteId"] == 7
