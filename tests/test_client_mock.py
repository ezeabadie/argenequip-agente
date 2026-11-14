"""El mock debe respetar las reglas del backend real."""
from datetime import date, timedelta

import pytest

from argenequip_client import ArgenEquipError


def body(**extra):
    base = {"clienteId": 1, "domicilioId": 11, "titulo": "Obra", "fechaInicio": "2030-01-24",
            "fechaFinEstimada": "2030-01-28", "montoTotal": 120000, "descripcion": None, "urlDocumento": None,
            "detalles": [{"herramientaId": 1, "cantidadPrestada": 1}]}
    return {**base, **extra}


def test_homonimos(cliente):
    assert len(cliente.buscar_clientes("pérez")) == 2
    assert [c["nombre"] for c in cliente.buscar_clientes("27333444")] == ["Carlos Pérez"]


def test_ver_cliente_por_id(cliente):
    c = cliente.ver_cliente(2)
    assert c["nombre"] == "Carlos Pérez" and [d["calle"] for d in c["domicilios"]] == ["Belgrano"]
    with pytest.raises(ArgenEquipError, match="No encontrado"):
        cliente.ver_cliente(999)


def test_equipos_solo_activos(cliente):
    assert cliente.buscar_herramientas("generador") == []   # inactivo


def test_crear_alquiler_futuro_queda_pendiente(cliente):
    a = cliente.crear_alquiler(body())
    assert a["estado"] == "PENDIENTE" and a["saldoPendiente"] == 120000


def test_crear_alquiler_hoy_queda_activo(cliente):
    hoy = date.today()
    a = cliente.crear_alquiler(body(fechaInicio=hoy.isoformat(), fechaFinEstimada=(hoy + timedelta(days=2)).isoformat()))
    assert a["estado"] == "ACTIVO"


def test_validaciones_de_alta(cliente):
    with pytest.raises(ArgenEquipError, match="anterior o igual"):
        cliente.crear_alquiler(body(fechaInicio="2030-02-01"))
    with pytest.raises(ArgenEquipError, match="al menos un detalle"):
        cliente.crear_alquiler(body(detalles=[]))
    with pytest.raises(ArgenEquipError, match="mayor a 0"):
        cliente.crear_alquiler(body(montoTotal=0))
    with pytest.raises(ArgenEquipError, match="domicilio"):
        cliente.crear_alquiler(body(domicilioId=21))   # es de otro cliente


def test_stock_insuficiente_por_solapamiento(cliente):
    # Semilla: 2 de 3 hormigoneras ocupadas desde hoy por 30 días.
    with pytest.raises(ArgenEquipError, match=r"solicitado: 2, disponible: 1"):
        cliente.crear_alquiler(body(fechaInicio=date.today().isoformat(),
                                    fechaFinEstimada=(date.today() + timedelta(days=3)).isoformat(),
                                    detalles=[{"herramientaId": 1, "cantidadPrestada": 2}]))
    # Fuera del rango ocupado sí hay stock.
    assert cliente.crear_alquiler(body(detalles=[{"herramientaId": 1, "cantidadPrestada": 2}]))["id"]


def test_pagos_y_saldo(cliente):
    a = cliente.crear_alquiler(body())
    p = cliente.registrar_pago(a["id"], 40000, None)
    assert p["saldoRestante"] == 80000
    with pytest.raises(ArgenEquipError, match="supera el saldo"):
        cliente.registrar_pago(a["id"], 90000, None)
    cliente.registrar_pago(a["id"], 80000, "REC-1")
    assert cliente.ver_alquiler(a["id"])["estado"] == "PAGADO_SIN_FINALIZAR"
    with pytest.raises(ArgenEquipError, match="No se pueden registrar pagos"):
        cliente.registrar_pago(a["id"], 1, None)


def test_modificar_detalles_y_stock(cliente):
    a = cliente.crear_alquiler(body())
    det_id = cliente.ver_alquiler(a["id"])["detalles"][0]["id"]
    cliente.modificar_alquiler({"alquilerId": a["id"], "detallesNuevos": [{"herramientaId": 2, "cantidadPrestada": 2}],
                                "detallesModificar": [{"detalleId": det_id, "nuevaCantidadPrestada": 2}]})
    equipos = {d["herramientaNombre"]: d["cantidadPrestada"] for d in cliente.ver_alquiler(a["id"])["detalles"]}
    assert equipos == {"Hormigonera": 2, "Andamio": 2}
    with pytest.raises(ArgenEquipError, match="Andamio"):   # solo hay 9 útiles
        cliente.modificar_alquiler({"alquilerId": a["id"], "detallesNuevos": [{"herramientaId": 2, "cantidadPrestada": 50}]})
    with pytest.raises(ArgenEquipError, match="no encontrado"):
        cliente.modificar_alquiler({"alquilerId": a["id"], "detallesEliminar": [999]})


def test_modificar_fallido_no_deja_cambios(cliente):
    a = cliente.crear_alquiler(body())
    with pytest.raises(ArgenEquipError):
        cliente.modificar_alquiler({"alquilerId": a["id"], "fechaFinEstimada": "2030-01-30",
                                    "detallesNuevos": [{"herramientaId": 2, "cantidadPrestada": 50}]})
    assert cliente.ver_alquiler(a["id"])["fechaFinEstimada"] == "2030-01-28"


def test_fletes(cliente):
    a = cliente.crear_alquiler(body())
    f = cliente.crear_flete(a["id"], "Pedro", 15000)
    assert f["estado"] == "PENDIENTE"
    assert cliente.editar_flete(f["id"], None, 18000)["valor"] == 18000
    assert cliente.cobrar_flete(f["id"])["estado"] == "COBRADO"
    with pytest.raises(ArgenEquipError, match="PENDIENTE"):
        cliente.editar_flete(f["id"], "Otro", None)
    assert cliente.listar_fletes(estado="PENDIENTE") == []


def test_agregar_stock(cliente):
    assert cliente.agregar_stock(3, 4)["stockTotal"] == 9
    with pytest.raises(ArgenEquipError):
        cliente.agregar_stock(99, 1)
