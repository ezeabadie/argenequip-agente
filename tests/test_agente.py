"""Confirmación humana y streaming de eventos, con un modelo falso (no usa la API de Gemini)."""
import sqlite3

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

CREAR = {"cliente_id": 1, "domicilio_id": 11, "titulo": "Obra", "fecha_inicio": "2030-01-24",
         "fecha_fin": "2030-01-28", "monto_total": 1000, "equipos": [{"herramienta_id": 4, "cantidad": 1}]}


class FakeModel(GenericFakeChatModel):
    def bind_tools(self, *args, **kwargs):   # create_agent lo llama; el guion ya trae los tool_calls
        return self


@pytest.fixture
def agente_mod(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "clave-falsa")   # agente.py arma el modelo real al importarse
    import agente
    return agente


@pytest.fixture
def armar(agente_mod, cliente):
    """Devuelve una función que arma el agente con un guion de respuestas y lo deja como `agente.agent`."""
    def _armar(*mensajes):
        saver = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
        agente_mod.agent = agente_mod.build_agent(FakeModel(messages=iter(mensajes)), saver)
        return agente_mod, {"configurable": {"thread_id": "t"}}
    return _armar


def llamada_crear():
    return AIMessage(content="", tool_calls=[{"name": "crear_alquiler", "id": "c1", "args": CREAR}])


def test_crear_alquiler_se_pausa_y_no_escribe_hasta_aprobar(armar, cliente):
    ag, cfg = armar(llamada_crear(), AIMessage(content="Listo, creado."))
    evs = list(ag.stream_turn("nuevo", cfg))
    assert evs[-1]["tipo"] == "aprobacion" and evs[-1]["acciones"][0]["name"] == "crear_alquiler"
    assert ag.pending_approval(cfg) is not None
    assert len(cliente.alquileres) == 1                      # solo el de la semilla: todavía no se escribió nada

    evs = list(ag.stream_turn(ag.decision(True), cfg))
    assert [e["tipo"] for e in evs] == ["resultado", "texto"]
    assert "Alquiler creado: #2" in evs[0]["texto"]
    assert len(cliente.alquileres) == 2 and ag.pending_approval(cfg) is None


def test_rechazar_no_escribe_y_el_agente_responde(armar, cliente):
    ag, cfg = armar(llamada_crear(), AIMessage(content="Entendido, ¿qué querés cambiar?"))
    list(ag.stream_turn("nuevo", cfg))
    evs = list(ag.stream_turn(ag.decision(False), cfg))
    assert evs[0]["tipo"] == "resultado" and evs[0]["estado"] == "error"   # el rechazo llega como ToolMessage de error
    assert evs[-1]["tipo"] == "texto"
    assert len(cliente.alquileres) == 1


def test_las_tools_de_lectura_no_piden_confirmacion(armar):
    lectura = AIMessage(content="", tool_calls=[{"name": "resumen_alquileres", "id": "r1", "args": {}}])
    ag, cfg = armar(lectura, AIMessage(content="Acá está."))
    evs = list(ag.stream_turn("resumen", cfg))
    assert [e["tipo"] for e in evs] == ["llamada", "resultado", "texto"]
    assert ag.pending_approval(cfg) is None


def test_memoria_conserva_turnos_y_se_reconstruye(armar):
    ag, cfg = armar(AIMessage(content="Hola, soy el asistente."), AIMessage(content="Tu mensaje anterior fue: hola."))
    list(ag.stream_turn("hola", cfg))
    list(ag.stream_turn("¿qué dije antes?", cfg))
    turnos = ag.saved_turns(cfg)
    assert [t[0] for t in turnos] == ["hola", "¿qué dije antes?"]
    assert turnos[1][1][0]["texto"].startswith("Tu mensaje anterior")
    # El segundo turno recibió el primero en su historial (eso es lo que hace el checkpointer).
    assert len(ag.saved_state(cfg)["messages"]) == 4


