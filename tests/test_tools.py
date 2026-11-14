"""Las tools nunca lanzan excepciones: devuelven texto útil para el modelo."""
from datetime import date, timedelta

import tools

FUTURO = {"fecha_inicio": "2030-01-24", "fecha_fin": "2030-01-28"}


def run(tool, **args):
    return tool.invoke(args)


def test_buscar_cliente_avisa_cuando_hay_varios(cliente):
    out = run(tools.buscar_cliente, texto="Pérez")
    assert "HAY 2 CLIENTES" in out and "San Martín" in out and "Belgrano" in out


def test_buscar_cliente_ignora_tildes_y_mayusculas(cliente):
    """Como el backend real: «juan perez» encuentra a «Juan Pérez»."""
    out = run(tools.buscar_cliente, texto="juan perez")
    assert "1 resultado" in out and "Juan Pérez" in out
    assert "Taladro" in run(tools.buscar_equipos, texto="TALADRO")


def test_cliente_y_domicilio_para_la_tarjeta_de_confirmacion(cliente):
    assert tools.cliente_y_domicilio(2, 21) == ("Carlos Pérez · DNI 27333444", "Belgrano 1200")
    nombre, direccion = tools.cliente_y_domicilio(2, 31)            # el 31 es el domicilio de María Gómez, no de Carlos
    assert nombre.startswith("Carlos Pérez") and "no es uno de los domicilios del cliente" in direccion
    assert tools.cliente_y_domicilio(999, 1) == ("cliente #999", "domicilio #1")   # cliente inexistente: ids, sin romper


def test_buscar_cliente_sin_resultados(cliente):
    assert run(tools.buscar_cliente, texto="Zzz").startswith("Sin resultados")


def test_buscar_equipos_muestra_stock_util(cliente):
    assert "stock útil 9" in run(tools.buscar_equipos, texto="andamio")


def test_disponibilidad_no_alcanza(cliente):
    hoy = date.today()
    out = run(tools.consultar_disponibilidad, herramienta_id=1, fecha_desde=hoy.isoformat(),
              fecha_hasta=(hoy + timedelta(days=2)).isoformat(), cantidad=2)
    assert "NO ALCANZA" in out and "disponibles 1" in out


def test_errores_se_devuelven_como_texto(cliente):
    assert run(tools.consultar_disponibilidad, herramienta_id=999, fecha_desde="2030-01-01",
               fecha_hasta="2030-01-02", cantidad=1).startswith("ERROR: No encontrado")
    assert "DD-MM-AAAA" in run(tools.consultar_disponibilidad, herramienta_id=1, fecha_desde="24/01",
                               fecha_hasta="2030-01-02")


def test_fechas_en_formato_dd_mm_aaaa(cliente):
    """El socio habla en DD-MM-AAAA: las tools lo aceptan y lo devuelven así; el backend sigue recibiendo ISO."""
    out = run(tools.crear_alquiler, cliente_id=1, domicilio_id=11, titulo="Obra", monto_total=1000,
              equipos=[{"herramienta_id": 4, "cantidad": 1}], fecha_inicio="24-01-2030", fecha_fin="28-01-2030")
    assert "24-01-2030 a 28-01-2030" in out
    guardado = cliente.alquileres[max(cliente.alquileres)]
    assert str(guardado["fechaInicio"]) == "2030-01-24" and str(guardado["fechaFinEstimada"]) == "2030-01-28"
    disp = run(tools.consultar_disponibilidad, herramienta_id=4, fecha_desde="24-01-2030", fecha_hasta="28-01-2030")
    assert "del 24-01-2030 al 28-01-2030" in disp and "ALCANZA" in disp
    assert "24-01-2030 a 28-01-2030" in run(tools.resumen_alquileres)


def test_fecha_ddmmaaaa_acepta_iso_date_y_texto_raro():
    assert tools.fecha_ddmmaaaa("2030-01-24") == "24-01-2030"
    assert tools.fecha_ddmmaaaa("24-01-2030") == "24-01-2030"
    assert tools.fecha_ddmmaaaa(date(2030, 1, 24)) == "24-01-2030"
    assert tools.fecha_ddmmaaaa("mañana") == "mañana" and tools.fecha_ddmmaaaa(None) == "None"


def _alquilar(titulo, ini, fin, monto, cliente_id=1, domicilio_id=11):
    return run(tools.crear_alquiler, cliente_id=cliente_id, domicilio_id=domicilio_id, titulo=titulo, monto_total=monto,
               equipos=[{"herramienta_id": 4, "cantidad": 1}], fecha_inicio=ini, fecha_fin=fin)


