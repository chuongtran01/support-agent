import pytest
from pydantic import ValidationError

from app.config import Settings


def settings(**env) -> Settings:
    """Settings as the environment gives them, ignoring the developer's .env file."""
    return Settings(_env_file=None, **env)


def test_the_agent_runs_its_tools_in_process_by_default(monkeypatch):
    monkeypatch.delenv("TOOL_BACKEND", raising=False)

    assert settings().tool_backend == "local"


@pytest.mark.parametrize("backend", ["local", "mcp"])
def test_the_tool_backend_can_be_set_from_the_environment(monkeypatch, backend):
    monkeypatch.setenv("TOOL_BACKEND", backend)

    assert settings().tool_backend == backend


@pytest.mark.parametrize("backend", ["MCP", "remote", ""])
def test_any_other_tool_backend_is_refused(monkeypatch, backend):
    monkeypatch.setenv("TOOL_BACKEND", backend)

    with pytest.raises(ValidationError, match="tool_backend"):
        settings()


def test_a_tool_call_over_mcp_has_thirty_seconds_by_default(monkeypatch):
    monkeypatch.delenv("MCP_CALL_TIMEOUT_SECONDS", raising=False)

    assert settings().mcp_call_timeout_seconds == 30


def test_the_call_timeout_can_be_set_from_the_environment(monkeypatch):
    monkeypatch.setenv("MCP_CALL_TIMEOUT_SECONDS", "2.5")

    assert settings().mcp_call_timeout_seconds == 2.5


@pytest.mark.parametrize("seconds", ["0", "-1", "soon"])
def test_a_call_timeout_that_is_not_a_positive_number_is_refused(monkeypatch, seconds):
    monkeypatch.setenv("MCP_CALL_TIMEOUT_SECONDS", seconds)

    with pytest.raises(ValidationError, match="mcp_call_timeout_seconds"):
        settings()
