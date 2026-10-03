from core.brain.brain import VegaBrain


def test_que_horas_sao():
    brain = VegaBrain()
    response = brain.process("que horas são")
    assert "horas" in response.lower() or "hora" in response.lower()
    assert "context" not in response.lower()


def test_que_dia_e_hoje():
    brain = VegaBrain()
    response = brain.process("que dia é hoje")
    assert "hoje" in response.lower() or "dia" in response.lower()
    assert "context" not in response.lower()
