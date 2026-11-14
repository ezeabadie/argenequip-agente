"""PDF del contrato: el generador (contrato.py) y la tool generar_contrato_pdf."""
import pytest

import contrato
import tools

DATOS = {
    "numero": 12, "titulo": "Obra Juan Pérez - San Martín 450", "emision": "13-11-2026",
    "cliente": {"nombre": "Juan Pérez", "dni": "28456123", "telefono": "+54 261 555-0101", "mail": "juan@example.com"},
    "domicilio": "San Martín 450", "inicio": "15-12-2026", "fin": "20-12-2026",
    "equipos": [("Taladro percutor", "3"), ("Amoladora angular 115 mm", "2")],
    "monto_total": "$500.000,00", "pagado": "$100.000,00", "saldo": "$400.000,00",
    "pagos": [("05-12-2026", "$100.000,00", "REC-0001")], "descripcion": "Obra en planta baja",
}


def texto_del_pdf(pdf: bytes) -> str:
    return pdf.decode("latin-1")   # sin comprimir, el texto queda legible dentro del PDF


def test_el_pdf_se_genera_y_tiene_las_partes_del_contrato():
    pdf = contrato.armar_contrato(DATOS, comprimir=False)
    assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF")
    t = texto_del_pdf(pdf)
    for esperado in ("CONTRATO DE ALQUILER DE EQUIPOS", "Juan Pérez", "28456123", "San Martín 450",
                     "15-12-2026", "20-12-2026", "Taladro percutor", "Amoladora angular 115 mm",
                     "$500.000,00", "$400.000,00", "REC-0001", "PRIMERA - Objeto", "SÉPTIMA - Conformidad",
                     "EL LOCADOR", "EL LOCATARIO", "Observaciones"):
        assert esperado in t, f"falta «{esperado}» en el PDF"


def test_faltantes_figuran_como_no_informado_y_no_rompe_con_caracteres_raros():
    datos = {**DATOS, "cliente": {"nombre": "Łukasz “El Maestro” — Núñez", "dni": None, "telefono": None, "mail": None},
             "pagos": [], "descripcion": None}
    t = texto_del_pdf(contrato.armar_contrato(datos, comprimir=False))
    assert "informado" in t and "Núñez" in t              # lo que no entra en latin-1 se reemplaza, sin fallar
    assert "Observaciones" not in t                       # sin descripción no hay sección


def test_el_pdf_comprimido_tambien_es_valido():
    pdf = contrato.armar_contrato(DATOS)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


@pytest.fixture
def contratos(tmp_path, monkeypatch):
    """Los PDF van a una carpeta temporal y sin comprimir, para poder leer su texto."""
    monkeypatch.setattr(tools, "CONTRATOS_DIR", tmp_path / "contratos")
    monkeypatch.setattr(tools, "armar_contrato", lambda d: contrato.armar_contrato(d, comprimir=False))
    return tmp_path / "contratos"


def _alquiler(cliente_id=1, domicilio_id=11):
    return tools.crear_alquiler.invoke({
        "cliente_id": cliente_id, "domicilio_id": domicilio_id, "titulo": "Obra Pérez", "monto_total": 120000,
        "equipos": [{"herramienta_id": 4, "cantidad": 2}], "fecha_inicio": "24-01-2030", "fecha_fin": "28-01-2030"})


def test_la_tool_genera_el_pdf_con_los_datos_del_sistema(cliente, contratos):
    _alquiler()
    cliente.registrar_pago(2, 50000, "REC-9")
    out = tools.generar_contrato_pdf.invoke({"alquiler_id": 2})
    assert out.startswith("PDF generado: contratos/contrato-alquiler-2.pdf")
    assert "24-01-2030 a 28-01-2030" in out and "2 x Taladro" in out and "$120.000,00" in out and "saldo $70.000,00" in out
    archivo = contratos / "contrato-alquiler-2.pdf"
    assert archivo.exists() and archivo.read_bytes().startswith(b"%PDF")
    t = texto_del_pdf(archivo.read_bytes())
    for esperado in ("Juan Pérez", cliente.clientes[0]["dni"], "San Martín 450", "24-01-2030", "$120.000,00", "REC-9", "Taladro"):
        assert esperado in t


def test_avisa_los_datos_del_cliente_que_faltan(cliente, contratos):
    _alquiler()
    cliente.clientes[0]["dni"] = ""                       # Juan Pérez sin DNI cargado
    out = tools.generar_contrato_pdf.invoke({"alquiler_id": 2})
    assert "No informado" in out and "DNI" in out
    assert "informado" in texto_del_pdf((contratos / "contrato-alquiler-2.pdf").read_bytes())


def test_un_alquiler_cancelado_no_tiene_contrato(cliente, contratos):
    cliente.alquileres[1]["estado"] = "CANCELADO"
    out = tools.generar_contrato_pdf.invoke({"alquiler_id": 1})
    assert out.startswith("ERROR") and "CANCELADO" in out
    assert not contratos.exists() or not list(contratos.iterdir())   # no se creó ningún archivo


def test_al_crear_un_alquiler_se_pide_generar_el_contrato():
    """El socio espera ver el contrato apenas confirma el alta: lo piden la skill «nuevo» y el prompt."""
    import agente
    from pathlib import Path
    skill = (Path(tools.SKILLS_DIR) / "nuevo.md").read_text(encoding="utf-8")
    assert "generar_contrato_pdf" in skill and "No generes el contrato" in skill
    assert "generar_contrato_pdf" in agente.SYSTEM_PROMPT and "contrato" in tools.listar_skills()


def test_un_alquiler_inexistente_devuelve_error_en_texto(cliente, contratos):
    assert tools.generar_contrato_pdf.invoke({"alquiler_id": 999}).startswith("ERROR")


def test_generar_de_nuevo_reemplaza_el_pdf(cliente, contratos):
    _alquiler()
    tools.generar_contrato_pdf.invoke({"alquiler_id": 2})
    cliente.registrar_pago(2, 30000, "REC-NUEVO")
    tools.generar_contrato_pdf.invoke({"alquiler_id": 2})
    assert len(list(contratos.iterdir())) == 1
    assert "REC-NUEVO" in texto_del_pdf((contratos / "contrato-alquiler-2.pdf").read_bytes())
