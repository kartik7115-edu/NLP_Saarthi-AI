"""Runs the Node frontend suites from pytest and checks that the backend serves the modified frontend files.

The Node suites (tests/frontend/*.test.js) load the REAL backend/frontend_dist/voice.js and script.js into a vm context with
stubbed browser APIs: no npm dependencies, Node >= 18. They are skipped (not passed) when Node is not installed.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend_dist"
client = TestClient(app)


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed: frontend suites NOT run")
def test_frontend_node_suites_pass():
    p = subprocess.run(["node", "--test", "tests/frontend/voice.test.js", "tests/frontend/script.test.js",
                        "tests/frontend/stage5_repair.test.js", "tests/frontend/root_frontend.test.js"],
                       cwd=ROOT, capture_output=True, text=True, timeout=240)
    assert p.returncode == 0, p.stdout[-4000:] + p.stderr[-1500:]
    assert re.search(r"# fail 0\b", p.stdout)
    assert int(re.search(r"# pass (\d+)", p.stdout).group(1)) >= 62


@pytest.mark.parametrize("url,marker", [
    ("/voice.js", "SaarthiTTS"),
    ("/voice.js", "function loadVoices"),
    ("/script.js", "SaarthiTTS.speakResponse(data, getLang())"),
    ("/script.js", "function getRequestLang()"),
    ("/sw.js", "saarthi-v4"),
    ("/sw.js", "networkFirst"),
])
def test_backend_serves_the_modified_frontend(url, marker):
    r = client.get(url)
    assert r.status_code == 200 and marker in r.text, f"{url} does not contain {marker!r}"


@pytest.mark.parametrize("url,file", [("/voice.js", "voice.js"), ("/script.js", "script.js"), ("/sw.js", "sw.js")])
def test_served_bytes_are_exactly_frontend_dist(url, file):
    assert _norm(client.get(url).text) == _norm((DIST / file).read_text(encoding="utf-8"))


def test_old_service_worker_cache_name_is_gone():
    sw = client.get("/sw.js").text
    assert "saarthi-v2" not in sw and "saarthi-v3" not in sw     # v4: old caches (with old scripts) are deleted on activate