def _sin_respuesta(mensajes):
    """Ids de llamadas a tools que no tienen su resultado (los proveedores de modelos las rechazan)."""
    resueltas = {m.tool_call_id for m in mensajes if m.type == "tool"}
    return [c["id"] for m in mensajes if m.type == "ai" for c in m.tool_calls if c["id"] not in resueltas]


def _llamada_resumen():
    return AIMessage(content="", tool_calls=[{"name": "resumen_alquileres", "id": "r1", "args": {}}])


@pytest.mark.parametrize("corte, guion", [
    ("texto", [AIMessage(content="Hola!"), AIMessage(content="Respuesta que se cancela"), AIMessage(content="B")]),
    ("llamada", [AIMessage(content="Hola!"), _llamada_resumen(), AIMessage(content="B")]),
    ("resultado", [AIMessage(content="Hola!"), _llamada_resumen(), AIMessage(content="fin"), AIMessage(content="B")]),
], ids=["tras_la_respuesta", "tras_la_llamada_a_la_tool", "tras_el_resultado_de_la_tool"])
def test_cancelar_un_turno_a_medias_deja_seguir_la_conversacion(armar, corte, guion):
    """El socio toca «Detener» y Streamlit corta el turno entre dos pasos: se descarta entero y se puede seguir."""
    ag, cfg = armar(*guion)
    list(ag.stream_turn("hola", cfg))
    antes = ag.message_count(cfg)

    turno = ag.stream_turn("pedido que se cancela", cfg)
    for e in turno:
        if e["tipo"] == corte:
            break                                   # la interfaz abandona el turno acá (no cierra el generador)
    ag.discard_turn(cfg, antes)

    mensajes = ag.saved_state(cfg)["messages"]
    assert len(mensajes) == antes and _sin_respuesta(mensajes) == []
    assert "pedido que se cancela" not in [m.text for m in mensajes]

    list(ag.stream_turn("pedido nuevo", cfg))      # la conversación sigue funcionando
    mensajes = ag.saved_state(cfg)["messages"]
    assert [m.type for m in mensajes] == ["human", "ai", "human", "ai"] and _sin_respuesta(mensajes) == []
    assert mensajes[2].text == "pedido nuevo"


def _turno_a_medias(armar):
    """Una conversación con un turno interrumpido justo después de que el modelo pidió una tool."""
    ag, cfg = armar(AIMessage(content="Hola!"), _llamada_resumen(), AIMessage(content="B"))
    list(ag.stream_turn("hola", cfg))
    turno = ag.TurnoEnCurso(cfg)
    for e in ag.stream_turn("pedido", cfg):
        if e["tipo"] == "llamada":
            break
    assert ag.message_count(cfg) == turno.mensajes_antes + 2     # el pedido y la llamada a la tool
    return ag, cfg, turno


def test_cancelar_antes_de_que_termine_el_script_viejo_lo_descarta_el_viejo(armar):
    """Caso normal: el script nuevo marca la cancelación al instante y el viejo (que sigue en el paso del modelo) descarta."""
    ag, cfg, turno = _turno_a_medias(armar)
    turno.cancelar()
    assert not turno.hecho.is_set() and ag.message_count(cfg) == turno.mensajes_antes + 2   # todavía nada
    turno.terminar()                                                                       # el viejo termina su paso
    assert turno.hecho.is_set() and ag.message_count(cfg) == turno.mensajes_antes


def test_cancelar_cuando_el_script_viejo_ya_termino_descarta_el_nuevo(armar):
    ag, cfg, turno = _turno_a_medias(armar)
    turno.terminar()                                                                       # el viejo ya se había detenido
    assert ag.message_count(cfg) == turno.mensajes_antes + 2                               # sin cancelar no se toca nada
    turno.cancelar()
    assert turno.hecho.is_set() and ag.message_count(cfg) == turno.mensajes_antes


