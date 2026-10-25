import json
import socket
from io import BytesIO
from urllib.error import HTTPError

import pytest

import search_providers
from search_providers import TavilySearchProvider, fetch_public_http, is_public_http_url


class JsonResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.payload[:size]


def public_dns(monkeypatch):
    monkeypatch.setattr(search_providers.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
    ])


def provider_response(monkeypatch, payload):
    public_dns(monkeypatch)
    monkeypatch.setattr(
        search_providers.urllib.request,
        "urlopen",
        lambda request, timeout: JsonResponse(payload),
    )


def test_tavily_python314_header_compatibility_and_environment_credentials(monkeypatch):
    base_request = search_providers.urllib.request.Request(search_providers.TAVILY_SEARCH_URL)
    assert not hasattr(base_request, "get_unredirected_header")

    monkeypatch.setenv("TAVILY_API_KEY", "env-test-token")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_unredirected_header("Authorization")
        return JsonResponse(json.dumps({"results": []}).encode())

    monkeypatch.setattr(search_providers.urllib.request, "urlopen", fake_urlopen)

    TavilySearchProvider().search("query")

    assert captured["authorization"] == "Bearer env-test-token"


@pytest.mark.parametrize(
    ("query", "limit", "expected_error"),
    [
        ("x" * 1001, 6, "search_provider_invalid_query"),
        ("query", 0, "search_provider_invalid_limit"),
        ("query", True, "search_provider_invalid_limit"),
    ],
)
def test_tavily_rejects_invalid_query_and_result_limits_without_network(
    monkeypatch, query, limit, expected_error
):
    called = []
    monkeypatch.setattr(
        search_providers.urllib.request,
        "urlopen",
        lambda *args, **kwargs: called.append(True),
    )

    results, error = TavilySearchProvider("test-token").search(query, limit=limit)

    assert results == []
    assert error == expected_error
    assert called == []


def test_tavily_auth_is_not_forwarded_by_urllib_redirects():
    from urllib.request import HTTPRedirectHandler, Request

    request = Request("https://api.tavily.com/search")
    request.add_unredirected_header("Authorization", "Bearer test-token")
    redirected = HTTPRedirectHandler().redirect_request(request, None, 307, "Temporary Redirect", {}, "https://redirect.example/search")

    assert redirected is not None
    assert redirected.get_header("Authorization") is None


def test_tavily_normalizes_multiple_results_and_provenance(monkeypatch):
    provider_response(monkeypatch, {"results": [
        {"title": "First", "url": "https://example.org/one", "content": "Excerpt one", "score": 0.9},
        {"title": "Second", "url": "https://example.net/two", "content": "Excerpt two", "published_date": "2026-10-01"},
    ]})

    results, error = TavilySearchProvider("test-token").search(" topic ", limit=4)

    assert error is None
    assert results == [
        {"title": "First", "url": "https://example.org/one", "snippet": "Excerpt one", "provider": "tavily", "score": 0.9},
        {"title": "Second", "url": "https://example.net/two", "snippet": "Excerpt two", "provider": "tavily", "published_date": "2026-10-01"},
    ]


def test_tavily_distinguishes_zero_results(monkeypatch):
    provider_response(monkeypatch, {"results": []})

    results, error = TavilySearchProvider("test-token").search("no matches")

    assert results == []
    assert error == "search_provider_no_results"


def test_tavily_reports_http_status_without_leaking_response_body(monkeypatch):
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 429, "rate limited", {}, BytesIO(b"secret body"))

    monkeypatch.setattr(search_providers.urllib.request, "urlopen", fake_urlopen)

    results, error = TavilySearchProvider("test-token").search("query")

    assert results == []
    assert error == "search_provider_http_429"


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (b"not-json", "search_provider_invalid_json"),
        (json.dumps({"answer": "not a results envelope"}).encode(), "search_provider_invalid_response"),
    ],
)
def test_tavily_rejects_invalid_json_or_response(monkeypatch, payload, expected_error):
    public_dns(monkeypatch)
    monkeypatch.setattr(search_providers.urllib.request, "urlopen", lambda request, timeout: JsonResponse(payload))

    results, error = TavilySearchProvider("test-token").search("query")

    assert results == []
    assert error == expected_error


def test_tavily_distinguishes_timeout(monkeypatch):
    monkeypatch.setattr(search_providers.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(socket.timeout()))

    results, error = TavilySearchProvider("test-token").search("query")

    assert results == []
    assert error == "search_provider_timeout"


