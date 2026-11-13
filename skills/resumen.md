---
name: resumen
description: Procedimiento del comando «resumen»: mostrar los alquileres activos con equipos, valor y estado de pago.
---

# Comando «resumen»: alquileres activos

1. Llamá `resumen_alquileres` (no recibe argumentos). Trae los alquileres vigentes con equipos, valor, pagado, saldo y estado de pago ya calculados.
2. Presentalos al socio de forma ordenada (una tabla o lista), **sin recalcular nada**: usá los montos tal cual vinieron de la tool.
3. Cerrá con el saldo pendiente total que informa la tool.

## Estados de pago
- `SIN PAGOS`: no se cobró nada todavía.
- `PAGO PARCIAL`: hay pagos pero queda saldo.
- `PAGADO`: saldo en cero.

Si no hay alquileres activos, decilo simplemente.