def test_turno_que_termina_normalmente_no_se_descarta(armar):
    ag, cfg = armar(AIMessage(content="Hola!"))
    turno = ag.TurnoEnCurso(cfg)
    list(ag.stream_turn("hola", cfg))
    turno.terminar()
    assert turno.hecho.is_set() and ag.message_count(cfg) == 2


def test_historial_limitado_oculta_el_turno_que_se_esta_descartando(armar):
    ag, cfg, turno = _turno_a_medias(armar)
    assert [p for p, _ in ag.saved_turns(cfg)] == ["hola", "pedido"]
    assert [p for p, _ in ag.saved_turns(cfg, hasta=turno.mensajes_antes)] == ["hola"]


def test_una_llamada_a_tool_sin_resultado_se_repara_y_la_conversacion_sigue(armar):
    """Un turno cortado justo después de que el modelo pidió una tool no debe inutilizar la conversación."""
    ag, cfg = armar(AIMessage(content="Hola!"), _llamada_resumen(), AIMessage(content="Seguimos."))
    list(ag.stream_turn("hola", cfg))
    for e in ag.stream_turn("pedido", cfg):
        if e["tipo"] == "llamada":
            break                                                     # se corta: la tool nunca se ejecutó
    assert _sin_respuesta(ag.saved_state(cfg)["messages"]) == ["r1"]  # estado roto: los proveedores lo rechazarían

    list(ag.stream_turn("seguimos", cfg))                             # el siguiente mensaje lo repara
    mensajes = ag.saved_state(cfg)["messages"]
    assert _sin_respuesta(mensajes) == []
    assert [m.type for m in mensajes] == ["human", "ai", "human", "ai", "tool", "human", "ai"]
    assert mensajes[4].content.startswith("ERROR: la operación se interrumpió")   # la tool NO se volvió a ejecutar
    assert mensajes[-1].text == "Seguimos."


def test_reparar_no_toca_conversaciones_sanas_ni_confirmaciones_pendientes(armar):
    ag, cfg = armar(AIMessage(content="Hola!"), llamada_crear())
    list(ag.stream_turn("hola", cfg))
    assert ag.repair_dangling_call(cfg) is False                      # conversación sana
    list(ag.stream_turn("creá el alquiler", cfg))                     # queda pausada esperando confirmación
    assert ag.pending_approval(cfg) is not None
    antes = ag.message_count(cfg)
    assert ag.repair_dangling_call(cfg) is False and ag.message_count(cfg) == antes   # pendiente != colgada


def test_descartar_sin_cambios_no_toca_nada(armar):
    ag, cfg = armar(AIMessage(content="Hola!"))
    list(ag.stream_turn("hola", cfg))
    antes = ag.message_count(cfg)
    ag.discard_turn(cfg, antes)                      # nada que descartar
    assert ag.message_count(cfg) == antes


def test_cambiar_de_modelo_reconstruye_el_agente(agente_mod, monkeypatch):
    monkeypatch.setattr(agente_mod, "agent", agente_mod.agent)       # se restauran al terminar el test
    monkeypatch.setattr(agente_mod, "_modelo", agente_mod._modelo)
    monkeypatch.setattr(agente_mod, "_rotador", agente_mod._rotador)
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-falsa")
    antes = agente_mod.agent
    agente_mod.usar_modelo("openrouter", "proveedor/modelo:free")
    assert agente_mod.modelo_actual() == ("openrouter", "proveedor/modelo:free")
    assert agente_mod.agent is not antes
    despues = agente_mod.agent
    agente_mod.usar_modelo("openrouter", "proveedor/modelo:free")   # mismo modelo: no se reconstruye
    assert agente_mod.agent is despues