def test_tavily_connection_errors_do_not_expose_credentials_or_provider_details(monkeypatch):
    api_key = "env-test-token"
    monkeypatch.setattr(
        search_providers.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            search_providers.urllib.error.URLError(f"Bearer {api_key}")
        ),
    )

    results, error = TavilySearchProvider(api_key).search("query")

    assert results == []
    assert error == "search_provider_connection_error"
    assert api_key not in error


def test_tavily_rejects_oversized_response(monkeypatch):
    provider_response(monkeypatch, {"results": [{"title": "A", "url": "https://example.org"}]})
    monkeypatch.setattr(search_providers, "MAX_RESPONSE_BYTES", 8)

    results, error = TavilySearchProvider("test-token").search("query")

    assert results == []
    assert error == "search_provider_response_too_large"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.0.0.8/metadata",
        "http://localhost/",
        "file:///etc/passwd",
        "https://user:pass@example.org/private",
        "https://example.org:8443/search",
    ],
)
def test_public_url_validator_rejects_private_local_or_unsupported_targets(url):
    assert is_public_http_url(url) is False


def test_public_url_validator_rejects_hostname_resolving_to_private_address(monkeypatch):
    monkeypatch.setattr(search_providers.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.1.2.3", 443)),
    ])

    assert is_public_http_url("https://rebound.example/source") is False


def test_tavily_drops_private_result_urls(monkeypatch):
    provider_response(monkeypatch, {"results": [
        {"title": "Private", "url": "http://127.0.0.1/admin", "content": "not public"},
    ]})

    results, error = TavilySearchProvider("test-token").search("query")

    assert results == []
    assert error == "search_provider_no_public_results"


def test_public_fetch_connects_to_the_validated_ip_not_the_hostname(monkeypatch):
    connected = []

    class FakeSocket:
        def __init__(self, *args): pass
        def settimeout(self, timeout): pass
        def connect(self, address): connected.append(address)
        def close(self): pass

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "text/plain"}
        def getheader(self, name): return None
        def getheaders(self): return list(self.headers.items())
        def read(self, size): return b"pinned destination"

    class FakeConnection:
        def __init__(self, host, port, timeout): pass
        def request(self, method, path, headers): pass
        def getresponse(self): return FakeResponse()
        def close(self): pass

    monkeypatch.setattr(search_providers.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 80)),
    ])
    monkeypatch.setattr(search_providers.socket, "socket", FakeSocket)
    monkeypatch.setattr(search_providers.http.client, "HTTPConnection", FakeConnection)

    response, error = fetch_public_http("http://source.example/article")

    assert error is None
    assert response["body"] == b"pinned destination"
    assert connected == [("93.184.216.34", 80)]


def test_public_fetch_rejects_mixed_public_and_private_dns_answers(monkeypatch):
    connected = []

    class FakeSocket:
        def __init__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            connected.append(address)

        def close(self):
            pass

    monkeypatch.setattr(search_providers.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.8", 80)),
    ])
    monkeypatch.setattr(search_providers.socket, "socket", FakeSocket)

    response, error = fetch_public_http("http://mixed-dns.example/source")

    assert response is None
    assert error == "http_fetch_unsafe_destination"
    assert connected == []


def test_public_fetch_keeps_hostname_for_tls_and_host_header(monkeypatch):
    observed = {}

    class FakeSocket:
        def __init__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            observed["destination"] = address

        def sendall(self, data):
            observed["request"] = data.decode("ascii")

        def makefile(self, mode):
            return BytesIO(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 15\r\n"
                b"Connection: close\r\n\r\n"
                b"TLS destination"
            )

        def close(self):
            pass

    class TLSContext:
        check_hostname = True
        verify_mode = search_providers.ssl.CERT_REQUIRED

        def wrap_socket(self, raw_socket, *, server_hostname):
            observed["server_hostname"] = server_hostname
            return raw_socket

    monkeypatch.setattr(search_providers.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
    ])
    monkeypatch.setattr(search_providers.socket, "socket", FakeSocket)
    monkeypatch.setattr(search_providers.ssl, "create_default_context", lambda: TLSContext())

    response, error = fetch_public_http("https://source.example/article")

    assert error is None
    assert response["body"] == b"TLS destination"
    assert observed["destination"] == ("93.184.216.34", 443)
    assert observed["server_hostname"] == "source.example"
    assert "Host: source.example" in observed["request"]
    assert "GET /article HTTP/1.1" in observed["request"]


