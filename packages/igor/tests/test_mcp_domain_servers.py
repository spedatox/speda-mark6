from app.mcp.servers import RESERVED_SERVER_NAMES


def test_domain_mcp_servers_are_reserved_from_custom_shadowing():
    """A custom server must never replace a built-in domain integration."""
    assert {"semantic_scholar", "sec_edgar", "gti_agentic"} <= RESERVED_SERVER_NAMES


def test_semantic_scholar_is_keyless_but_never_receives_an_empty_key(monkeypatch):
    """The academic server supports public, rate-limited use safely."""
    from app.mcp import servers

    monkeypatch.setattr(servers.settings, "semantic_scholar_api_key", "")
    client = servers.MCPClient(
        server_name="semantic_scholar",
        transport="stdio",
        command=["uvx", "--from", "s2-mcp-server>=1.5.0", "s2-mcp-server"],
        env={},
    )
    assert client.command[-1] == "s2-mcp-server"
    assert "SEMANTIC_SCHOLAR_API_KEY" not in client.env
