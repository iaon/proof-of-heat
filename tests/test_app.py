from fastapi.testclient import TestClient

from proof_of_heat.config import AppConfig
from proof_of_heat import main


class DummyMiner:
    def __init__(self, *args, **kwargs):
        self.name = "dummy"

    def fetch_status(self):
        return {"power": 1000, "fan_speed": 60}

    def set_power_limit(self, watts: int):
        return {"power_limit": watts}

    def start(self):
        return {"status": "started"}

    def stop(self):
        return {"status": "stopped"}


def build_app(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(main, "Whatsminer", DummyMiner)
    config = AppConfig(data_dir=tmp_path)
    app = main.create_app(config)
    return TestClient(app)


def test_health(tmp_path, monkeypatch):
    client = build_app(tmp_path, monkeypatch)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ui_served(tmp_path, monkeypatch):
    client = build_app(tmp_path, monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "proof-of-heat MVP" in resp.text


def test_status_snapshot(tmp_path, monkeypatch):
    client = build_app(tmp_path, monkeypatch)
    resp = client.get("/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["mode"] == "comfort"
    assert payload["target_temperature_c"]
    assert payload["latest_snapshot"]["miner_status"]["power"] == 1000