def test_public_fetch_revalidates_redirect_hostname_dns_before_connecting(monkeypatch):
    connected = []
    resolved = []

    class FakeSocket:
        def __init__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            connected.append(address)

        def close(self):
            pass

    class RedirectResponse:
        status = 302

        def getheader(self, name):
            return "http://redirect.example/private" if name.lower() == "location" else None

        def getheaders(self):
            return [("Location", "http://redirect.example/private")]

        def read(self, size):
            return b""

    class FakeConnection:
        def __init__(self, host, port, timeout):
            pass

        def request(self, method, path, headers):
            pass

        def getresponse(self):
            return RedirectResponse()

        def close(self):
            pass

    def resolve(host, port, **kwargs):
        resolved.append(host)
        address = "93.184.216.34" if host == "source.example" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]

    monkeypatch.setattr(search_providers.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(search_providers.socket, "socket", FakeSocket)
    monkeypatch.setattr(search_providers.http.client, "HTTPConnection", FakeConnection)

    response, error = fetch_public_http("http://source.example/article")

    assert response is None
    assert error == "http_fetch_unsafe_destination"
    assert resolved == ["source.example", "redirect.example"]
    assert connected == [("93.184.216.34", 80)]


def test_public_fetch_revalidates_redirect_before_connecting(monkeypatch):
    connected = []

    class FakeSocket:
        def __init__(self, *args): pass
        def settimeout(self, timeout): pass
        def connect(self, address): connected.append(address)
        def close(self): pass

    class RedirectResponse:
        status = 302
        def getheader(self, name): return "http://127.0.0.1/admin" if name.lower() == "location" else None
        def getheaders(self): return [("Location", "http://127.0.0.1/admin")]
        def read(self, size): return b""

    class FakeConnection:
        def __init__(self, host, port, timeout): pass
        def request(self, method, path, headers): pass
        def getresponse(self): return RedirectResponse()
        def close(self): pass

    def resolve(host, port, **kwargs):
        if host == "source.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 80))]
        raise AssertionError("private IP literals must be rejected before resolution")

    monkeypatch.setattr(search_providers.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(search_providers.socket, "socket", FakeSocket)
    monkeypatch.setattr(search_providers.http.client, "HTTPConnection", FakeConnection)

    response, error = fetch_public_http("http://source.example/article")

    assert response is None
    assert error == "http_fetch_unsafe_destination"
    assert connected == [("93.184.216.34", 80)]


def test_public_fetch_re_resolves_and_pins_each_public_redirect(monkeypatch):
    connected = []
    resolutions = ["93.184.216.34", "1.1.1.1"]
    calls = {"count": 0}

    class FakeSocket:
        def __init__(self, *args): pass
        def settimeout(self, timeout): pass
        def connect(self, address): connected.append(address)
        def close(self): pass

    class RedirectResponse:
        status = 302
        def getheader(self, name): return "/next" if name.lower() == "location" else None
        def getheaders(self): return [("Location", "/next")]

    class FinalResponse:
        status = 200
        def getheaders(self): return [("Content-Type", "text/plain")]
        def read(self, size): return b"final body"

    class FakeConnection:
        def __init__(self, host, port, timeout): pass
        def request(self, method, path, headers): pass
        def getresponse(self):
            calls["count"] += 1
            return RedirectResponse() if calls["count"] == 1 else FinalResponse()
        def close(self): pass

    def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (resolutions.pop(0), port))]

    monkeypatch.setattr(search_providers.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(search_providers.socket, "socket", FakeSocket)
    monkeypatch.setattr(search_providers.http.client, "HTTPConnection", FakeConnection)

    response, error = fetch_public_http("http://source.example/article")

    assert error is None
    assert response["body"] == b"final body"
    assert connected == [("93.184.216.34", 80), ("1.1.1.1", 80)]


def test_tavily_distinguishes_missing_credentials_without_network(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    called = []
    monkeypatch.setattr(search_providers.urllib.request, "urlopen", lambda *args, **kwargs: called.append(True))

    results, error = TavilySearchProvider().search("query")

    assert results == []
    assert error == "search_provider_not_configured"
    assert called == []