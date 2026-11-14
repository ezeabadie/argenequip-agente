---
name: contrato
description: Procedimiento del comando «contrato»: generar el PDF del contrato de un alquiler existente con los datos reales del sistema y entregárselo al socio.
---

# Comando «contrato»: PDF del contrato de alquiler

Se usa para pedidos como «generame el contrato del alquiler 12» o «armá el contrato de Juan Pérez».
El PDF lo arma la tool `generar_contrato_pdf` **con datos del sistema y cláusulas fijas**: vos no redactás el contrato.

## Pasos (en este orden)
1. **Identificar el alquiler.** El contrato es de UN alquiler.
   - Si el socio dio el número (#12), usá ese id.
   - Si dio un cliente: `buscar_cliente` (si hay homónimos, preguntá cuál) y después `alquileres_de_cliente`. Mostrale la lista y **preguntá de cuál alquiler** quiere el contrato. Si el cliente tiene uno solo, usalo y avisalo.
   - Si no dijo ni cliente ni número, preguntá. **Nunca inventes ni adivines un id.**
2. **Generar:** llamá `generar_contrato_pdf(alquiler_id)`. No hace falta pedir confirmación: no modifica el sistema, solo crea (o reemplaza) un archivo en la carpeta `contratos/`.
3. **Informar** usando lo que devolvió la tool, **sin cambiar ningún dato**:
   - el nombre del archivo (`contratos/contrato-alquiler-N.pdf`) y que en la pantalla aparece el botón **«Descargar contrato (PDF)»**;
   - un resumen corto: número, cliente, fechas (DD-MM-AAAA), equipos, valor, pagado y saldo;
   - si la tool avisó que faltan datos del cliente (DNI, teléfono, correo), decíselo: figuran como «No informado» y conviene cargarlos en el sistema y volver a generar.
4. **Recordale** que es un modelo base: las partes deben revisarlo (y, si corresponde, un asesor legal) antes de firmarlo.

## Qué contiene el PDF (para describirlo bien, sin inventar)
Encabezado con el número y la fecha de emisión; las partes (ArgenEquip como LOCADOR y el cliente como LOCATARIO, con su DNI); siete cláusulas (objeto con la tabla de equipos, plazo, precio y forma de pago con los pagos ya registrados, uso y conservación, devolución, daños y pérdida, conformidad); espacio de firmas para ambas partes.

## Reglas
- **No inventes datos**: ni DNI, ni domicilio, ni montos, ni cláusulas. Todo sale de la tool.
- No modifiques montos ni fechas: si el socio quiere cambiarlos, hay que corregirlos en el sistema y volver a generar el PDF.
- Fechas en DD-MM-AAAA y montos en formato argentino ($1.234,50), tal como los devuelve la tool.
- Un alquiler **CANCELADO** no tiene contrato: la tool devuelve un ERROR; explicalo en palabras simples.
- Si la tool devuelve otro ERROR (por ejemplo, el alquiler no existe), explicalo y proponé cómo seguir (revisar el número con `resumen` o `historial`).
- Generar de nuevo el mismo alquiler reemplaza el PDF anterior: sirve para actualizar pagos o datos del cliente.
