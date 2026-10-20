"""真实 HTTP 客户端的失败记账；替换传输层，所有测试均不联网。"""
import io
import urllib.error
import urllib.request

import pytest

from academic_search.sources import SourceError
from academic_search.sources.http import HttpClient


@pytest.mark.parametrize("status", [404, 429, 503])
@pytest.mark.parametrize("raise_on_error", [True, False])
def test_http_error_counts_request_and_reads_quota(monkeypatch, status, raise_on_error):
    url = "https://example.invalid/"
    error = urllib.error.HTTPError(url, status, "error", {
        "x-ratelimit-remaining-usd": "0", "x-ratelimit-cost-usd": "0",
    }, io.BytesIO(b'{"error":"probe"}'))

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    client = HttpClient()
    if raise_on_error:
        with pytest.raises(SourceError) as excinfo:
            client.get(url, source="openalex")
        assert excinfo.value.status == status and excinfo.value.body
        assert len(client.log) == 1 and client.log[0].status == status
    else:
        response = client.get_bytes(url, source="openalex", raise_on_error=False)
        assert response.status == status and response.content
    assert client.requests == 1 and client.rate_remaining_usd == 0
    assert client.cost_usd == 0


def test_network_failure_counts_the_attempt(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.URLError("simulated outage")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    client = HttpClient()
    with pytest.raises(SourceError):
        client.get("https://example.invalid/", source="openalex")
    assert client.requests == 1 and client.log[0].status == 0
    assert client.rate_remaining_usd is None


@pytest.mark.parametrize("max_bytes", [None, 1])
def test_received_headers_are_accounted_once_even_if_body_is_too_large(monkeypatch, max_bytes):
    response = io.BytesIO(b"payload")
    response.status = 200
    response.headers = {"x-ratelimit-cost-usd": "0.001", "x-ratelimit-remaining-usd": "0.2"}
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: response)
    client = HttpClient()
    if max_bytes is None:
        assert client.get_bytes("https://example.invalid/").content == b"payload"
    else:
        with pytest.raises(SourceError):
            client.get_bytes("https://example.invalid/", max_bytes=max_bytes)
    assert client.requests == 1 and client.cost_usd == 0.001
    assert client.rate_remaining_usd == 0.2
