"""Air-gapped edition: the offline provider guard and airgap-check."""

import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zanii_evolve import airgap as AG  # noqa: E402
from zanii_evolve import providers as P  # noqa: E402
from zanii_evolve.products import Ctx  # noqa: E402

ENVS = ("ZANII_EVOLVE_OFFLINE", "ZANII_EVOLVE_OFFLINE_ALLOW", "ZANII_EVOLVE_OLLAMA_BASE_URL",
        "ZANII_EVOLVE_OPENAI_BASE_URL", "ZANII_LEDGER_URL", "ZANII_EVOLVE_REGISTRY",
        "ZANII_EVOLVE_VERTEX_PROJECTS", "ZANII_EVOLVE_POLICY_MODEL", "ZANII_EVOLVE_JUDGE_MODEL",
        "ZANII_EVOLVE_SEARCH_MODEL")


@pytest.fixture
def clean(monkeypatch):
    for k in set(ENVS) | set(AG.url_envs()):     # this machine's own URL env too
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(P, "PROVIDERS", {})
    return monkeypatch


@pytest.mark.parametrize("url,ok", [
    ("http://localhost:11434/v1", True), ("http://127.0.0.1:8000", True),
    ("http://10.2.3.4/v1", True), ("http://[::1]:11434", True), ("http://gpu.localhost", True),
    ("https://api.openai.com/v1", False), ("http://8.8.8.8", False), ("not a url", False),
])
def test_local_url(clean, url, ok):
    assert P.local_url(url) is ok


def test_offline_refuses_cloud_providers(clean):
    clean.setenv("ZANII_EVOLVE_OFFLINE", "1")
    for model in ("claude-opus-5-5", "anthropic/claude-opus-5-5", "openai/gpt-5"):
        with pytest.raises(P.ProviderError, match="ZANII_EVOLVE_OFFLINE"):
            P.resolve(model)
    assert P.resolve("ollama/llama4")[1] == "llama4"
    clean.setenv("ZANII_EVOLVE_OLLAMA_BASE_URL", "https://ollama.example.com/v1")
    clean.setattr(P, "PROVIDERS", {})
    with pytest.raises(P.ProviderError, match="not a local host"):
        P.resolve("ollama/llama4")


def test_allow_list_admits_an_on_prem_openai_server(clean):
    clean.setenv("ZANII_EVOLVE_OFFLINE", "1")
    clean.setenv("ZANII_EVOLVE_OFFLINE_ALLOW", "openai,vllm.corp")
    clean.setenv("ZANII_EVOLVE_OPENAI_BASE_URL", "http://vllm.corp:8000/v1")
    assert P.resolve("openai/qwen3")[1] == "qwen3"
    clean.delenv("ZANII_EVOLVE_OFFLINE")
    assert P.resolve("anthropic/claude-opus-5-5")[1] == "claude-opus-5-5"   # guard off


def _run(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain")
    ap.add_argument("--runs", default="runs")
    AG.cli(ap.add_subparsers(dest="cmd"))
    args = ap.parse_args(argv)
    return args.product(args, Ctx(args, ROOT))


def test_airgap_check_exit_codes(clean, tmp_path, capsys):
    assert _run(["airgap-check"]) == 1                          # guard off, cloud defaults
    assert "ZANII_EVOLVE_OFFLINE is not set" in capsys.readouterr().out
    clean.setenv("ZANII_EVOLVE_OFFLINE", "1")
    for k in ("POLICY", "JUDGE", "SEARCH"):
        clean.setenv(f"ZANII_EVOLVE_{k}_MODEL", "ollama/llama4")
    assert _run(["airgap-check"]) == 0
    assert _run(["airgap-check", "--url", "https://blackbox.example.com"]) == 1
    clean.setenv("ZANII_LEDGER_URL", "http://10.0.0.5:9000")
    out = tmp_path / "a.json"
    assert _run(["airgap-check", "--url", "http://127.0.0.1:8787", "--sign", "--out",
                 str(out)]) == 0
    from zanii_evolve import statement as ST
    assert ST.verify(ST.load(out), kind="airgap", allow_unsigned=True)[0]
    assert ST.load(out)["body"]["ok"] is True


@pytest.mark.parametrize("url,ok", [
    ("http://[::ffff:8.8.8.8]/", False),         # IPv4-mapped public address
    ("http://[::ffff:127.0.0.1]/", True),
    ("http://[2002:0808:0808::1]/", False),      # 6to4 of 8.8.8.8
    ("http://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/", False),   # Teredo
    ("http://0.0.0.0:11434/", False),
    ("http://[::]/", False),
])
def test_local_url_unwraps_tunnels(clean, url, ok):
    assert P.local_url(url) is ok


def test_every_url_env_is_checked(clean):
    clean.setenv("ZANII_EVOLVE_OFFLINE", "1")
    for k in ("POLICY", "JUDGE", "SEARCH"):
        clean.setenv(f"ZANII_EVOLVE_{k}_MODEL", "ollama/llama4")
    clean.setenv("ZANII_EVOLVE_REGISTRY", "registry.example.com:8080/x")   # no scheme
    clean.setenv("BLACKBOX_URL", "HTTPS://bb.example.com")                 # upper case
    clean.setenv("ANTHROPIC_BASE_URL", "https://api.example.com")
    clean.setenv("ZANII_FOO_URL", "file:///etc/passwd")
    r = AG.check()
    bad = {e["name"] for e in r["endpoints"] if not e["local"]}
    assert {"ZANII_EVOLVE_REGISTRY", "BLACKBOX_URL", "ANTHROPIC_BASE_URL",
            "ZANII_FOO_URL"} <= bad and not r["ok"]
    clean.setenv("ZANII_EVOLVE_REGISTRY", str(ROOT / "registry"))         # a local folder
    clean.delenv("BLACKBOX_URL")
    clean.delenv("ANTHROPIC_BASE_URL")
    clean.delenv("ZANII_FOO_URL")
    assert AG.check()["ok"]


def test_domain_models_are_checked(clean):
    clean.setenv("ZANII_EVOLVE_OFFLINE", "1")
    r = AG.check({"policy_model": "ollama/a", "judge_model": "anthropic/claude-sonnet-5-5"})
    assert not r["ok"] and any("judge model" in x for x in r["leaks"])
