"""Free, fixed-host research connectors for Elyntro's bounded tool runtime.

These adapters query public knowledge APIs. They accept search terms, never arbitrary
URLs; network destinations are fixed in code to avoid turning the tools into SSRF proxies.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


TIMEOUT_SECONDS = 8
MAX_RESULTS = 5
USER_AGENT = "ElyntroResearch/1.0 (https://github.com/JejeStudio-web/elyntro-prototype)"


def _query_text(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return value[:300] if len(value) > 300 else value


def _get_json(url, host):
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise ValueError("upstream_http_error")
        raw = response.read(600_001)
    if len(raw) > 600_000:
        raise ValueError("upstream_response_too_large")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("upstream_invalid_json")
    return data


def _result(source, query, results, error=None):
    clean = []
    for item in results[:MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:240]
        url = str(item.get("url") or "").strip()[:900]
        snippet = str(item.get("snippet") or "").strip()[:1800]
        if title and url.startswith("https://"):
            clean.append({"title": title, "url": url, "snippet": snippet, "source": source})
    return {
        "ok": bool(clean),
        "source": source,
        "query": query,
        "summary": f"{len(clean)} resultados recuperados de {source}." if clean else f"{source} no devolvió resultados utilizables.",
        "results": clean,
        "error": error if not clean else None,
    }


def wikipedia_search(args):
    query = _query_text(args.get("query"))
    if not query:
        return {"ok": False, "error": "La consulta de Wikipedia debe ser texto no vacío."}
    params = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "format": "json", "utf8": 1, "srlimit": MAX_RESULTS,
    })
    try:
        data = _get_json("https://es.wikipedia.org/w/api.php?" + params, "es.wikipedia.org")
        rows = data.get("query", {}).get("search", [])
        results = [{
            "title": row.get("title", ""),
            "url": "https://es.wikipedia.org/wiki/" + urllib.parse.quote(str(row.get("title", "")).replace(" ", "_"), safe="()_"),
            "snippet": str(row.get("snippet", "")).replace("<span class=\"searchmatch\">", "").replace("</span>", ""),
        } for row in rows if isinstance(row, dict)]
        return _result("Wikipedia ES", query, results)
    except Exception as exc:
        return _result("Wikipedia ES", query, [], type(exc).__name__)


def crossref_search(args):
    query = _query_text(args.get("query"))
    if not query:
        return {"ok": False, "error": "La consulta de Crossref debe ser texto no vacío."}
    params = urllib.parse.urlencode({"query.bibliographic": query, "rows": MAX_RESULTS, "select": "title,URL,author,published,DOI"})
    try:
        data = _get_json("https://api.crossref.org/works?" + params, "api.crossref.org")
        rows = data.get("message", {}).get("items", [])
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            titles = row.get("title") or []
            title = titles[0] if titles else ""
            authors = row.get("author") or []
            author_names = ", ".join(" ".join(filter(None, [a.get("given"), a.get("family")])) for a in authors[:3] if isinstance(a, dict))
            year = ""
            date_parts = (row.get("published") or {}).get("date-parts") or []
            if date_parts and date_parts[0]:
                year = str(date_parts[0][0])
            snippet = "; ".join(part for part in (author_names, year, str(row.get("DOI") or "")) if part)
            results.append({"title": title, "url": row.get("URL", ""), "snippet": snippet})
        return _result("Crossref", query, results)
    except Exception as exc:
        return _result("Crossref", query, [], type(exc).__name__)


def arxiv_search(args):
    query = _query_text(args.get("query"))
    if not query:
        return {"ok": False, "error": "La consulta de arXiv debe ser texto no vacío."}
    params = urllib.parse.urlencode({"search_query": "all:" + query, "start": 0, "max_results": MAX_RESULTS, "sortBy": "relevance"})
    request = urllib.request.Request(
        "https://export.arxiv.org/api/query?" + params,
        headers={"Accept": "application/atom+xml", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ValueError("upstream_http_error")
            raw = response.read(600_001)
        if len(raw) > 600_000:
            raise ValueError("upstream_response_too_large")
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("atom:entry", ns):
            title = " ".join((entry.findtext("atom:title", default="", namespaces=ns)).split())
            summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ns)).split())
            url = entry.findtext("atom:id", default="", namespaces=ns)
            if title and url.startswith("https://arxiv.org/abs/"):
                results.append({"title": title, "url": url, "snippet": summary[:1800]})
        return _result("arXiv", query, results)
    except Exception as exc:
        return _result("arXiv", query, [], type(exc).__name__)
