import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from paper_pilot import server
from paper_pilot.config import Settings
from paper_pilot.models import combine_papers, normalize_publication_date
from paper_pilot.services.academic import AcademicSearchService


def service_at(tmp_path: Path) -> AcademicSearchService:
    settings = Settings(None, None, None, "user", None, tmp_path, (), 10)
    settings.cache_dir.mkdir(exist_ok=True)
    return AcademicSearchService(settings)


def test_native_sort_and_continuation_for_each_provider(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    seen = []

    async def get_json(client, url, params, namespace):
        seen.append((url, params))
        if namespace == "openalex_search":
            return {"meta": {"count": 2, "next_cursor": "oa-next"}, "results": [{"id": "W1", "display_name": "Soil", "publication_date": "2026-06-10"}]}
        if namespace == "crossref_search":
            return {"message": {"total-results": 2, "next-cursor": "cr-next", "items": [{"title": ["Soil"], "published": {"date-parts": [[2026, 6]]}}]}}
        if namespace == "europepmc_search":
            return {"hitCount": 2, "nextCursorMark": "pmc-next", "resultList": {"result": [{"id": "1", "title": "Soil", "firstPublicationDate": "2026-06-03"}]}}
        if namespace == "doaj_search":
            # Even when the defensive year filter removes a page, it must remain pageable.
            year = "2010" if params["page"] == 1 else "2026"
            return {"total": 2, "results": [{"id": year, "bibjson": {"title": "Soil", "year": year}}]}
        raise AssertionError(namespace)

    async def get_text(client, url, params, namespace):
        seen.append((url, params))
        return '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
          <opensearch:totalResults>2</opensearch:totalResults><entry><id>https://arxiv.org/abs/2606.00001</id>
          <title>Soil</title><published>2026-06-01T00:00:00Z</published><updated>2026-09-01T00:00:00Z</updated></entry></feed>'''

    monkeypatch.setattr(service, "_get_json", get_json)
    monkeypatch.setattr(service, "_get_text", get_text)
    for source, continuation in [("openalex", "oa-next"), ("crossref", "1"),
                                 ("europe_pmc", "pmc-next"), ("arxiv", "1"), ("doaj", "2")]:
        bundle = asyncio.run(service.search_literature("soil pH", 1, 2020, 2026, False, "newest", source))
        status = bundle.source_status[0]
        assert status["status"] == "ok", bundle.warnings
        assert status["next_request"]["cursor"] == continuation
        assert status["next_request"]["source"] == source
        assert status["next_request"]["open_access_only"] is False
        next_bundle = asyncio.run(service.search_literature(**status["next_request"]))
        if source == "doaj":
            assert bundle.results == [] and next_bundle.results[0].year == 2026
            assert next_bundle.source_status[0]["next_request"] is None
        if source == "arxiv":
            assert bundle.results[0].publication_date == "2026-06-01"
            assert bundle.results[0].publication_date_source == "arxiv.first_submission"
    params = [p for _, p in seen]
    assert any(p.get("sort") == "publication_date:desc" and p.get("cursor") == "oa-next" for p in params)
    assert any(p.get("sort") == "published" and p.get("order") == "desc" and p.get("offset") == 1 and "cursor" not in p for p in params)
    assert any(p.get("sortBy") == "submittedDate" and p.get("start") == 1 for p in params)
    assert any("sort_date:y" in p.get("query", "") and p.get("cursorMark") == "pmc-next" for p in params)
    assert any(p.get("sort") == "bibjson.year.exact:desc" and p.get("page") == 2 for p in params)
    assert any("bibjson.year%3A%5B2020%20TO%202026%5D" in url for url, _ in seen)


def test_semantic_scholar_bulk_remainder_is_not_skipped(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.url.params["sort"] == "publicationDate:desc"
        assert "limit" not in request.url.params
        ids = [1, 2, 3, 4, 5] if not request.url.params.get("token") else [6]
        data = {"total": 6, "data": [{"paperId": str(i), "title": str(i), "year": 2026} for i in ids]}
        if ids[0] == 1:
            data["token"] = "batch-two"
        return httpx.Response(200, json=data)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(upstream), **kwargs))

    async def run():
        args = dict(topic="soil pH", limit_per_source=2, open_access_only=False, sort_by="newest", source="semantic_scholar")
        found = []
        while args:
            bundle = await service.search_literature(**args)
            found.extend(p.source_id for p in bundle.results)
            args = bundle.source_status[0]["next_request"]
        return found

    ids = asyncio.run(run())
    assert sorted(ids) == ["1", "2", "3", "4", "5", "6"]
    assert len(ids) == len(set(ids))
    assert len(requests) == 2  # local continuations reuse the first cached bulk response


def test_newest_order_mcp_contract_and_failed_source_retry(tmp_path, monkeypatch):
    service = service_at(tmp_path)

    async def get_json(client, url, params, namespace):
        if namespace != "openalex_search":
            raise httpx.ConnectError("offline")
        return {"meta": {"count": 3}, "results": [
            {"id": "old", "display_name": "Soil pH old", "publication_date": "2010-01-01", "cited_by_count": 100000},
            {"id": "unknown", "display_name": "Soil pH unknown"},
            {"id": "new", "display_name": "Soil pH new", "publication_date": "2026-08-10", "cited_by_count": 0},
        ]}

    async def get_text(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(service, "_get_json", get_json)
    monkeypatch.setattr(service, "_get_text", get_text)
    monkeypatch.setattr(server, "get_academic_service", lambda: service)
    monkeypatch.setattr(server, "get_settings", lambda: service.settings)

    async def run():
        result = await server.mcp._tool_manager.call_tool("search_literature", {
            "topic": "soil pH", "sort_by": "newest", "open_access_only": False,
        }, context=None, convert_result=True)
        blocks = result[0] if isinstance(result, tuple) else result
        return json.loads(next(b.text for b in blocks if b.type == "text"))

    payload = asyncio.run(run())
    assert [p["title"] for p in payload["results"]] == ["Soil pH new", "Soil pH old", "Soil pH unknown"]
    assert payload["results"][0]["publication_date_precision"] == "day"
    failed = [s for s in payload["source_status"] if s["status"] == "error"]
    assert len(failed) == 5 and all(s["retry_request"]["source"] == s["source"] for s in failed)
    assert all("next_request" not in s for s in failed)  # failure is not exhaustion
    assert payload["coverage"]["scope"] == "returned_provider_pages"
    assert payload["coverage"]["open_access_only"] is False
    default = asyncio.run(service.search_literature("soil pH", source="openalex", open_access_only=False))
    assert default.results[0].source_id == "old"  # existing relevance default is preserved


def test_dates_preserve_precision_and_conflicting_provider_evidence(tmp_path):
    service = service_at(tmp_path)
    records = [
        service._paper_from_crossref({"DOI": "10.1234/date", "title": ["Soil"], "published": {"date-parts": [[2026, 2]]}}),
        service._paper_from_semantic_scholar({"externalIds": {"DOI": "10.1234/date"}, "title": "Soil", "publicationDate": "2026-02-10"}),
        service._paper_from_openalex({"doi": "10.1234/date", "display_name": "Soil", "publication_date": "2025-12-01"}),
    ]
    merged = combine_papers(records)[0].to_dict()
    assert merged["publication_date"] == "2026-02-10"
    assert len(merged["publication_dates"]) == 3
    assert normalize_publication_date([2025]) == "2025"
    assert normalize_publication_date([2025, 2]) == "2025-02"
    assert normalize_publication_date("2025-02-30") is None
    assert normalize_publication_date("0000") is None
    assert normalize_publication_date([2025, None, 2]) is None
    assert service._paper_from_crossref({"published": {"date-parts": [[]]}}).publication_date is None


@pytest.mark.parametrize("params", [
    {"topic": " "}, {"limit_per_source": 0}, {"limit_per_source": 101},
    {"from_year": 2026, "to_year": 2020}, {"from_year": -1},
    {"sort_by": "made_up"}, {"source": "made_up"}, {"cursor": "next"},
])
def test_invalid_search_does_not_call_providers(tmp_path, params):
    with pytest.raises(ValueError):
        asyncio.run(service_at(tmp_path).search_literature(**{"topic": "soil", **params}))


def test_malformed_provider_cursors_fail_before_network(tmp_path, monkeypatch):
    def unexpected_client(**kwargs):
        raise AssertionError("Invalid cursors must fail before contacting providers")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected_client)
    cases = [
        ("arxiv", "newest", "abc"), ("arxiv", "newest", "30000"),
        ("crossref", "newest", "abc"), ("crossref", "newest", "10000"),
        ("doaj", "newest", "0"), ("semantic_scholar", "relevance", "abc"),
        ("semantic_scholar", "newest", "{"), ("semantic_scholar", "newest", "[]"),
        ("semantic_scholar", "newest", '{"offset":true}'),
        ("semantic_scholar", "newest", '{"offset":1000}'),
        ("semantic_scholar", "newest", '{"token":2}'),
    ]
    for source, order, cursor in cases:
        with pytest.raises(ValueError):
            asyncio.run(service_at(tmp_path).search_literature("soil", source=source, sort_by=order, cursor=cursor))


def test_expired_search_cache_does_not_hide_outage(tmp_path):
    service = service_at(tmp_path)
    url = "https://api.openalex.org/works"
    params = {"search": "soil", "per-page": 5, "cursor": "*", "sort": "publication_date:desc"}
    service._cache_file("openalex_search", url, params, "json").write_text(json.dumps({"expires_at": 0, "payload": {"results": []}}))

    def offline(request):
        assert request.extensions["timeout"]["read"] == 5.0  # inherit the client's deadline
        raise httpx.ConnectError("offline")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
            return await service._get_json(client, url, params, "openalex_search")

    with pytest.raises(httpx.ConnectError):
        asyncio.run(run())


def test_provider_result_caps_are_visible_not_exhaustion(tmp_path, monkeypatch):
    service = service_at(tmp_path)

    async def get_json(client, url, params, namespace):
        if namespace == "crossref_search":
            assert params["offset"] == 9999 and params["rows"] == 1 and "cursor" not in params
            return {"message": {"total-results": 20000, "items": [{"title": ["Last accessible result"]}]}}
        assert params["offset"] == 999 and params["limit"] == 1
        return {"total": 2000, "next": 1000, "data": [{"paperId": "last", "title": "Last accessible result"}]}

    monkeypatch.setattr(service, "_get_json", get_json)
    for source, order, cursor in [("crossref", "newest", "9999"), ("semantic_scholar", "relevance", "999")]:
        bundle = asyncio.run(service.search_literature("soil", 5, open_access_only=False,
                                                       source=source, sort_by=order, cursor=cursor))
        status = bundle.source_status[0]
        assert status["status"] == "limited" and status["next_request"] is None
        assert status["notes"] and len(bundle.results) == 1


def test_crossref_relevance_search_pages_by_offset_because_its_cursor_loses_the_ranking(tmp_path):
    service = service_at(tmp_path)
    sent = []

    async def get_json(client, url, params, namespace):
        sent.append(params)
        return {"message": {"total-results": 40, "items": [{"title": [f"Soil {n}"], "DOI": f"10.1/{n}"} for n in range(5)]}}

    service._get_json = get_json
    first = asyncio.run(service._search_crossref(None, "soil pH", 5, None, None))
    second = asyncio.run(service._search_crossref(None, "soil pH", 5, None, None, cursor=first.next_cursor))
    assert [params["offset"] for params in sent] == [0, 5] and second.next_cursor == "10"
    assert all("cursor" not in params and "sort" not in params for params in sent)


def test_api_keys_reach_only_their_own_service_and_rate_limits_say_what_to_do(tmp_path):
    service = AcademicSearchService(replace(service_at(tmp_path).settings, semantic_scholar_api_key="s2-key", openalex_api_key="oa-key"))
    assert "x-api-key" not in service._headers() and "Authorization" not in service._headers()
    assert service._auth_headers("https://api.semanticscholar.org/graph/v1/paper/search") == {"x-api-key": "s2-key"}
    assert service._auth_headers("https://api.openalex.org/works") == {"Authorization": "Bearer oa-key"}
    assert service._auth_headers("https://api.crossref.org/works") == {}
    keyless = service_at(tmp_path)
    limited = httpx.HTTPStatusError("Client error '429 Too Many Requests'", request=None, response=None)
    assert "OPENALEX_API_KEY" in keyless._rate_limit_hint("openalex", limited)
    assert service._rate_limit_hint("openalex", limited) == ""