def test_historial_lista_todos_los_alquileres_de_un_cliente(cliente):
    _alquilar("Obra Uno", "01-02-2030", "05-02-2030", 100000)
    _alquilar("Obra Dos", "10-03-2030", "14-03-2030", 50000)
    _alquilar("Obra del otro Pérez", "01-04-2030", "02-04-2030", 1000, cliente_id=2, domicilio_id=21)
    out = run(tools.alquileres_de_cliente, cliente_id=1)
    assert out.startswith("2 alquiler(es) de Juan Pérez")
    assert out.index("Obra Dos") < out.index("Obra Uno")                       # el más reciente primero
    assert "Obra Mitre" not in out and "otro Pérez" not in out                  # de otros clientes: no
    assert "10-03-2030 a 14-03-2030" in out and "equipos: 1 x Taladro" in out
    assert "valor $150.000,00 | pagado $0,00 | saldo $150.000,00" in out       # totales calculados en código


def test_historial_incluye_los_cerrados_pero_no_los_suma(cliente):
    _alquilar("Obra Uno", "01-02-2030", "05-02-2030", 100000)
    cliente.alquileres[1]["estado"] = "CANCELADO"                                # el alquiler semilla (de María Gómez)
    cliente.alquileres[2]["estado"] = "CANCELADO"
    out = run(tools.alquileres_de_cliente, cliente_id=1)
    assert "CANCELADO" in out and "Totales (sin contar cancelados): valor $0,00" in out
    assert "1 CANCELADO" in out


def test_historial_de_un_cliente_sin_alquileres(cliente):
    assert "no tiene alquileres" in run(tools.alquileres_de_cliente, cliente_id=2)
    assert "no tiene alquileres" in run(tools.alquileres_de_cliente, cliente_id=999)


def test_error_inesperado_no_rompe(cliente, monkeypatch):
    monkeypatch.setattr(cliente, "buscar_clientes", lambda t: 1 / 0)
    assert run(tools.buscar_cliente, texto="x").startswith("ERROR inesperado")


def test_crear_alquiler(cliente):
    out = run(tools.crear_alquiler, cliente_id=1, domicilio_id=11, titulo="Obra Pérez", monto_total=120000,
              equipos=[{"herramienta_id": 1, "cantidad": 1}], **FUTURO)
    assert "Alquiler creado: #2" in out and "$120.000,00" in out and "PENDIENTE" in out


def test_crear_alquiler_stock_insuficiente_texto_util(cliente):
    hoy = date.today()
    out = run(tools.crear_alquiler, cliente_id=1, domicilio_id=11, titulo="X", monto_total=1000,
              equipos=[{"herramienta_id": 1, "cantidad": 3}], fecha_inicio=hoy.isoformat(),
              fecha_fin=(hoy + timedelta(days=1)).isoformat())
    assert out.startswith("ERROR") and "Hormigonera (solicitado: 3, disponible: 1)" in out


def test_crear_alquiler_datos_invalidos(cliente):
    assert "posterior" in run(tools.crear_alquiler, cliente_id=1, domicilio_id=11, titulo="X", monto_total=1000,
                              equipos=[{"herramienta_id": 1, "cantidad": 1}],
                              fecha_inicio="2030-02-01", fecha_fin="2030-01-01")
    assert "domicilio" in run(tools.crear_alquiler, cliente_id=1, domicilio_id=21, titulo="X", monto_total=1000,
                              equipos=[{"herramienta_id": 1, "cantidad": 1}], **FUTURO)
    assert run(tools.crear_alquiler, cliente_id=1, domicilio_id=11, titulo="X", monto_total=0,
               equipos=[{"herramienta_id": 1, "cantidad": 1}], **FUTURO).startswith("ERROR")


def test_resumen_muestra_equipos_valor_y_estado_de_pago(cliente):
    run(tools.crear_alquiler, cliente_id=1, domicilio_id=11, titulo="Obra Pérez", monto_total=100000,
        equipos=[{"herramienta_id": 4, "cantidad": 1}, {"herramienta_id": 2, "cantidad": 2}], **FUTURO)
    cliente.registrar_pago(2, 40000, None)
    out = run(tools.resumen_alquileres)
    assert "2 alquiler(es) activo(s)" in out
    assert "equipos: 2 x Hormigonera" in out                         # el alquiler de la semilla
    assert "equipos: 1 x Taladro, 2 x Andamio" in out
    assert "valor $100.000,00 | pagado $40.000,00 | saldo $60.000,00 | pago: PAGO PARCIAL" in out
    assert "pago: SIN PAGOS" in out
    assert "Saldo pendiente total: $260.000,00" in out


def test_resumen_excluye_cancelados_y_pagados(cliente):
    run(tools.crear_alquiler, cliente_id=1, domicilio_id=11, titulo="Obra", monto_total=1000,
        equipos=[{"herramienta_id": 4, "cantidad": 1}], **FUTURO)
    cliente.registrar_pago(2, 1000, None)                  # saldo 0 -> PAGADO_SIN_FINALIZAR: sigue vigente
    assert "pago: PAGADO" in run(tools.resumen_alquileres)
    cliente.alquileres[1]["estado"] = "CANCELADO"
    out = run(tools.resumen_alquileres)
    assert "1 alquiler(es) activo(s)" in out and "Obra Mitre" not in out
    cliente.alquileres[2]["estado"] = "CANCELADO"
    assert run(tools.resumen_alquileres) == "No hay alquileres activos."
