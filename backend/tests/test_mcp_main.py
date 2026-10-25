import logging

import pytest
from starlette.applications import Starlette

from app.config import Settings, settings
from app.mcp_server import __main__ as entry
from app.mcp_server.server import MCP_PATH

CUSTOM_SECRET = "a-long-random-value-for-this-test!"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_the_default_secret_is_fine_on_this_machine(host):
    entry.check_secret(host, entry.DEFAULT_JWT_SECRET)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "mcp.example.com"])
def test_the_default_secret_is_refused_anywhere_else(host):
    with pytest.raises(SystemExit, match="JWT_SECRET"):
        entry.check_secret(host, entry.DEFAULT_JWT_SECRET)


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_a_real_secret_is_fine_anywhere(host):
    entry.check_secret(host, CUSTOM_SECRET)


def test_the_default_secret_is_the_one_in_the_settings():
    assert entry.DEFAULT_JWT_SECRET == "dev-only-secret-change-me-before-deploying"


def test_it_builds_an_app_without_an_openai_key_and_says_so(monkeypatch, caplog):
    monkeypatch.setattr(settings, "openai_api_key", "")

    with caplog.at_level(logging.WARNING):
        app = entry.build_app()

    assert isinstance(app, Starlette)
    assert "search_knowledge will fail" in caplog.text


def test_it_builds_an_app_with_an_openai_key(monkeypatch, caplog):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")

    with caplog.at_level(logging.WARNING):
        app = entry.build_app()

    assert isinstance(app, Starlette)
    assert "search_knowledge will fail" not in caplog.text


def test_the_default_server_url_ends_with_the_path_the_server_uses():
    assert Settings.model_fields["mcp_server_url"].default.endswith(MCP_PATH)


def test_the_app_serves_the_protocol_at_that_path_and_nowhere_else():
    paths = [route.path for route in entry.build_app().routes]

    assert paths == [MCP_PATH]
