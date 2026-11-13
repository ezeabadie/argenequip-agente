# ArgenEquip · Asistente de alquileres con IA

Agente conversacional (LangChain `create_agent` + LangGraph) que ayuda a un socio de **ArgenEquip**, un sistema de gestión de alquiler de herramientas y equipos para obra, a operar el sistema escribiendo en lenguaje natural. Proyecto integrador de *Modelos de Lenguaje* (Unidad 5: agentes).

Comandos disponibles hoy:

| Comando | Qué hace |
|---|---|
| `nuevo` | Crea un alquiler paso a paso: busca el cliente (y pregunta cuál si hay homónimos), elige el domicilio, busca los equipos, verifica disponibilidad en las fechas, **pide confirmación humana** y lo registra. El stock se reserva al crear. |
| `resumen` | Muestra todos los alquileres activos con sus equipos, valor y estado de pago. |
| `cancelar` | Cancela la operación en curso (en la UI: botón *Rechazar* de la confirmación). |

Se pueden escribir como comando suelto (`resumen`) o en una frase libre (*«Quiero alquilarle a Pérez una hormigonera del 24 al 28 por 120000»*).

## Arquitectura

```
[Streamlit UI] ─► [Agente Python (create_agent)] ──HTTPS──► Gemini (nube)
                          │  devuelve tool_calls ◄──────────────┘
                          ▼  las tools se ejecutan LOCALMENTE
                  argenequip_client.py
                    ├─ modo mock: datos en memoria (sin backend)
                    └─ modo api : API REST Spring Boot (localhost) ─► MySQL
```

El modelo en la nube **nunca toca el sistema**: solo decide qué tool llamar y con qué argumentos. Las tools corren en tu máquina y usan la API REST (no la base directamente), de modo que se respetan las validaciones del backend.

## Requisitos

- **Python 3.11 o superior** (probado en 3.14).
- Una **API key de Gemini** (gratis, desde Google AI Studio).
- Solo para el modo `api`: el backend ArgenEquip (Spring Boot + MySQL) corriendo en local y un usuario con rol SOCIO. Para el modo `mock` no hace falta nada más.

## Instalación

```bash
git clone https://github.com/ezeabadie/argenequip-agente.git
cd argenequip-agente

# Entorno virtual
python -m venv .venv                  # en Windows también sirve: py -m venv .venv
.venv\Scripts\activate                # Windows (PowerShell/cmd)
source .venv/bin/activate             # macOS / Linux

# Dependencias
pip install -r requirements.txt
```

## Configuración

1. Copiá la plantilla de variables de entorno:

   ```bash
   copy .env.example .env             # Windows
   cp .env.example .env               # macOS / Linux
   ```

2. Conseguí tu API key en <https://aistudio.google.com/apikey> (*Create API key*) y pegala en `.env`.

3. Elegí el modo de ejecución. Variables del `.env`:

