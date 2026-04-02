from proof_of_heat.plugins import whatsminer as whatsminer_module
from proof_of_heat.plugins.whatsminer import Whatsminer


def test_set_power_percent_uses_normal_mode_payload(monkeypatch):
    calls = []

    def fake_call_whatsminer(**kwargs):
        calls.append(kwargs)
        if kwargs["cmd"] == "get.device.info":
            return {"msg": {"salt": "salt-value"}}
        if kwargs["cmd"] == "set.miner.power_percent":
            return {"code": 0, "msg": {"status": "ok"}}
        raise AssertionError(f"Unexpected command: {kwargs}")

    monkeypatch.setattr(whatsminer_module, "call_whatsminer", fake_call_whatsminer)

    miner = Whatsminer(
        host="miner.local",
        port=4433,
        login="user",
        password="secret",
        timeout=10,
    )

    response = miner.set_power_percent(70)

    assert response == {"code": 0, "msg": {"status": "ok"}}
    assert calls[1]["cmd"] == "set.miner.power_percent"
    assert calls[1]["param"] == {"percent": "70", "mode": "normal"}
