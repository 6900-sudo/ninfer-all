from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "compose.yaml").read_text())


def test_only_tls_ports_are_published_to_the_host():
    published = {
        name: service["ports"]
        for name, service in COMPOSE["services"].items()
        if service.get("ports")
    }
    assert published == {
        "caddy": ["443:443", "443:443/udp"],
    }


def test_ninfer_is_private_and_requires_its_own_api_key():
    services = COMPOSE["services"]
    ninfer = services["ninfer"]

    assert set(ninfer["networks"]) == {"private"}
    assert COMPOSE["networks"]["private"]["internal"] is True
    assert "--api-key" in ninfer["command"]
    assert "${NINFER_API_KEY:?Set NINFER_API_KEY in .env}" in ninfer["command"]
    assert ninfer["environment"]["NINFER_API_KEY"] == (
        "${NINFER_API_KEY:?Set NINFER_API_KEY in .env}"
    )


def test_gateway_is_not_published_and_bridges_only_private_and_tls_networks():
    gateway = COMPOSE["services"]["gateway"]

    assert "ports" not in gateway
    assert set(gateway["networks"]) == {"private", "edge"}
    assert "8000" in gateway["expose"]


def test_model_downloader_is_separate_from_the_private_inference_network():
    services = COMPOSE["services"]

    assert set(services["model-download"]["networks"]) == {"edge"}
    assert set(services["ninfer"]["networks"]) == {"private"}
    assert services["model-download"]["restart"] == "no"