| Variable | Descripción |
|---|---|
| `GOOGLE_API_KEY` | Clave de Gemini (obligatoria si usás Gemini). |
| `OPENROUTER_API_KEY` | Clave de OpenRouter (<https://openrouter.ai/keys>); solo si elegís ese proveedor. |
| `MODEL_PROVIDER` | Proveedor con el que arranca la app: `google_genai` (por defecto) u `openrouter`. Se cambia después desde la barra lateral. |
| `MODEL_NAME` | Modelo de Gemini. Por defecto `gemini-3.5-flash`, que devuelve el razonamiento que muestra la UI. |
| `OPENROUTER_MODEL` | Slug del modelo de OpenRouter (los gratuitos terminan en `:free`). Tiene que soportar *tool calling*. |
| `ARGENEQUIP_MODE` | `mock` (datos de prueba en memoria) o `api` (backend real). |
| `ARGENEQUIP_BASE_URL` | Solo modo `api`. Ej.: `http://localhost:8080`. |
| `ARGENEQUIP_USER` / `ARGENEQUIP_PASSWORD` | Solo modo `api`. Credenciales de un usuario SOCIO. |

> **El archivo `.env` está en `.gitignore`: nunca lo subas ni pegues tu clave en otros archivos.** `.env.example` solo lleva valores de ejemplo.

### Modo `mock` (recomendado para probar)

Con `ARGENEQUIP_MODE=mock` el proyecto corre solo con Python. Trae datos de prueba:

- Clientes: **Juan Pérez** (domicilios *San Martín 450* y *Las Heras 88*), **Carlos Pérez** (*Belgrano 1200*) y **María Gómez**.
- Equipos: Hormigonera (3), Andamio (10), Amoladora (5), Taladro (4).
- Un alquiler previo (*Obra Mitre*) que ocupa 2 hormigoneras, útil para ver el error de stock insuficiente.

Los datos viven en memoria: se reinician cada vez que se reinicia la aplicación.

### Modo `api` (backend real)

Con `ARGENEQUIP_MODE=api`, el cliente hace login (`POST /api/auth/login`) y usa el token JWT; si vence, vuelve a iniciar sesión automáticamente. Levantá primero el backend y completá `ARGENEQUIP_BASE_URL`, `ARGENEQUIP_USER` y `ARGENEQUIP_PASSWORD`.

## Cómo ejecutarlo

### Interfaz web (Streamlit)

```bash
streamlit run app.py
```

Se abre en <http://localhost:8501>. En la pantalla vas a ver, en vivo mientras el agente trabaja:

- 💭 el **razonamiento** del modelo (desplegable),
- cada **tool invocada** con sus argumentos y su resultado, en orden,
- la respuesta en markdown,
- cuando una operación modifica el sistema, una tarjeta de **confirmación** con los botones **Aprobar** / **Rechazar**.

La barra lateral tiene atajos para `nuevo` y `resumen`, el **selector de proveedor y modelo** (Gemini u OpenRouter; el nombre del modelo es editable) y la lista de conversaciones (cada una conserva su memoria, también si cambiás de modelo a mitad de camino). Si falta la clave del proveedor elegido, la UI avisa y sigue con el modelo anterior.

### Consola

```bash
python agente.py
```

Mismo agente, sin interfaz gráfica: imprime razonamiento, tools y resultados, y pregunta `¿Aprobás? (s/n)` ante una escritura.

### Tests

```bash
pytest
```

Los tests **no usan la API de Gemini** (el modelo se reemplaza por uno falso) ni el backend.

## Guion de demo (funciona en modo `mock`)

1. `nuevo: quiero alquilarle a Pérez una hormigonera del 24 al 28 de este mes por 120000`
   → hay dos Pérez: el agente pregunta cuál.
2. `Juan Pérez, el de San Martín` *(usa la memoria de la conversación)*
   → verifica la disponibilidad y **se pausa pidiendo confirmación** con el resumen del alquiler.
3. Tocá **Aprobar** → se crea el alquiler y el stock queda reservado.
4. `resumen` → lista los alquileres activos, incluido el nuevo, con equipos, valor y estado de pago.

> El plan gratuito de Gemini tiene límites bajos y la latencia puede ser de varios segundos por respuesta. Una demo completa usa unas 8 llamadas al modelo.

## Estructura del repositorio

```
├── app.py                 # UI Streamlit: eventos en vivo + botones de aprobación
├── agente.py              # create_agent, system prompt, HITL, checkpointer, streaming
├── tools.py               # tools del agente (todas capturan sus errores)
├── model.py               # fábrica del modelo (Gemini + respaldo OpenRouter/Groq)
├── argenequip_client.py   # interfaz + cliente HTTP + cliente mock
├── skills/                # conocimiento bajo demanda (markdown)
├── tests/                 # mock, tools, cliente HTTP y agente (con modelo falso)
├── .env.example           # plantilla de configuración (sin claves)
└── requirements.txt
```

## Justificación de los componentes

- **`create_agent` (LangChain)**: el modelo decide en cada paso qué tool llamar, que es lo que necesita un flujo de varios pasos con datos que se descubren sobre la marcha (¿hay dos Pérez? ¿alcanza el stock?). No se usa Deep Agents.
- **Tools** (`tools.py`), todas necesarias para `nuevo` y `resumen`:
  - `buscar_cliente`: obtiene id y domicilios del cliente; avisa explícitamente si hay 0 o más de 1 resultado para que el agente pregunte en vez de elegir.
  - `buscar_equipos`: obtiene el id y el stock de cada equipo (el agente no puede inventar ids).
  - `consultar_disponibilidad`: verifica, por equipo, que haya unidades libres en las fechas pedidas.
  - `crear_alquiler`: escribe en el sistema (**única tool con confirmación humana**).
  - `resumen_alquileres`: consulta los alquileres vigentes; el estado de pago (`SIN PAGOS` / `PAGO PARCIAL` / `PAGADO`) y los totales se calculan en código, no con el modelo.
  - `load_skill`: carga las instrucciones de un comando bajo demanda.
  - Todas devuelven un texto `ERROR: …` ante cualquier falla (timeout, 401, 404, validación del backend, conexión) en lugar de lanzar excepciones, así que un error no corta el flujo del agente.
- **Conocimiento propio: skills** (`skills/*.md`): cada comando tiene su procedimiento en un archivo markdown corto que el agente carga con `load_skill` cuando lo necesita (no ocupa contexto hasta entonces). Se eligió esto en lugar de RAG porque el conocimiento es *procedimental* (qué preguntar y en qué orden), es poco y cabe entero en el contexto; no hay un corpus de texto donde buscar.
- **Memoria de corto plazo**: `SqliteSaver` como checkpointer con un `thread_id` por conversación. Es lo que permite el turno *«Juan Pérez, el de San Martín»* sin repetir el pedido. Persiste en `memoria.db`.
- **Middleware: solo `HumanInTheLoopMiddleware`, sobre `crear_alquiler`.** Crear un alquiler escribe en el sistema real y es difícil de deshacer. El `interrupt` del grafo garantiza que la operación no se ejecuta sin la aprobación del socio; un prompt que diga «pedí permiso» no lo garantiza, porque el modelo podría ignorarlo. No se resuelve con código propio porque habría que reimplementar la pausa y reanudación del grafo con su persistencia. Las tools de lectura no lo necesitan.
- **UI Streamlit**: permite conversar, renderiza markdown y muestra en streaming el razonamiento y cada tool con argumentos y resultado.
- **Proveedor y modelo (Gemini `gemini-3.5-flash`)**: soporta tool calling y devuelve resúmenes de razonamiento (`include_thoughts=True`), que la UI muestra aparte de la respuesta. Tiene capa gratuita.
- **Montos y fechas**: el valor total del alquiler lo indica el socio (el backend no tiene precios); el modelo no hace cuentas.

## Limitaciones conocidas

- **Privacidad:** en el plan gratuito, Google puede usar los datos enviados a Gemini para mejorar sus modelos. Por eso la demo usa clientes ficticios. El backend no se expone a internet: solo viaja a la nube lo que el agente lee en sus tools.
- **Calidad según el modelo:** el flujo `nuevo` necesita un modelo con *tool calling* confiable. Los modelos gratuitos de OpenRouter pueden equivocar argumentos o ser lentos y tener cupos bajos; Gemini es el modelo con el que se probó la demo. El modelo elegido es global del proceso de Streamlit (pensado para un solo usuario local).
- **Razonamiento en inglés:** los resúmenes de razonamiento de Gemini suelen venir en inglés aunque la respuesta sea en español. Con otros proveedores el razonamiento puede no estar disponible (la UI igualmente muestra cada tool y su resultado).
- **Límites del plan gratuito:** si se supera la cuota, la UI muestra un aviso (429); si el modelo está saturado, otro (503). Se reintenta automáticamente hasta 4 veces antes de avisar.
- **Alcance:** solo están implementados `nuevo`, `resumen` y `cancelar`. Los demás comandos del sistema (cobrar, objetos, flete, fechas, postergar, stock, etc.) todavía no están disponibles.
- **Datos de la tarjeta de confirmación:** muestra el cliente y el domicilio por su id (el título del alquiler sí incluye el nombre).
- **Modo `api`:** `consultar_disponibilidad`, `crear_alquiler` y el listado de alquileres siguen los contratos del backend leídos del código fuente; conviene validarlos contra el backend corriendo antes de la demo.
- El estado del modo `mock` no persiste entre ejecuciones.
