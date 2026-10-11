from __future__ import annotations

from investorch.config import REDACTED
from investorch.mcp import public_mcp_server_config


def test_public_server_masks_credentials_and_preserves_secret_references() -> None:
    server = {
        "name": "research",
        "transport": "streamable_http",
        "url": "https://user:password@part@host.test/mcp?token=url-token&key=${URL_KEY}#fragment-token",
        "headers": {"Authorization": "Bearer literal-token", "X-Key": "${HEADER_KEY}"},
    }
    public = public_mcp_server_config(server)
    assert public["url"] == f"https://{REDACTED}@host.test/mcp?token={REDACTED}&key=${{URL_KEY}}#{REDACTED}"
    assert public["headers"] == {"Authorization": REDACTED, "X-Key": "${HEADER_KEY}"}
    assert server["headers"]["Authorization"] == "Bearer literal-token"
