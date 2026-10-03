import threading
from datetime import datetime, timedelta
from unittest.mock import Mock

from core.brain.brain import VegaBrain
from core.actions.daily_support import VegaDailySupport


def _brain_for_daily_stream(support, scheduler, ai):
    brain = VegaBrain.__new__(VegaBrain)
    brain._process_stream_lock = threading.RLock()
    brain._response_cancelled = threading.Event()
    brain.daily_support = support
    brain.scheduler = scheduler
    brain.ai = ai
    brain.last_screen_summary = None
    brain.memory = Mock()
    brain.conversation = Mock()
    brain._process_security_request = lambda _text: None
    brain._process_pending_security_response = lambda _text: None
    brain._is_holographic_environment_request = lambda _text: False
    brain._process_holographic_interaction_command = lambda _text: None
    brain._is_visual_analysis_request = lambda _text: False
    brain._is_database_view_request = lambda _text: False
    brain._answer_time_or_date_question = lambda _text: None
    brain._process_scheduler_request = lambda _text: None
    brain._process_mode = lambda _text: None
    brain._process_command = lambda _text: None
    brain._process_tool_request = lambda _text: None
    return brain


def test_notes_persist_and_list_newest_first(tmp_path):
    path = tmp_path / "daily_support.json"
    support = VegaDailySupport(path)
    support.add_note("  Entregar o trabalho de física  ")
    support.add_note("Revisar a introdução")

    restored = VegaDailySupport(path)

    assert [note["text"] for note in restored.list_notes()] == [
        "Revisar a introdução",
        "Entregar o trabalho de física",
    ]


def test_focus_session_tracks_steps_and_survives_reload(tmp_path):
    path = tmp_path / "daily_support.json"
    support = VegaDailySupport(path)
    support.start_focus(
        "Estudar equações",
        ["Rever o exemplo", "Resolver os exercícios"],
        duration_minutes=30,
        reminder_id="focus-reminder",
    )

    restored = VegaDailySupport(path)
    completed = restored.complete_next_focus_step()

    assert completed[0] == 0
    assert completed[1]["done"] is True
    assert restored.current_focus()["steps"][1]["done"] is False
    assert restored.finish_focus()["goal"] == "Estudar equações"
    assert restored.current_focus() is None


def test_quick_note_command_preserves_original_text_and_lists_notes(tmp_path):
    brain = VegaBrain.__new__(VegaBrain)
    brain.daily_support = VegaDailySupport(tmp_path / "daily_support.json")

    response = brain._process_daily_support_request("Anota que Entregar o Trabalho de Física")

    assert response == "Anotei: Entregar o Trabalho de Física"
    listed = brain._process_daily_support_request("minhas notas")
    assert "Entregar o Trabalho de Física" in listed


def test_daily_overview_includes_due_reminders_and_todays_notes(tmp_path):
    brain = VegaBrain.__new__(VegaBrain)
    brain.daily_support = VegaDailySupport(tmp_path / "daily_support.json")
    brain.daily_support.add_note("Revisar a introdução")

    class FakeScheduler:
        @staticmethod
        def list():
            return [{
                "text": "Entregar o relatório",
                "due_at": (datetime.now().astimezone() + timedelta(hours=2)).isoformat(),
            }]

    brain.scheduler = FakeScheduler()
    summary = brain._process_daily_support_request("organiza meu dia")

    assert "Entregar o relatório" in summary
    assert "Revisar a introdução" in summary


def test_focus_session_command_generates_steps_and_can_progress(tmp_path):
    brain = VegaBrain.__new__(VegaBrain)
    brain.daily_support = VegaDailySupport(tmp_path / "daily_support.json")

    class FakeAI:
        @staticmethod
        def chat(*_args, **_kwargs):
            return "1. Rever a teoria\n2. Resolver exercícios\n3. Conferir as respostas"

    class FakeScheduler:
        def __init__(self):
            self.items = []
            self.cancelled = []

        def add_in(self, text, minutes, kind="reminder"):
            item = {"id": "focus_timer", "text": text, "minutes": minutes, "kind": kind}
            self.items.append(item)
            return item

        def list(self):
            return self.items

        def cancel(self, item_id):
            self.cancelled.append(item_id)
            return True

    brain.ai = FakeAI()
    brain.scheduler = FakeScheduler()

    response = brain._process_daily_support_request(
        "começar uma sessão de estudo sobre Física por 30 minutos"
    )

    assert "Sessão de foco iniciada por 30 minutos" in response
    assert brain.daily_support.current_focus()["goal"] == "Física"
    assert brain.scheduler.items[0]["kind"] == "focus"
    assert "Rever a teoria" in brain._process_daily_support_request("terminei a etapa")
    assert brain._process_daily_support_request("encerrar sessão de foco") == "Sessão encerrada. Salvei seu progresso."
    assert brain.scheduler.cancelled == ["focus_timer"]


def test_daily_support_commands_work_through_voice_stream(tmp_path):
    support = VegaDailySupport(tmp_path / "daily_support.json")

    class FakeAI:
        @staticmethod
        def chat(*_args, **_kwargs):
            return "1. Rever a teoria\n2. Resolver exercícios\n3. Revisar respostas"

    class FakeScheduler:
        def __init__(self):
            self.items = []
            self.cancelled = []

        def add_in(self, text, minutes, kind="reminder"):
            item = {"id": "focus_timer", "text": text, "minutes": minutes, "kind": kind}
            self.items.append(item)
            return item

        def list(self):
            return self.items

        def cancel(self, item_id):
            self.cancelled.append(item_id)
            return True

    scheduler = FakeScheduler()
    brain = _brain_for_daily_stream(support, scheduler, FakeAI())

    note_response = list(brain.process_stream("anota que Entregar o trabalho de Física"))
    focus_response = list(brain.process_stream("começar uma sessão de estudo sobre Equações por 30 minutos"))
    overview_response = list(brain.process_stream("organiza meu dia"))

    assert "Entregar o trabalho de Física" in note_response[0]
    assert "Sessão de foco iniciada" in focus_response[0]
    assert "Equações" in overview_response[0]
    assert "Próxima etapa" in overview_response[0]

    completed = list(brain.process_stream("terminei a etapa"))
    assert "Etapa concluída" in completed[0]
    finished = list(brain.process_stream("encerrar sessão de foco"))
    assert "Sessão encerrada" in finished[0]
    assert scheduler.cancelled == ["focus_timer"]