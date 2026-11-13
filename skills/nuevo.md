---
name: nuevo
description: Procedimiento del comando «nuevo»: crear un alquiler paso a paso (cliente, domicilio, equipos, fechas, disponibilidad, monto, confirmación).
---

# Comando «nuevo»: crear un alquiler

Datos que hacen falta antes de crear: **cliente, domicilio, equipos con cantidades, fecha de inicio, fecha de fin y monto total**.
Si el socio ya dio alguno en su mensaje, no se lo vuelvas a preguntar.

## Pasos (en este orden)
1. **Cliente**: `buscar_cliente` con el nombre o DNI que dio el socio.
   - 0 resultados: pedí otro nombre o el DNI.
   - Más de 1: mostrale las opciones (nombre, DNI, domicilios) y **preguntá cuál**. Nunca elijas vos.
2. **Domicilio**: si el cliente tiene más de uno, preguntá cuál. Si tiene uno solo, usalo y avisalo.
3. **Equipos**: `buscar_equipos` por cada equipo pedido para obtener su id. Si el nombre es ambiguo o no existe, preguntá.
4. **Fechas**: formato AAAA-MM-DD. Si el socio dice «del 24 al 28» sin mes/año, asumí el mes y año actuales (la fecha de hoy está en el prompt) y confirmalo en el resumen. La fecha de inicio no puede ser posterior a la de fin.
5. **Disponibilidad**: `consultar_disponibilidad` **una vez por equipo**, con las fechas y la cantidad pedidas. Si alguno «NO ALCANZA», informalo y preguntá cómo seguir (otra cantidad, otras fechas o quitar el equipo). No crees el alquiler.
6. **Monto**: lo dice el socio. Si no lo dio, preguntalo. **No lo calcules ni lo estimes.**
7. **Título**: armalo vos, corto y descriptivo (ej. «Obra Pérez - San Martín 450»).
8. **Crear**: llamá `crear_alquiler`. El sistema le pedirá confirmación al socio automáticamente; no hace falta que pidas permiso por texto antes.

## Después de crear
Informá el número de alquiler, el cliente, las fechas, el valor y el estado. Aclará que el stock quedó reservado.
Si el sistema devuelve un ERROR, explicalo en palabras simples y proponé una salida.

## Cancelar
Si el socio dice «cancelar» a mitad del proceso, abandoná el alta y confirmalo. No se creó nada.
