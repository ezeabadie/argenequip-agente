"""Generador del PDF del contrato de alquiler (fpdf2, solo Python: no hace falta instalar nada más).

Recibe un diccionario con los datos YA formateados (los arma `generar_contrato_pdf` en tools.py a partir
de la API) y devuelve los bytes del PDF. Las cláusulas son fijas: las redacta el código, no el modelo,
así un contrato nunca incluye texto legal inventado. Para cambiarlas se edita CLAUSULAS acá.

Fuente: Helvetica, que viene con el PDF y cubre el español (latin-1). Los caracteres fuera de latin-1
(p. ej. comillas tipográficas) se reemplazan antes de escribir.
"""
from fpdf import FPDF

NO_INFORMADO = "No informado"

# Texto de cada cláusula; {…} se reemplaza con los datos del contrato.
CLAUSULAS = [
    ("PRIMERA - Objeto",
     "EL LOCADOR entrega en alquiler al LOCATARIO los equipos que se detallan a continuación, para ser "
     "utilizados en el domicilio de uso indicado ({domicilio})."),
    ("SEGUNDA - Plazo",
     "El alquiler comienza el {inicio} y finaliza el {fin}. Toda extensión del plazo debe acordarse "
     "previamente con EL LOCADOR."),
    ("TERCERA - Precio y forma de pago",
     "El precio total del alquiler es de {monto_total}. A la fecha de emisión de este contrato se registran "
     "pagos por {pagado}, quedando un saldo de {saldo}."),
    ("CUARTA - Uso y conservación",
     "EL LOCATARIO se compromete a usar los equipos con la diligencia debida y conforme a su destino, a no "
     "cederlos ni subalquilarlos, y a no trasladarlos del domicilio de uso sin autorización de EL LOCADOR."),
    ("QUINTA - Devolución",
     "Vencido el plazo, EL LOCATARIO devolverá los equipos limpios y en el estado en que los recibió, salvo "
     "el desgaste normal por su uso. La demora en la devolución habilita a EL LOCADOR a reclamar el alquiler "
     "de los días excedidos."),
    ("SEXTA - Daños y pérdida",
     "Los daños que excedan el desgaste normal, y la pérdida o el robo de los equipos durante el alquiler, "
     "son responsabilidad de EL LOCATARIO, quien deberá abonar su reparación o reposición."),
    ("SÉPTIMA - Conformidad",
     "Ambas partes declaran conocer y aceptar las condiciones de este contrato, que se firma en dos "
     "ejemplares de un mismo tenor."),
]

AVISO = ("Modelo base generado automaticamente por el asistente de ArgenEquip. Debe ser revisado por las "
         "partes (y, si corresponde, por un asesor legal) antes de su firma.")

# Si al llegar a la última cláusula ya se pasó de esta altura (mm), esa cláusula y las firmas van a una hoja
# nueva: página A4 de 297 mm, menos margen inferior, menos lo que ocupan la cláusula, las firmas y el aviso.
LIMITE_ULTIMA_CLAUSULA = 215

