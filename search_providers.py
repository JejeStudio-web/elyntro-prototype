"""Search provider adapters with normalized, provenance-aware results."""
from __future__ import annotations

import ipaddress
import http.client
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
MAX_RESPONSE_BYTES = 1_000_000
MAX_PUBLIC_FETCH_REDIRECTS = 4


class _TavilyRequest(urllib.request.Request):
    """urllib compatibility shim for Python 3.14 where get_unredirected_header is absent."""

    def get_unredirected_header(self, name, default=None):
        name = str(name)
        for candidate in (name, name.title(), name.lower(), name.upper()):
            if candidate in self.unredirected_hdrs:
                return self.unredirected_hdrs[candidate]
        return default


class SearchProvider(Protocol):
    def search(self, query: str, limit: int = 6) -> tuple[list[dict], str | None]: ...


def _public_target(url: str):
    """Resolve once and retain the validated socket addresses for the actual connect."""
    try:
        if not isinstance(url, str) or not url or any(ord(char) < 32 or ord(char) == 127 for char in url):
            return None
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port
        expected_port = 80 if parsed.scheme == "http" else 443
        if port not in (None, expected_port):
            return None
        host = parsed.hostname.rstrip(".").casefold()
        if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return None
        host = host.encode("idna").decode("ascii")
        port = port or expected_port
        try:
            literal = ipaddress.ip_address(host)
            if literal.version == 4:
                records = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (literal.compressed, port))]
            else:
                records = [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (literal.compressed, port, 0, 0))]
        except ValueError:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not records:
            return None
        validated = []
        seen = set()
        for record in records:
            family, socktype, proto, _, sockaddr = record
            address = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
            if not address.is_global:
                return None
            key = (family, socktype, proto, sockaddr)
            if key not in seen:
                seen.add(key)
                validated.append((family, socktype, proto, sockaddr))
        return parsed, host, port, tuple(validated)
    except (OSError, ValueError, UnicodeError):
        return None


def is_public_http_url(url: str) -> bool:
    """Check that a URL resolves only to global HTTP(S) addresses.

    Call ``fetch_public_http`` for requests: this predicate alone does not pin a
    subsequent connection to the addresses it observed.
    """
    return _public_target(url) is not None


def fetch_public_http(
    url: str,
    *,
    timeout: float = 10.0,
    max_bytes: int = 900_000,
    headers: dict[str, str] | None = None,
    max_redirects: int = MAX_PUBLIC_FETCH_REDIRECTS,
) -> tuple[dict | None, str | None]:
    """Fetch HTTP(S) directly to a validated IP and re-resolve/revalidate each redirect.

    No environment proxy is used. HTTPS still verifies the certificate and sends SNI
    for the URL hostname. This pins the socket destination for each hop, though it
    is not a general-purpose network sandbox.
    """
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0
            or isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1
            or isinstance(max_redirects, bool) or not isinstance(max_redirects, int) or not 0 <= max_redirects <= 8):
        return None, "http_fetch_invalid_limits"

    deadline = time.monotonic() + timeout
    current_url = url
    for redirect_count in range(max_redirects + 1):
        target = _public_target(current_url)
        if target is None:
            return None, "http_fetch_unsafe_destination"
        parsed, host, port, addresses = target
        request_target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        request_headers = {"Connection": "close", **(headers or {})}
        redirected_url = None
        last_error = "http_fetch_connection_error"

        for family, socktype, proto, sockaddr in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "http_fetch_timeout"
            raw_socket = None
            connection = None
            try:
                raw_socket = socket.socket(family, socktype, proto)
                raw_socket.settimeout(remaining)
                raw_socket.connect(sockaddr)
                if parsed.scheme == "https":
                    raw_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=host)
                connection = http.client.HTTPConnection(host, port, timeout=remaining)
                connection.sock = raw_socket
                raw_socket = None
                connection.request("GET", request_target, headers=request_headers)
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        return None, "http_fetch_redirect_without_location"
                    if redirect_count >= max_redirects:
                        return None, "http_fetch_too_many_redirects"
                    redirected_url = urllib.parse.urljoin(current_url, location)
                    next_scheme = urllib.parse.urlsplit(redirected_url).scheme.casefold()
                    if parsed.scheme == "https" and next_scheme != "https":
                        return None, "http_fetch_redirect_downgrade"
                    break
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    return None, "http_fetch_response_too_large"
                return {
                    "status": response.status,
                    "headers": dict(response.getheaders()),
                    "body": body,
                    "url": current_url,
                }, None
            except (socket.timeout, TimeoutError):
                last_error = "http_fetch_timeout"
            except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
                last_error = "http_fetch_connection_error"
            finally:
                if connection is not None:
                    connection.close()
                elif raw_socket is not None:
                    raw_socket.close()

        if redirected_url is not None:
            current_url = redirected_url
            continue
        return None, last_error
    return None, "http_fetch_too_many_redirects"


class TavilySearchProvider:
    """Tavily Search adapter. It never fabricates results or retries paid requests."""

    name = "tavily"

    def __init__(self, api_key: str | None = None, timeout: float = 12.0) -> None:
        self.api_key = os.environ.get("TAVILY_API_KEY", "").strip() if api_key is None else api_key.strip()
        if not 0.1 <= timeout <= 60:
            raise ValueError("timeout must be between 0.1 and 60 seconds")
        self.timeout = timeout

    def search(self, query: str, limit: int = 6) -> tuple[list[dict], str | None]:
        if not self.api_key:
            return [], "search_provider_not_configured"
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            return [], "search_provider_invalid_query"
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return [], "search_provider_invalid_limit"

        payload = {
            "query": query.strip()[:1000],
            "search_depth": "basic",
            "max_results": min(limit, 10),
            "include_answer": False,
            "include_raw_content": False,
        }
        request = _TavilyRequest(
            TAVILY_SEARCH_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Elyntro/1.0",
            },
            method="POST",
        )
        request.add_unredirected_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if not 200 <= status < 300:
                    return [], f"search_provider_http_{status}"
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return [], "search_provider_response_too_large"
        except urllib.error.HTTPError as exc:
            return [], f"search_provider_http_{exc.code}"
        except (TimeoutError, socket.timeout):
            return [], "search_provider_timeout"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return [], "search_provider_timeout"
            return [], "search_provider_connection_error"
        except OSError:
            return [], "search_provider_connection_error"

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return [], "search_provider_invalid_json"
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            return [], "search_provider_invalid_response"
        if not data["results"]:
            return [], "search_provider_no_results"

        normalized: list[dict] = []
        rejected_url = False
        invalid_item = False
        for item in data["results"][: payload["max_results"]]:
            if not isinstance(item, dict):
                invalid_item = True
                continue
            title = item.get("title")
            url = item.get("url")
            excerpt = item.get("content") or item.get("raw_content") or item.get("snippet")
            if not isinstance(title, str) or not title.strip() or not isinstance(url, str) or not url.strip():
                invalid_item = True
                continue
            if not is_public_http_url(url):
                rejected_url = True
                continue
            result = {
                "title": title.strip()[:220],
                "url": url.strip()[:800],
                "snippet": excerpt.strip()[:2000] if isinstance(excerpt, str) else "",
                "provider": self.name,
            }
            score = item.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                result["score"] = score
            published_date = item.get("published_date")
            if isinstance(published_date, str) and published_date.strip():
                result["published_date"] = published_date.strip()[:120]
            normalized.append(result)

        if normalized:
            if rejected_url or invalid_item:
                return normalized, "search_provider_partial_results"
            return normalized, None
        if rejected_url:
            return [], "search_provider_no_public_results"
        return [], "search_provider_invalid_response"