import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from core.actions.scheduler import VegaScheduler
from core.security.security import VegaSecurity
from core.memory.memory import VegaMemory
from core.vision.advanced_perception import CameraCalibration, OptionalVisionFeatures


def test_security_pin_round_trip():
    with tempfile.TemporaryDirectory() as directory:
        security = VegaSecurity(Path(directory) / "security.json")
        security.set_pin("1234")
        assert security.has_pin()
        assert security.verify("1234")
        assert not security.verify("0000")


def test_scheduler_persists_reminders():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "scheduler.json"
        scheduler = VegaScheduler(path)
        item = scheduler.add("testar lembrete", datetime.now() + timedelta(hours=1))
        assert scheduler.list()[0]["id"] == item["id"]
        scheduler.stop()


def test_report_is_stored_in_events():
    with tempfile.TemporaryDirectory() as directory:
        memory = VegaMemory(Path(directory) / "memory")
        assert memory.add_report_event({"title": "Relatório de teste", "sections": {"resultado": "ok"}})
        daily_files = list((Path(directory) / "memory" / "daily").glob("*.json"))
        content = "\n".join(path.read_text(encoding="utf-8") for path in daily_files)
        assert '"role": "report"' in content
        assert "Relatório de teste" in content


def test_camera_calibration_and_face_profile():
    with tempfile.TemporaryDirectory() as directory:
        calibration = CameraCalibration(Path(directory) / "calibration.json")
        calibration.save(camera_index=2, brightness=0.6)
        assert calibration.data["camera_index"] == 2
        assert calibration.data["brightness"] == 0.6

        features = OptionalVisionFeatures(Path(directory) / "faces.json")
        features.register_face("perfil", [0.1, 0.2])
        assert features.match_face([0.1, 0.2])["name"] == "perfil"
