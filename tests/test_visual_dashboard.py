from tools.visual_dashboard import build_dashboard_snapshot, format_dashboard_text


def test_dashboard_snapshot_collects_notes_focus_and_reminders(tmp_path):
    daily_support = tmp_path / "daily_support.json"
    scheduler_file = tmp_path / "scheduler.json"

    daily_support.write_text(
        """
        {
          "notes": [
            {"text": "Revisar a introdução"},
            {"text": "Entregar o relatório"}
          ],
          "focus": {
            "goal": "Estudar física",
            "duration_minutes": 30,
            "steps": [
              {"text": "Rever a teoria", "done": false},
              {"text": "Resolver exercícios", "done": true}
            ]
          }
        }
        """,
        encoding="utf-8",
    )
    scheduler_file.write_text(
        """
        [
          {"text": "Entrega de trabalho", "due_at": "2026-10-02T18:00:00+00:00", "kind": "reminder", "active": true},
          {"text": "Revisão de física", "due_at": "2026-10-02T20:00:00+00:00", "kind": "focus", "active": true}
        ]
        """,
        encoding="utf-8",
    )

    snapshot = build_dashboard_snapshot(daily_support, scheduler_file)

    assert snapshot["notes"][0] == "Revisar a introdução"
    assert snapshot["focus"]["goal"] == "Estudar física"
    assert snapshot["reminders"][0]["text"] == "Entrega de trabalho"

    rendered = format_dashboard_text(snapshot)
    assert "VEGA · Agenda do dia" in rendered
    assert "Estudar física" in rendered
    assert "Entrega de trabalho" in rendered