_REEMPLAZOS = {"–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "•": "-", "€": "EUR"}


def _l1(texto) -> str:
    """Texto seguro para la fuente Helvetica (latin-1): reemplaza lo que no entra y escapa el markdown."""
    s = str(texto)
    for malo, bueno in _REEMPLAZOS.items():
        s = s.replace(malo, bueno)
    return s.encode("latin-1", "replace").decode("latin-1").replace("**", "*").replace("__", "_")


class _Contrato(FPDF):
    numero = ""

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(110)
        self.cell(0, 10, _l1(f"Contrato de alquiler N° {self.numero}  -  Página {self.page_no()}/{{nb}}"), align="C")


def armar_contrato(datos: dict, comprimir: bool = True) -> bytes:
    """Devuelve el PDF del contrato. `datos`: ver generar_contrato_pdf en tools.py."""
    cliente = datos["cliente"]
    campos = {k: _l1(datos.get(k) or NO_INFORMADO) for k in
              ("domicilio", "inicio", "fin", "monto_total", "pagado", "saldo")}

    pdf = _Contrato(format="A4")
    pdf.numero = datos["numero"]
    pdf.set_compression(comprimir)
    pdf.alias_nb_pages()
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(auto=True, margin=22)
    pdf.add_page()

    # --- Encabezado
    pdf.set_font("Helvetica", "B", 17)
    pdf.cell(0, 10, "CONTRATO DE ALQUILER DE EQUIPOS", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90)
    pdf.cell(0, 6, _l1(f"Contrato N° {datos['numero']}  -  {datos['titulo']}"), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, _l1(f"Fecha de emisión: {datos['emision']}"), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(4)

    # --- Partes
    # Los marcadores de negrita (**) son nuestros; lo que viene de los datos se limpia con _l1 antes de insertarlo.
    pdf.set_font("Helvetica", "", 10.5)
    contacto = ", ".join(x for x in (f"teléfono {_l1(cliente['telefono'])}" if cliente.get("telefono") else "",
                                      f"correo {_l1(cliente['mail'])}" if cliente.get("mail") else "") if x)
    pdf.multi_cell(
        0, 5.5,
        f"Entre **ArgenEquip**, en adelante EL LOCADOR, y **{_l1(cliente['nombre'])}**, DNI/CUIT "
        f"{_l1(cliente.get('dni') or NO_INFORMADO)}" + (f" ({contacto})" if contacto else "") +
        ", en adelante EL LOCATARIO, se celebra el presente contrato de alquiler de equipos, "
        "sujeto a las siguientes cláusulas:",
        align="J", markdown=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # --- Cláusulas (la primera lleva la tabla de equipos; la tercera, la de pagos)
    for i, (titulo, texto) in enumerate(CLAUSULAS):
        if i == len(CLAUSULAS) - 1 and pdf.get_y() > LIMITE_ULTIMA_CLAUSULA:
            pdf.add_page()   # la última cláusula y las firmas van juntas: las firmas nunca quedan solas en una hoja
        pdf.set_font("Helvetica", "", 10.5)
        pdf.multi_cell(0, 5.5, f"**{titulo}.** " + texto.format(**campos), align="J", markdown=True,
                       new_x="LMARGIN", new_y="NEXT")
        if titulo.startswith("PRIMERA"):
            _tabla(pdf, ("Equipo", "Cantidad"), datos["equipos"], (140, 30))
        elif titulo.startswith("TERCERA") and datos.get("pagos"):
            pdf.ln(1)
            _tabla(pdf, ("Fecha", "Monto", "Comprobante"), datos["pagos"], (45, 60, 65))
        pdf.ln(2)

    if datos.get("descripcion"):
        pdf.multi_cell(0, 5.5, f"**Observaciones.** {_l1(datos['descripcion'])}", align="J", markdown=True,
                       new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # --- Firmas
    pdf.ln(16)
    y = pdf.get_y()
    for x, rol, nombre in ((20, "EL LOCADOR", "ArgenEquip"), (115, "EL LOCATARIO", cliente["nombre"])):
        pdf.line(x, y, x + 75, y)
        pdf.set_xy(x, y + 1)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(75, 5, _l1(rol), align="C", new_x="LEFT", new_y="NEXT")
        pdf.set_x(x)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(75, 5, _l1(nombre), align="C", new_x="LEFT", new_y="NEXT")
        pdf.set_x(x)
        pdf.cell(75, 5, "Firma y aclaración", align="C")
    pdf.set_xy(20, y + 22)

    # --- Aviso
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(110)
    pdf.multi_cell(0, 4, _l1(AVISO), align="C", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


def _tabla(pdf: FPDF, encabezados: tuple, filas: list, anchos: tuple) -> None:
    """Tabla simple: encabezado en negrita y una fila por elemento."""
    pdf.set_font("Helvetica", "", 10)
    pdf.ln(1)
    with pdf.table(col_widths=anchos, text_align="LEFT", line_height=6, width=sum(anchos)) as tabla:
        fila = tabla.row()
        for h in encabezados:
            fila.cell(_l1(h))
        for datos_fila in filas:
            fila = tabla.row()
            for celda in datos_fila:
                fila.cell(_l1(celda))
