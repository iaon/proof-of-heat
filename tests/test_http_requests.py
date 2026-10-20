import json

import httpx
import pytest

from proof_of_heat.services import http_client, market_data, weather
from proof_of_heat.services.device_polling import DevicePoller


@pytest.fixture
def capture_requests(monkeypatch):
    real_client = httpx.Client
    requests = []

    def install(response):
        def handle(request):
            requests.append(request)
            return response

        def create_client(**kwargs):
            return real_client(transport=httpx.MockTransport(handle), **kwargs)

        monkeypatch.setattr(http_client.httpx, "Client", create_client)
        return requests

    return install


@pytest.mark.parametrize(
    "fetch, kwargs, url, params, provider",
    [
        (
            weather.fetch_open_meteo_weather,
            {"latitude": 60.205988, "longitude": 30.039666, "timezone": "Europe/Moscow"},
            weather.OPEN_METEO_URL,
            {"latitude": "60.205988", "longitude": "30.039666", "current_weather": "true", "timezone": "Europe/Moscow"},
            "open_meteo",
        ),
        (
            weather.fetch_met_no_weather,
            {"latitude": 60.205988, "longitude": 30.039666, "altitude_m": 20},
            weather.MET_NO_URL,
            {"lat": "60.205988", "lon": "30.039666", "altitude": "20"},
            "met_no",
        ),
        (
            market_data.fetch_mempool_prices,
            {},
            f"{market_data.MEMPOOL_API_URL}/v1/prices",
            {},
            "mempool_space",
        ),
        (
            market_data.fetch_mempool_hashrate,
            {"time_period": "3m"},
            f"{market_data.MEMPOOL_API_URL}/v1/mining/hashrate/3m",
            {},
            "mempool_space",
        ),
        (
            market_data.fetch_mempool_reward_stats,
            {"block_count": 10},
            f"{market_data.MEMPOOL_API_URL}/v1/mining/reward-stats/10",
            {},
            "mempool_space",
        ),
        (
            market_data.fetch_cbr_daily_rates,
            {},
            market_data.CBR_DAILY_RATES_URL,
            {},
            "cbr",
        ),
    ],
)
def test_api_requests_identify_application(capture_requests, fetch, kwargs, url, params, provider):
    response = (
        httpx.Response(200, text='<ValCurs Date="21.10.2026"/>')
        if provider == "cbr"
        else httpx.Response(200, json={})
    )
    requests = capture_requests(response)

    result = fetch(timeout_s=3.5, **kwargs)

    assert result["provider"] == provider
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url.copy_with(query=None)) == url
    assert dict(request.url.params) == params
    assert request.headers["User-Agent"] == http_client.USER_AGENT
    assert "https://github.com/iaon/proof-of-heat" in request.headers["User-Agent"]
    assert "example.com" not in request.headers["User-Agent"]
    assert request.extensions["timeout"]["read"] == 3.5


def test_zont_request_preserves_identity_auth_and_headers(capture_requests, tmp_path):
    requests = capture_requests(httpx.Response(200, json={
        "ok": True,
        "devices": [{"serial": "SN-TEST", "temp_out": 3.2}],
    }))
    settings = {
        "integrations": {"zont_api": [{
            "id": 1,
            "headers": {"X-ZONT-Client": "test@example.com", "X-Custom": "custom"},
            "login": "login",
            "password": "password",
        }]},
    }
    poller = DevicePoller(settings, data_dir=tmp_path)

    result = poller.poll_zont_device({
        "integration_id": 1, "device_id": 12000, "serial": "SN-TEST", "timeout_s": 3.5,
    })

    assert result["provider"] == "zont"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://my.zont.online/api/devices"
    assert request.headers["User-Agent"] == http_client.USER_AGENT
    assert request.headers["X-ZONT-Client"] == "test@example.com"
    assert request.headers["X-Custom"] == "custom"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Authorization"] == "Basic bG9naW46cGFzc3dvcmQ="
    assert json.loads(request.content) == {"load_io": True}
    assert request.extensions["timeout"]["read"] == 3.5
