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


def test_cambiar_de_modelo_reconstruye_el_agente(agente_mod, monkeypatch):
    monkeypatch.setattr(agente_mod, "agent", agente_mod.agent)       # se restauran al terminar el test
    monkeypatch.setattr(agente_mod, "_modelo", agente_mod._modelo)
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
