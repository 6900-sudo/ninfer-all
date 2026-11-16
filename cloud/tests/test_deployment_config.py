import os
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent
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
    assert ninfer["environment"]["NINFER_API_KEY"] == (
        "${NINFER_API_KEY:?Set NINFER_API_KEY in .env}"
    )


def test_launcher_accepts_the_ninfer_command_and_passes_the_engine_key(tmp_path):
    # The entrypoint hands everything after `run` to scripts/run.sh, which exits on an argument
    # it does not know. Run that launcher with a stand-in server that prints its arguments.
    command = COMPOSE["services"]["ninfer"]["command"]
    assert command[0] == "run"
    arguments = [
        re.sub(r"\$\{\w+:-([^}]*)\}", r"\1", argument) for argument in command[1:]
    ]
    assert not any("$" in argument for argument in arguments)

    server = tmp_path / "ninfer-serve"
    server.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n")
    server.chmod(0o755)
    model = tmp_path / "model.ninfer"
    model.touch()
    engine_key = "engine-key-for-the-launcher-test"

    result = subprocess.run(
        ["bash", str(REPOSITORY / "scripts" / "run.sh"), *arguments],
        env={
            "PATH": os.environ["PATH"],
            "NINFER_SERVER": str(server),
            "NINFER_MODEL": str(model),
            "NINFER_API_KEY": engine_key,
            "NINFER_HOST": "0.0.0.0",
            "NINFER_PORT": "8080",
            "NINFER_CONTEXT": "32768",
            "NINFER_KV_CAPACITY": "32768",
            "NINFER_CONCURRENCY": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    server_arguments = result.stdout.splitlines()
    assert server_arguments[server_arguments.index("--api-key") + 1] == engine_key
    assert server_arguments.count("--api-key") == 1


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
