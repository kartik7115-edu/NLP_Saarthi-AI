"""Test isolation: no test may reach a live LLM just because a developer has GROQ_API_KEY in their .env.

Tests that want a (fake) configured Groq set settings.groq_api_key themselves; tests marked `live`
deliberately use the real key and are skipped when none is configured.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))      # lets tests import the shared translation_fakes helper

from app.config import settings  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls the real Groq API (needs GROQ_API_KEY and network); skipped otherwise")


@pytest.fixture(autouse=True)
def _no_live_llm(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setattr(settings, "groq_api_key", None)
