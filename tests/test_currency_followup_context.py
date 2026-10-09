from unittest.mock import patch
from core.brain.brain import VegaBrain

def test_why_followup_after_currency_quote_keeps_quote_and_context():
    brain = VegaBrain()
    tool = brain.tool_manager.get_tool("currency")
    quote = {
        "source": "awesomeapi",
        "base": "USD",
        "target": "BRL",
        "amount": 1.0,
        "rate": 4.97,
        "converted": 4.97,
        "high": 5.00,
        "low": 4.95,
        "pct_change": -0.48,
    }

    with patch.object(tool, "implementation", return_value=quote):
        answer = brain._process_tool_request("Como tá a cotação do dólar?")

    assert "4,97" in answer
    assert brain.conversation.get_messages()[-1]["content"] == answer
    assert brain.orchestrator.immediate[-1]["content"] == answer
    assert brain.orchestrator.task.name == "currency_quote"
    assert brain.orchestrator.task.state == "done"

    assert brain.orchestrator.classify_turn_intent("E por que isso?") == "continuation"


def test_standalone_why_question_is_classified_as_conversation_followup():
    from core.orchestration.conversation import ConversationOrchestrator
    assert ConversationOrchestrator().classify_turn_intent("Por que isso?") == "continuation"