def test_cambiar_de_modelo_sin_clave_falla_y_conserva_el_anterior(agente_mod, monkeypatch):
    monkeypatch.setattr(agente_mod, "agent", agente_mod.agent)
    monkeypatch.setattr(agente_mod, "_modelo", agente_mod._modelo)
    monkeypatch.setattr(agente_mod, "_rotador", agente_mod._rotador)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    antes, modelo = agente_mod.agent, agente_mod.modelo_actual()
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        agente_mod.usar_modelo("openrouter", "proveedor/modelo:free")
    assert agente_mod.agent is antes and agente_mod.modelo_actual() == modelo


def test_proveedor_invalido(monkeypatch):
    import model
    with pytest.raises(RuntimeError, match="inválido"):
        model.get_model("inexistente", "x")


def test_es_limite_de_cuota(agente_mod):
    assert agente_mod.es_limite_de_cuota(Exception("429 RESOURCE_EXHAUSTED: quota"))
    assert not agente_mod.es_limite_de_cuota(Exception("503 UNAVAILABLE"))


GEMINI_DIARIO = ("Error calling model 'gemini-3.5-flash' (RESOURCE_EXHAUSTED): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
                 "'message': 'You exceeded your current quota. * Quota exceeded for metric: generate_content_free_tier_requests, "
                 "limit: 20, description: Generate content requests per day limit exceeded on Free Tier.\\nPlease retry in "
                 "12h39m48.172705921s.', 'status': 'RESOURCE_EXHAUSTED'}}")


class TooManyRequestsResponseError(Exception):   # el nombre es lo que lanza el SDK de OpenRouter
    pass


def test_cuota_diaria_de_gemini_explica_el_limite_y_cuando_se_libera(agente_mod):
    tipo, titulo, detalle = agente_mod.explicar_error(Exception(GEMINI_DIARIO))
    assert tipo == "cuota" and titulo == "Se agotó el uso del modelo"
    assert "DIARIO" in detalle and "12 h 39 min" in detalle and "barra lateral" in detalle


def test_cuota_por_minuto_y_openrouter(agente_mod):
    _, _, detalle = agente_mod.explicar_error(Exception("429 RESOURCE_EXHAUSTED. Please retry in 34.5s."))
    assert "por minuto" in detalle and "35 s" in detalle and "DIARIO" not in detalle
    # OpenRouter no dice «429» en el texto, pero el nombre del error lo delata
    assert agente_mod.clasificar_error(TooManyRequestsResponseError("Provider returned error")) == "cuota"


def test_otros_errores_del_modelo_se_clasifican(agente_mod):
    c = agente_mod.clasificar_error
    assert c(Exception("503 UNAVAILABLE: model overloaded")) == "saturado"
    assert c(Exception("400 INVALID_ARGUMENT: API key not valid")) == "clave"
    assert c(Exception("402 Payment Required: insufficient credits")) == "credito"
    tipo, titulo, detalle = agente_mod.explicar_error(ValueError("algo raro"))
    assert (tipo, titulo) == ("otro", "El agente falló") and "algo raro" in detalle


# --- Varias claves del mismo proveedor ----------------------------------------------

LLAMADAS = {"sin_cupo": 0, "roto": 0}


class SinCupo(FakeModel):
    """Una clave que ya gastó su cupo."""
    def _generate(self, *args, **kwargs):
        LLAMADAS["sin_cupo"] += 1
        raise Exception("429 RESOURCE_EXHAUSTED: quota exceeded")


class Roto(FakeModel):
    """Falla por algo que NO es de cupo."""
    def _generate(self, *args, **kwargs):
        LLAMADAS["roto"] += 1
        raise ValueError("boom")


def _agente_con_claves(agente_mod, *modelos):
    from model import RotarClaves
    saver = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    rotador = RotarClaves(list(modelos))
    agente_mod.agent = agente_mod.build_agent(modelos[0], saver, rotador)
    return rotador, {"configurable": {"thread_id": "t"}}


