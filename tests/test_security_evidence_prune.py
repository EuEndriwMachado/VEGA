# -*- coding: utf-8 -*-
"""Rotação da pasta de evidências: mantém as últimas N fotos."""
import time

from core.security.security import VegaSecurity as SecurityManager


def _make_pictures(tmp_path, count):
    directory = tmp_path / "security_evidence"
    directory.mkdir(exist_ok=True)
    for i in range(count):
        path = directory / f"command_20260101T0000{i:02d}Z.jpg"
        path.write_bytes(b"fake")
        # Ensure distinct mtimes in filesystems with coarse resolution.
        time.sleep(0.01)
    return directory


def test_prune_keeps_latest(tmp_path):
    directory = _make_pictures(tmp_path, 40)
    SecurityManager._prune_evidence(directory, keep=30)
    remaining = sorted(p.name for p in directory.iterdir())
    assert len(remaining) == 30
    # Keep the newest ones (higher index).
    assert remaining[0] == "command_20260101T000010Z.jpg"
    assert remaining[-1] == "command_20260101T000039Z.jpg"


def test_prune_with_fewer_than_limit(tmp_path):
    directory = _make_pictures(tmp_path, 5)
    SecurityManager._prune_evidence(directory, keep=30)
    assert len(list(directory.iterdir())) == 5


def test_prune_default_uses_class_setting(tmp_path):
    directory = _make_pictures(tmp_path, 35)
    SecurityManager._prune_evidence(directory)
    assert len(list(directory.iterdir())) == 30


def test_prune_ignores_non_images(tmp_path):
    directory = tmp_path / "security_evidence"
    directory.mkdir(exist_ok=True)
    for i in range(32):
        (directory / f"command_{i:03d}.jpg").write_bytes(b"x")
    (directory / "audit.json").write_text("{}", encoding="utf-8")
    SecurityManager._prune_evidence(directory)
    assert len(list(directory.iterdir())) == 31  # 30 images + 1 json
