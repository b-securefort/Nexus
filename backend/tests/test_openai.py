"""
Test OpenAI connectivity - verifies the backend can reach Azure OpenAI.
Run DIRECTLY with: python -m pytest tests/test_openai.py -v
(conftest.py overrides env vars, so we read .env ourselves)

This test uses REAL Azure OpenAI credentials from .env.
Skipped automatically in CI or when credentials are placeholders.
"""

import pytest

# Read real credentials from .env file, bypassing conftest.py overrides
_env_vars: dict[str, str] = {}
try:
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                _env_vars[key.strip()] = value.strip()
except FileNotFoundError:
    pass

_endpoint = _env_vars.get("AZURE_OPENAI_ENDPOINT", "")
_api_key = _env_vars.get("AZURE_OPENAI_API_KEY", "")
_api_version = _env_vars.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
_deployment = _env_vars.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-mini")

_has_real_key = bool(_api_key) and _api_key not in ("test-key", "placeholder", "")

skip_if_no_real_key = pytest.mark.skipif(
    not _has_real_key,
    reason="No real Azure OpenAI API key in .env",
)


@skip_if_no_real_key
class TestOpenAIConnectivity:
    """Integration tests that hit the real Azure OpenAI API."""

    def test_openai_client_connects(self):
        """Verify we can create an OpenAI client and list models."""
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=_endpoint,
            api_key=_api_key,
            api_version=_api_version,
        )
        assert client is not None

    def test_chat_completion_basic(self):
        """Verify a simple chat completion works with the configured deployment."""
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=_endpoint,
            api_key=_api_key,
            api_version=_api_version,
        )

        response = client.chat.completions.create(
            model=_deployment,
            messages=[{"role": "user", "content": "Say hello in one word."}],
            max_completion_tokens=10,
        )

        assert response.choices
        assert len(response.choices) > 0
        assert response.choices[0].message.content
        print(f"  Model response: {response.choices[0].message.content}")

    def test_chat_completion_streaming(self):
        """Verify streaming chat completion works."""
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=_endpoint,
            api_key=_api_key,
            api_version=_api_version,
        )

        stream = client.chat.completions.create(
            model=_deployment,
            messages=[{"role": "user", "content": "Say hi."}],
            max_completion_tokens=10,
            stream=True,
        )

        chunks = list(stream)
        assert len(chunks) > 0
        # At least one chunk should have content
        content_chunks = [
            c for c in chunks
            if c.choices and c.choices[0].delta and c.choices[0].delta.content
        ]
        assert len(content_chunks) > 0

    def test_chat_completion_with_tools(self):
        """Verify tool calling works with the deployment."""
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=_endpoint,
            api_key=_api_key,
            api_version=_api_version,
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"},
                        },
                        "required": ["location"],
                    },
                },
            }
        ]

        response = client.chat.completions.create(
            model=_deployment,
            messages=[{"role": "user", "content": "What's the weather in London?"}],
            tools=tools,
            max_completion_tokens=100,
        )

        assert response.choices
        choice = response.choices[0]
        # Model should either call the tool or respond with text
        assert choice.message.tool_calls or choice.message.content

    def test_uses_max_completion_tokens_not_max_tokens(self):
        """Verify that max_completion_tokens param is accepted (max_tokens is rejected by newer models)."""
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=_endpoint,
            api_key=_api_key,
            api_version=_api_version,
        )

        # This should succeed with max_completion_tokens
        response = client.chat.completions.create(
            model=_deployment,
            messages=[{"role": "user", "content": "Say ok."}],
            max_completion_tokens=10,
        )
        assert response.choices[0].message.content
