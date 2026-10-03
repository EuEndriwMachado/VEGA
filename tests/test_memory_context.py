from core.memory.memory import VegaMemory
from core.agent_state import AgentStateManager


def test_permanent_memories_are_available_on_every_turn(tmp_path):
    memory = VegaMemory(base_path=str(tmp_path))
    memory.remember("nome do criador", "Endriw Machado", importance=1.0)

    context = memory.build_memory_context("qual a previsão do tempo?")

    assert "nome do criador" in context
    assert "Endriw Machado" in context


def test_contextual_memory_retrieval_is_query_aware(tmp_path):
    memory = VegaMemory(base_path=str(tmp_path))
    memory.remember("tema favorito", "Física", importance=1.0)
    memory.record_procedure("estudar física", ["rever teoria", "resolver exercícios"], importance=0.9)

    hits = memory.contextual_recall("quero estudar física por 30 minutos", limit=5)

    assert any("física" in item["summary"].lower() or "física" in item["key"].lower() for item in hits)


def test_agent_state_tracks_current_goal_and_session():
    state = AgentStateManager()
    state.observe("quero estudar física e organizar meu dia")

    assert state.current_goal is not None
    assert "fisica" in state.current_goal.lower() or "física" in state.current_goal.lower()
    assert "estudo" in state.focus_context.lower() or "study" in state.focus_context.lower()


def test_agent_state_builds_execution_plan_for_complex_goals():
    state = AgentStateManager()
    state.observe("crie um sistema de login com autenticação e testes")

    plan = state.plan_current_goal()

    assert isinstance(plan, list)
    assert plan
    assert any("analisar" in step.lower() for step in plan)
    assert any("implement" in step.lower() or "implementar" in step.lower() for step in plan)


def test_agent_state_records_learning_from_failures_and_reuses_strategy():
    state = AgentStateManager()
    state.observe("crie um sistema de login")

    state.record_learning(
        "crie um sistema de login",
        success=False,
        evidence="a autenticação falhou após a última alteração",
        strategy="revisar fluxo de autenticação e validar novamente",
    )

    summary = state.get_adaptation_summary("crie um sistema de login")

    assert summary
    assert any("revisar" in item.lower() or "autentica" in item.lower() for item in summary)
