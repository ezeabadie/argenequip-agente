---
name: comandos
description: Lista de comandos del sistema y cómo interpretar pedidos en lenguaje natural; incluye la regla de «cancelar».
---

# Comandos disponibles hoy

- **nuevo**: crea un alquiler paso a paso (skill «nuevo»). Funciona para hoy o fechas futuras; el stock se reserva al crear.
- **resumen**: muestra todos los alquileres activos con equipos, valor y estado de pago (skill «resumen»).
- **historial**: lista todos los alquileres de un cliente, en cualquier estado (skill «historial»). Ej.: «historial de Juan Pérez», «listame todos los alquileres de Pérez».
- **contrato**: genera el PDF del contrato de un alquiler existente (skill «contrato»). Ej.: «generame el contrato del alquiler 12».
- **cancelar**: cancela cualquier operación en curso.

Otros comandos del sistema (cobrar, objetos, flete, fechas, postergar, stock, etc.) **todavía no están disponibles en este asistente**: si el socio los pide, decile que aún no se pueden hacer desde acá.

## Lenguaje natural
El socio puede escribir el comando solo («nuevo», «resumen») o una frase libre («quiero alquilarle a Pérez una hormigonera», «mostrame los alquileres de Gómez»). En ambos casos cargá la skill correspondiente con `load_skill` y seguí su procedimiento.

## cancelar
Descartá la operación en curso, no ejecutes ninguna escritura pendiente y confirmá que no se modificó nada.