def test_claves_separadas_por_coma(monkeypatch):
    import model
    monkeypatch.setenv("GOOGLE_API_KEY", " clave-a , clave-b\n,clave-c,")
    assert model.claves("google_genai") == ["clave-a", "clave-b", "clave-c"]
    assert len(model.get_models("google_genai")) == 3
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    assert model.claves("google_genai") == [] and "GOOGLE_API_KEY" in model.falta_clave("google_genai")


def test_cada_modelo_usa_su_propia_clave(monkeypatch):
    import model
    monkeypatch.setenv("GOOGLE_API_KEY", "clave-a,clave-b")
    usadas = [m.google_api_key.get_secret_value() for m in model.get_models("google_genai")]
    assert usadas == ["clave-a", "clave-b"]
    monkeypatch.setenv("GROQ_API_KEY", "gsk-a, gsk-b")
    groq = model.get_models("groq")
    assert [m.groq_api_key.get_secret_value() for m in groq] == ["gsk-a", "gsk-b"]
    assert groq[0].model_name == model.modelo_por_defecto("groq")


def test_groq_aparece_en_el_selector_y_pide_su_clave(monkeypatch):
    import model
    assert "groq" in model.PROVEEDORES
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert "GROQ_API_KEY" in model.falta_clave("groq")


def test_espera_sugerida_de_groq(agente_mod):
    _, _, detalle = agente_mod.explicar_error(Exception(
        "429 rate_limit_exceeded: Rate limit reached for tokens per day (TPD). Please try again in 7m12.5s."))
    assert "DIARIO" in detalle and "8 min" not in detalle and "7 min" in detalle


def test_pasa_a_la_siguiente_clave_cuando_se_agota_el_cupo(agente_mod, monkeypatch):
    monkeypatch.setattr(agente_mod, "agent", agente_mod.agent)
    LLAMADAS.update(sin_cupo=0, roto=0)
    rotador, cfg = _agente_con_claves(agente_mod, SinCupo(messages=iter([])),
                                      FakeModel(messages=iter([AIMessage(content="Respondió la 2da"),
                                                               AIMessage(content="Otra vez la 2da")])))
    evs = list(agente_mod.stream_turn("hola", cfg))
    assert evs[-1]["texto"] == "Respondió la 2da" and rotador.actual == 1
    list(agente_mod.stream_turn("de nuevo", cfg))
    assert LLAMADAS["sin_cupo"] == 1          # recuerda cuál anda: no vuelve a probar la agotada


def test_si_se_agotan_todas_las_claves_falla_con_error_de_cuota(agente_mod, monkeypatch):
    monkeypatch.setattr(agente_mod, "agent", agente_mod.agent)
    monkeypatch.setattr(agente_mod, "_rotador", agente_mod._rotador)
    LLAMADAS.update(sin_cupo=0, roto=0)
    rotador, cfg = _agente_con_claves(agente_mod, SinCupo(messages=iter([])), SinCupo(messages=iter([])))
    agente_mod._rotador = rotador
    with pytest.raises(Exception) as e:
        list(agente_mod.stream_turn("hola", cfg))
    assert agente_mod.es_limite_de_cuota(e.value) and LLAMADAS["sin_cupo"] == 2   # probó las dos
    assert "Se probaron las 2 claves" in agente_mod.explicar_error(e.value)[2]


def test_no_rota_ante_errores_que_no_son_de_cupo(agente_mod, monkeypatch):
    monkeypatch.setattr(agente_mod, "agent", agente_mod.agent)
    LLAMADAS.update(sin_cupo=0, roto=0)
    rotador, cfg = _agente_con_claves(agente_mod, Roto(messages=iter([])), FakeModel(messages=iter([AIMessage(content="x")])))
    with pytest.raises(ValueError):
        list(agente_mod.stream_turn("hola", cfg))
    assert rotador.actual == 0 and LLAMADAS["roto"] == 1                         # no pasó a la siguiente
