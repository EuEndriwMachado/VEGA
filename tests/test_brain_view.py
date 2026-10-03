from core.brain.brain_view import is_brain_view_request


def test_brain_view_accepts_natural_requests():
    requests = (
        "VEGA, deixa eu ver seu cérebro",
        "mostra a rede neural da Vega",
        "quero visualizar a mente dela",
    )

    assert all(is_brain_view_request(request) for request in requests)


def test_brain_view_does_not_capture_regular_memory_questions():
    assert not is_brain_view_request("quais memórias você tem?")
