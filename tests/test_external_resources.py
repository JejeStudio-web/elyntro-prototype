import json

import external_resources


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.body[:limit]


def test_wikipedia_connector_uses_fixed_api_and_normalizes_results(monkeypatch):
    observed = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["timeout"] = timeout
        return FakeResponse(json.dumps({
            "query": {"search": [{"title": "Ciencia de datos", "snippet": "Análisis de datos"}]}
        }))

    monkeypatch.setattr(external_resources.urllib.request, "urlopen", fake_urlopen)
    result = external_resources.wikipedia_search({"query": "ciencia de datos"})
    assert result["ok"] is True
    assert result["results"][0]["url"].startswith("https://es.wikipedia.org/wiki/")
    assert "wikipedia.org/w/api.php" in observed["url"]
    assert observed["timeout"] <= 10


def test_crossref_connector_returns_bibliographic_provenance(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse(json.dumps({
            "message": {"items": [{
                "title": ["A useful paper"],
                "URL": "https://doi.org/10.5555/example",
                "author": [{"given": "Ada", "family": "Lovelace"}],
                "published": {"date-parts": [[2024]]},
                "DOI": "10.5555/example",
            }]}
        }))

    monkeypatch.setattr(external_resources.urllib.request, "urlopen", fake_urlopen)
    result = external_resources.crossref_search({"query": "useful paper"})
    assert result["ok"] is True
    assert result["results"][0]["source"] == "Crossref"
    assert "Ada Lovelace" in result["results"][0]["snippet"]
    assert "2024" in result["results"][0]["snippet"]


def test_arxiv_connector_parses_atom_and_rejects_empty_query(monkeypatch):
    atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://arxiv.org/abs/2401.00001</id>
        <title>  A scientific paper  </title>
        <summary>Summary of the research.</summary>
      </entry>
    </feed>"""

    monkeypatch.setattr(external_resources.urllib.request, "urlopen", lambda request, timeout: FakeResponse(atom))
    result = external_resources.arxiv_search({"query": "language models"})
    assert result["ok"] is True
    assert result["results"][0]["title"] == "A scientific paper"
    assert result["results"][0]["url"] == "https://arxiv.org/abs/2401.00001"
    assert external_resources.arxiv_search({"query": " "})["ok"] is False
