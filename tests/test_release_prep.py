"""v0.2.0 release prep (oss#34): connector discovery and launch blockers."""
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from mm.mcp.server import oauth_metadata, protected_resource


def _discovery_client():
    app = Starlette(routes=[Route("/.well-known/oauth-authorization-server", oauth_metadata),
                            Route("/.well-known/oauth-protected-resource", protected_resource)])
    return TestClient(app)


def test_discovery_advertises_the_login_flow_connectors_need(monkeypatch):
    """claude.ai / ChatGPT read authorization_endpoint + registration_endpoint from here."""
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    meta = _discovery_client().get("/.well-known/oauth-authorization-server",
                                   headers={"host": "abc.trycloudflare.com", "x-forwarded-proto": "https"}).json()
    assert meta["authorization_endpoint"] == "https://abc.trycloudflare.com/authorize"
    assert meta["registration_endpoint"] == "https://abc.trycloudflare.com/register"
    assert "authorization_code" in meta["grant_types_supported"] and meta["code_challenge_methods_supported"] == ["S256"]


def test_configured_base_url_wins_and_empty_means_unset(monkeypatch):
    c = _discovery_client()
    monkeypatch.setenv("MCP_BASE_URL", "https://mm.example.com/")
    assert c.get("/.well-known/oauth-protected-resource").json() == {
        "resource": "https://mm.example.com/mcp", "authorization_servers": ["https://mm.example.com"]}
    monkeypatch.setenv("MCP_BASE_URL", "")  # what docker compose passes when it isn't set
    assert c.get("/.well-known/oauth-protected-resource", headers={"host": "localhost:8001"}).json()["resource"] \
        == "http://localhost:8001/mcp"


def test_a_corrupt_pdf_is_skipped_not_fatal(tmp_path):
    from mm.connectors.files import FilesConnector
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "good.md").write_text("# Good\n\nThis page should still be ingested.\n")
    (notes / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    c = FilesConnector(config={"path": str(notes)}, user_config=None)
    pages = c.ingest()
    assert len(pages) == 1 and pages[0].source_ref.endswith("good.md")  # the good page still lands
    assert len(c.skipped) == 1 and "broken.pdf" in c.skipped[0]
