---
name: historial
description: Procedimiento del comando «historial»: listar todos los alquileres de un cliente (en cualquier estado) con equipos, valor y estado de pago.
---

# Comando «historial»: todos los alquileres de un cliente

Se usa para pedidos como «historial de Juan Pérez» o «listame todos los alquileres de Pérez».

## Pasos
1. **Cliente**: `buscar_cliente` con el nombre o DNI que dio el socio. Si no lo dio, preguntalo.
   - 0 resultados: pedí otro nombre o el DNI.
   - Más de 1: mostrale las opciones (nombre, DNI, domicilios) y **preguntá cuál**. Nunca elijas vos.
2. **Listar**: llamá `alquileres_de_cliente` con el id del cliente elegido. Trae **todos** sus alquileres, de cualquier estado, del más reciente al más antiguo.
3. **Presentar**: mostralos ordenados (una tabla o lista) **sin recalcular nada**: usá los montos, estados y totales tal cual vinieron de la tool. Las fechas, en formato DD-MM-AAAA.
4. Cerrá con los totales (valor, pagado y saldo) y con cuántos alquileres hay en cada estado.

## Estados
`PENDIENTE` (todavía no empezó), `ACTIVO`, `VENCIDO` (pasó la fecha de fin y no se devolvió), `PAGADO_SIN_FINALIZAR` (pagado pero con equipos sin devolver), `FINALIZADO_PENDIENTE_PAGO` (devuelto, falta cobrar), `PAGADO` (terminado y cobrado) y `CANCELADO`. Los cancelados no cuentan en los totales.

Si el cliente no tiene alquileres, decilo simplemente.
