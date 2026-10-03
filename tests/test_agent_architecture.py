from core.brain.agent_framework import AgentOrchestrator, PermissionManager


def test_permission_manager_blocks_dangerous_actions():
    manager = PermissionManager()
    assert manager.evaluate("consultar horario") == "safe"
    assert manager.evaluate("criar arquivo em documentos") == "moderate"
    assert manager.evaluate("apagar arquivos importantes") == "dangerous"
    assert manager.request_confirmation("apagar arquivos importantes") is True


def test_orchestrator_creates_plan_for_complex_work():
    orchestrator = AgentOrchestrator()
    plan = orchestrator.plan("crie um sistema de login com autenticação e testes")

    assert isinstance(plan, list)
    assert plan
    assert any("analisar" in step.lower() for step in plan)
    assert any("implementar" in step.lower() or "implement" in step.lower() for step in plan)


def test_orchestrator_verifies_execution_and_recommends_retry():
    orchestrator = AgentOrchestrator()
    outcome = orchestrator.verify_execution(
        "crie um sistema de login",
        "a autenticação falhou após a última alteração",
        success=False,
    )

    assert outcome["status"] == "needs_retry"
    assert "corrigir" in outcome["next_action"].lower()
    assert "verificar" in outcome["summary"].lower()
