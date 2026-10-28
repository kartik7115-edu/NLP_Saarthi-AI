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
    config.addinivalue_line("markers", "faq: test runs with the FAQ answer bank enabled")


@pytest.fixture(autouse=True)
def _pipeline_not_faq(request, monkeypatch):
    """The suites test the retrieval/LLM/translation pipeline: the FAQ answer bank is off unless a test turns it on."""
    if not request.node.get_closest_marker("faq"):
        monkeypatch.setattr(settings, "faq_answers_enabled", False)


@pytest.fixture(autouse=True)
def _no_answer_cache_file(monkeypatch):
    """Tests never read or write the on-disk answer cache (backend/app/data/answer_cache.json), and exercise the
    real pipeline rather than the curated answers (those have their own tests that switch them on)."""
    monkeypatch.setattr(settings, "answer_cache_file", "")
    monkeypatch.setattr(settings, "demo_answers_enabled", False)


@pytest.fixture(autouse=True)
def _no_live_llm(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setattr(settings, "groq_api_key", None)
