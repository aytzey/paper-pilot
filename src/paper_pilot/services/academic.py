from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx

from paper_pilot import __version__
from paper_pilot.config import Settings
from paper_pilot.models import (
    PaperRecord,
    combine_papers,
    normalize_doi,
    normalize_pmcid,
    normalize_publication_date,
)

SearchSource = Literal["all", "semantic_scholar", "openalex", "arxiv", "crossref", "europe_pmc", "doaj"]
SearchOrder = Literal["relevance", "newest"]
SOURCES = ("semantic_scholar", "openalex", "arxiv", "crossref", "europe_pmc", "doaj")

SEMANTIC_SCHOLAR_FIELDS = ",".join(
    [
        "title",
        "abstract",
        "authors",
        "year",
        "publicationDate",
        "venue",
        "url",
        "externalIds",
        "citationCount",
        "isOpenAccess",
        "openAccessPdf",
        "fieldsOfStudy",
    ]
)

RECOMMENDATION_FIELDS = ",".join(
    [
        "title",
        "abstract",
        "authors",
        "year",
        "venue",
        "url",
        "externalIds",
        "citationCount",
        "isOpenAccess",
        "openAccessPdf",
        "fieldsOfStudy",
    ]
)

SUBRESOURCE_DOI_SUFFIX_RE = re.compile(
    r"/(?:fig(?:ure)?|table|tbl|supp(?:lementary)?(?:-material)?|appendix|appx|video|media|dataset|data|code|eq|equation|chart|graph|image|scheme|box|plate)-[a-z0-9._-]+$",
    re.IGNORECASE,
)
FALLBACK_HTTP_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


@dataclass(slots=True)
class SearchBundle:
    results: list[PaperRecord]
    warnings: list[str]
    source_status: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class SearchPage:
    results: list[PaperRecord]
    next_cursor: str | None = None
    total: int | None = None
    sort_applied: str = "relevance"
    notes: list[str] = field(default_factory=list)
    limited: bool = False


class AcademicSearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent()}

    def _auth_headers(self, url: str) -> dict[str, str]:
        """An API key is sent only to the service it belongs to, and never in a URL that a warning could echo."""
        if url.startswith("https://api.semanticscholar.org/") and self.settings.semantic_scholar_api_key:
            return {"x-api-key": self.settings.semantic_scholar_api_key}
        if url.startswith("https://api.openalex.org/") and self.settings.openalex_api_key:
            return {"Authorization": f"Bearer {self.settings.openalex_api_key}"}
        return {}

    def _rate_limit_hint(self, source_name: str, exc: Exception) -> str:
        if "429" not in str(exc):
            return ""
        if source_name == "semantic_scholar" and not self.settings.semantic_scholar_api_key:
            return " (its keyless pool is usually rate-limited; set SEMANTIC_SCHOLAR_API_KEY)"
        if source_name == "openalex" and not self.settings.openalex_api_key:
            return (" (keyless requests share a small daily budget per network, about 100 searches, "
                    "reset at midnight UTC; set OPENALEX_API_KEY, keys are free)")
        return ""

    def _user_agent(self) -> str:
        if self.settings.openalex_email:
            return f"paper-pilot/{__version__} ({self.settings.openalex_email})"
        return f"paper-pilot/{__version__}"

    async def search_literature(
        self,
        topic: str,
        limit_per_source: int = 5,
        from_year: int | None = None,
        to_year: int | None = None,
        open_access_only: bool = True,
        sort_by: SearchOrder = "relevance",
        source: SearchSource = "all",
        cursor: str | None = None,
    ) -> SearchBundle:
        if not topic.strip():
            raise ValueError("topic must not be blank.")
        if not 1 <= limit_per_source <= 100:
            raise ValueError("limit_per_source must be between 1 and 100.")
        if any(year is not None and not 1 <= year <= 9999 for year in (from_year, to_year)):
            raise ValueError("Years must be between 1 and 9999.")
        if from_year is not None and to_year is not None and from_year > to_year:
            raise ValueError("from_year must not exceed to_year.")
        if sort_by not in {"relevance", "newest"} or source not in {"all", *SOURCES}:
            raise ValueError("Unknown search source or sort order.")
        if cursor is not None and (source == "all" or not cursor or len(cursor) > 32768):
            raise ValueError("A nonempty cursor requires one source and must be at most 32768 characters.")
        if cursor is not None:
            if source == "semantic_scholar" and sort_by == "newest":
                state = json.loads(cursor)
                if (not isinstance(state, dict)
                        or type(state.get("offset", 0)) is not int
                        or not 0 <= state.get("offset", 0) < 1000
                        or (state.get("token") is not None and not isinstance(state["token"], str))):
                    raise ValueError("Invalid Semantic Scholar bulk cursor.")
            else:
                bounds = {"arxiv": (0, 30000), "doaj": (1, 1000001), "crossref": (0, 10000),
                          "semantic_scholar": (0, 1000)}.get(source)
                if bounds and (not cursor.isdecimal() or not bounds[0] <= int(cursor) < bounds[1]):
                    raise ValueError(f"Invalid {source} page cursor; narrow the query if its result limit was reached.")
        selected = SOURCES if source == "all" else (source,)
        request = dict(topic=topic, limit_per_source=limit_per_source, from_year=from_year,
                       to_year=to_year, open_access_only=open_access_only, sort_by=sort_by)
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=self._headers(),
            trust_env=True,
            verify=self.settings.ssl_verify,
        ) as client:
            tasks = [getattr(self, f"_search_{name}")(
                client, topic, limit_per_source, from_year, to_year,
                **({"open_access_only": open_access_only} if name in {"semantic_scholar", "openalex", "europe_pmc"} else {}),
                sort_by=sort_by, cursor=cursor,
            ) for name in selected]
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            warnings: list[str] = []
            combined: list[PaperRecord] = []
            source_status = []
            for source_name, result in zip(selected, gathered, strict=True):
                current_request = {**request, "source": source_name, "cursor": cursor}
                if isinstance(result, Exception):
                    warnings.append(
                        f"{source_name} search failed: {self._format_exception(result)}{self._rate_limit_hint(source_name, result)}"
                    )
                    source_status.append({"source": source_name, "status": "error", "sort_requested": sort_by,
                                          "error": type(result).__name__, "retry_request": current_request})
                    continue
                source_status.append({
                    "source": source_name, "status": "limited" if result.limited else "ok",
                    "sort_requested": sort_by, "sort_applied": result.sort_applied,
                    "returned_count": len(result.results), "total_reported": result.total,
                    "next_request": {**current_request, "cursor": result.next_cursor} if result.next_cursor is not None else None,
                    "notes": result.notes,
                })
                combined.extend(result.results)

            merged = combine_papers(combined)
            merged, enrichment_warnings = await self._enrich_with_unpaywall(client, merged)
            warnings.extend(enrichment_warnings)

        if open_access_only:
            oa_only = [record for record in merged if record.is_open_access or record.pdf_url]
            if not oa_only and merged:
                warnings.append(
                    "No strictly open-access results were found; returning best-effort "
                    "results that may be closed-access."
                )
            merged = oa_only or merged
        # Re-rank by topic relevance first, then citations/recency/OA, so precise matches
        # outrank merely famous-but-tangential papers.
        if sort_by == "newest":
            merged.sort(key=lambda record: (record.publication_date or (f"{record.year:04d}" if record.year else ""), record.dedupe_key()), reverse=True)
        else:
            merged.sort(key=lambda record: record.quality_score(topic), reverse=True)
        return SearchBundle(results=merged, warnings=warnings, source_status=source_status)

    async def recommend_similar(
        self,
        seed_title: str,
        seed_doi: str | None = None,
        limit: int = 8,
        open_access_only: bool = True,
    ) -> SearchBundle:
        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers=self._headers(),
                trust_env=True,
                verify=self.settings.ssl_verify,
            ) as client:
                search_results = (await self._search_semantic_scholar(client, seed_doi or seed_title, 1, None, None, False)).results
                if not search_results:
                    raise RuntimeError("seed record not found")

                paper_id = search_results[0].source_id
                params = {
                    "limit": limit,
                    "fields": RECOMMENDATION_FIELDS,
                    "from": "recent",
                }
                recommendations_url = f"https://api.semanticscholar.org/recommendations/v1/papers/forpaper/{paper_id}"
                response = await client.get(recommendations_url, params=params, headers=self._auth_headers(recommendations_url))
                response.raise_for_status()
                data = response.json()
                records = combine_papers([
                    self._paper_from_semantic_scholar(item, related_score=1.0)
                    for item in data.get("recommendedPapers", [])
                ])
                records, warnings = await self._enrich_with_unpaywall(client, records)
                if open_access_only:
                    records = [record for record in records if record.is_open_access or record.pdf_url] or records
                return SearchBundle(results=sorted(records, key=lambda record: record.rank_score(), reverse=True), warnings=warnings)
        except Exception as exc:
            # One Crossref query instead of all six sources again: a second OpenAlex search per
            # pipeline run would halve the keyless daily budget for a list of title neighbours.
            fallback = await self.search_literature(
                seed_title, limit_per_source=limit, open_access_only=open_access_only, source="crossref",
            )
            fallback.warnings.append(
                f"Semantic Scholar recommendation endpoint unavailable ({self._format_exception(exc)}); fell back to keyword search."
            )
            return SearchBundle(results=fallback.results[:limit], warnings=fallback.warnings)

    async def _search_semantic_scholar(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
        open_access_only: bool,
        *, sort_by: SearchOrder = "relevance", cursor: str | None = None,
    ) -> SearchPage:
        params: dict[str, Any] = {
            "query": topic,
            "limit": min(limit, 100),
            "fields": SEMANTIC_SCHOLAR_FIELDS,
        }
        if open_access_only:
            params["openAccessPdf"] = ""
        if from_year and to_year:
            params["year"] = f"{from_year}-{to_year}"
        elif from_year:
            params["year"] = f"{from_year}-"  # open-ended: during or after from_year
        elif to_year:
            params["year"] = f"-{to_year}"  # open-ended: during or before to_year
        endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"
        offset = 0
        token = None
        if sort_by == "newest":
            endpoint += "/bulk"
            params.pop("limit")
            params.pop("year", None)
            if from_year or to_year:
                params["publicationDateOrYear"] = f"{from_year or ''}:{to_year or ''}"
            params["sort"] = "publicationDate:desc"
            if cursor:
                state = json.loads(cursor)
                token, offset = state.get("token"), state.get("offset", 0)
                if not isinstance(offset, int) or not 0 <= offset < 1000 or (token is not None and not isinstance(token, str)):
                    raise ValueError("Invalid Semantic Scholar bulk cursor.")
            if token:
                params["token"] = token
        else:
            offset = int(cursor or "0")
            if not 0 <= offset < 1000:
                raise ValueError("Semantic Scholar relevance search is limited to 1000 results.")
            params["offset"] = offset
            params["limit"] = min(limit, 1000 - offset)
        data = await self._get_json(
            client,
            endpoint,
            params,
            "semantic_scholar_search",
        )
        items = data.get("data", [])
        limited = False
        if sort_by == "newest":
            # The upstream bulk API returns up to 1000 records, irrespective of our display limit.
            # Reuse the cached batch and carry its local offset so no remainder is skipped.
            selected = items[offset:offset + limit]
            end = offset + len(selected)
            next_cursor = (json.dumps({"token": token, "offset": end}) if end < len(items) else
                           json.dumps({"token": data["token"], "offset": 0}) if data.get("token") and items else None)
        else:
            selected = items
            next_offset = data.get("next")
            limited = offset + len(items) >= 1000 and (data.get("total") or 0) > 1000
            next_cursor = str(next_offset) if next_offset is not None and next_offset < 1000 and items else None
        return SearchPage([self._paper_from_semantic_scholar(item) for item in selected], next_cursor,
                          data.get("total"), "publication_date" if sort_by == "newest" else "relevance",
                          ["Relevance search exposes at most 1000 results; narrow the query/year range."] if limited else [], limited)

    async def _search_openalex(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
        open_access_only: bool,
        *, sort_by: SearchOrder = "relevance", cursor: str | None = None,
    ) -> SearchPage:
        params: dict[str, Any] = {
            "search": topic,
            "per-page": limit,
            "cursor": cursor or "*",
        }
        if sort_by == "newest":
            params["sort"] = "publication_date:desc"
        if self.settings.openalex_email:
            params["mailto"] = self.settings.openalex_email
        filters: list[str] = []
        if open_access_only:
            filters.append("open_access.is_oa:true")
        if from_year and to_year:
            filters.append(f"publication_year:{from_year}-{to_year}")
        elif from_year:
            filters.append(f"publication_year:{from_year}-2100")
        elif to_year:
            filters.append(f"publication_year:1900-{to_year}")
        if filters:
            params["filter"] = ",".join(filters)
        data = await self._get_json(client, "https://api.openalex.org/works", params, "openalex_search")
        items = data.get("results", [])
        return SearchPage([self._paper_from_openalex(item) for item in items],
                          (data.get("meta") or {}).get("next_cursor") if items else None,
                          (data.get("meta") or {}).get("count"),
                          "publication_date" if sort_by == "newest" else "relevance")

    async def _search_arxiv(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None = None,
        to_year: int | None = None,
        *, sort_by: SearchOrder = "relevance", cursor: str | None = None,
    ) -> SearchPage:
        terms = re.findall(r"[\w-]+", topic)
        if not terms:
            return SearchPage([])
        # Preserve exact title matches while allowing the topic's words anywhere in a paper.
        phrase = " ".join(terms)
        query = f'(ti:"{phrase}" OR ({" AND ".join(f"all:{term}" for term in terms)}))'
        if from_year or to_year:
            query += f" AND submittedDate:[{from_year or 1991}01010000 TO {to_year or 9999}12312359]"
        offset = int(cursor or "0")
        if not 0 <= offset < 30000:
            raise ValueError("arXiv exposes at most 30000 results; narrow the query/year range.")
        params = {
            "search_query": query,
            "start": offset,
            "max_results": limit,
            "sortBy": "submittedDate" if sort_by == "newest" else "relevance",
            "sortOrder": "descending",
        }
        text = await self._get_text(client, "https://export.arxiv.org/api/query", params, "arxiv_search")
        records = self._parse_arxiv_feed(text)
        total_text = ET.fromstring(text).findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
        total = int(total_text) if total_text else None
        end = offset + len(records)
        more = bool(records) and (end < total if total is not None else len(records) == limit)
        limited = more and end >= 30000
        return SearchPage(records, str(end) if more and not limited else None, total,
                          "first_submission_date" if sort_by == "newest" else "relevance",
                          ["arXiv dates describe first submission, not journal publication."] +
                          (["30000-result limit reached; narrow the query/year range."] if limited else []), limited)

    async def _search_crossref(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
        *, sort_by: SearchOrder = "relevance", cursor: str | None = None,
    ) -> SearchPage:
        # Pages are addressed by offset. Crossref's deep-paging cursor drops relevance ordering
        # (cursor=* turns the first page into unrelated records) and rejects date sorting.
        offset = int(cursor or "0")
        if not 0 <= offset < 10000:
            raise ValueError("Crossref paging exposes at most 10000 results; narrow the query/year range.")
        params: dict[str, Any] = {
            "query.bibliographic": topic,
            "rows": min(limit, 10000 - offset),
            "offset": offset,
            "mailto": self.settings.openalex_email or None,
        }
        if sort_by == "newest":
            params.update(sort="published", order="desc")
        filters: list[str] = []
        if from_year:
            filters.append(f"from-pub-date:{from_year}-01-01")
        if to_year:
            filters.append(f"until-pub-date:{to_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        data = await self._get_json(
            client,
            "https://api.crossref.org/works",
            {k: v for k, v in params.items() if v is not None},
            "crossref_search",
        )
        message = data.get("message") or {}
        items = message.get("items", [])
        total = message.get("total-results")
        end = offset + len(items)
        more = bool(items) and (end < total if total is not None else len(items) == params["rows"])
        limited = more and end >= 10000
        next_cursor = str(end) if more and not limited else None
        notes = ["Crossref PDF links may require publisher access; metadata is not full-text evidence."]
        if limited:
            notes.append("Crossref paging stops at 10000 results. Narrow the query/year range to continue.")
        # A "component" is a supplement file or figure registered under its own DOI, not a paper.
        return SearchPage([self._paper_from_crossref(item) for item in items if item.get("type") != "component"],
                          next_cursor, total, "publication_date" if sort_by == "newest" else "relevance", notes, limited)

    async def _search_europe_pmc(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
        open_access_only: bool,
        *, sort_by: SearchOrder = "relevance", cursor: str | None = None,
    ) -> SearchPage:
        query = topic
        if open_access_only:
            query = f"{query} OPEN_ACCESS:y"
        if from_year and to_year:
            query = f"{query} FIRST_PDATE:[{from_year}-01-01 TO {to_year}-12-31]"
        elif from_year:
            query = f"{query} FIRST_PDATE:[{from_year}-01-01 TO 2100-12-31]"
        elif to_year:
            query = f"{query} FIRST_PDATE:[1900-01-01 TO {to_year}-12-31]"
        if sort_by == "newest":
            query = f"({query}) sort_date:y"

        params = {
            "query": query,
            "format": "json",
            "pageSize": limit,
            "resultType": "core",
            "cursorMark": cursor or "*",
        }
        data = await self._get_json(
            client,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params,
            "europepmc_search",
        )
        items = data.get("resultList", {}).get("result", [])
        next_cursor = data.get("nextCursorMark")
        return SearchPage([self._paper_from_europe_pmc(item) for item in items],
                          next_cursor if items and next_cursor != cursor else None,
                          data.get("hitCount"), "first_publication_date" if sort_by == "newest" else "relevance")

    async def _search_doaj(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
        *, sort_by: SearchOrder = "relevance", cursor: str | None = None,
    ) -> SearchPage:
        # DOAJ indexes only peer-reviewed open-access journal articles.
        page = int(cursor or "1")
        if not 1 <= page <= 1000000:
            raise ValueError("Invalid DOAJ page cursor.")
        query = topic
        if from_year or to_year:
            query = f"({topic}) AND bibjson.year:[{from_year or '*'} TO {to_year or '*'}]"
        params = {"pageSize": limit, "page": page}
        if sort_by == "newest":
            params["sort"] = "bibjson.year.exact:desc"
        data = await self._get_json(
            client,
            f"https://doaj.org/api/search/articles/{quote(query, safe='')}",
            params,
            "doaj_search",
        )
        records = [self._paper_from_doaj(item) for item in data.get("results", [])]
        # Keep a defensive local filter; continuation is based on raw upstream counts.
        total = data.get("total")
        more = bool(records) and (page * limit < total if total is not None else len(records) == limit)
        if from_year or to_year:
            lo = from_year or 0
            hi = to_year or 9999
            records = [r for r in records if r.year is None or lo <= r.year <= hi]
        return SearchPage(records, str(page + 1) if more else None, total,
                          "publication_year" if sort_by == "newest" else "relevance",
                          ["DOAJ contains OA journals only; provider sorting has year precision."])

    def _paper_from_doaj(self, item: dict[str, Any]) -> PaperRecord:
        bibjson = item.get("bibjson") or {}
        identifiers = bibjson.get("identifier") or []
        doi = next((i.get("id") for i in identifiers if (i.get("type") or "").lower() == "doi"), None)
        links = bibjson.get("link") or []
        pdf_url = next(
            (link.get("url") for link in links if (link.get("content_type") or "").lower() == "pdf"),
            None,
        )
        landing = next(
            (link.get("url") for link in links if (link.get("type") or "").lower() == "fulltext"),
            None,
        )
        if not pdf_url and (landing or "").lower().split("?")[0].endswith(".pdf"):
            pdf_url = landing
        journal = bibjson.get("journal") or {}
        year = int(bibjson["year"]) if str(bibjson.get("year", "")).strip().isdigit() else None
        month = str(bibjson.get("month") or "").zfill(2)
        return PaperRecord(
            source="doaj",
            source_id=item.get("id") or doi or bibjson.get("title", "unknown"),
            title=bibjson.get("title") or "Untitled",
            authors=[author.get("name", "") for author in bibjson.get("author", []) if author.get("name")],
            abstract=bibjson.get("abstract"),
            year=year,
            publication_date=normalize_publication_date(f"{year}-{month}") or normalize_publication_date(year),
            publication_date_source="doaj.bibjson.year/month",
            venue=journal.get("title"),
            doi=normalize_doi(doi),
            url=landing or (f"https://doi.org/{normalize_doi(doi)}" if doi else None),
            pdf_url=pdf_url,
            citation_count=None,
            is_open_access=True,
            keywords=[kw for kw in (bibjson.get("keywords") or []) if kw][:6],
        )

    def _paper_from_semantic_scholar(self, item: dict[str, Any], related_score: float | None = None) -> PaperRecord:
        external_ids = item.get("externalIds") or {}
        doi = normalize_doi(external_ids.get("DOI"))
        pdf_info = item.get("openAccessPdf") or {}
        return PaperRecord(
            source="semantic_scholar",
            source_id=item.get("paperId") or item.get("corpusId") or item.get("title", "unknown"),
            title=item.get("title") or "Untitled",
            authors=[author.get("name", "") for author in item.get("authors", []) if author.get("name")],
            abstract=item.get("abstract"),
            year=item.get("year"),
            publication_date=normalize_publication_date(item.get("publicationDate")) or normalize_publication_date(item.get("year")),
            publication_date_source="semantic_scholar.publicationDate/year",
            venue=item.get("venue"),
            doi=doi,
            url=item.get("url"),
            pdf_url=pdf_info.get("url"),
            citation_count=item.get("citationCount"),
            is_open_access=bool(item.get("isOpenAccess") or pdf_info.get("url")),
            keywords=item.get("fieldsOfStudy") or [],
            related_score=related_score,
            raw={key: value for key, value in {
                "arxiv_id": external_ids.get("ArXiv"),
                "pmcid": normalize_pmcid(external_ids.get("PubMedCentral")),
            }.items() if value},
        )

    def _paper_from_openalex(self, item: dict[str, Any]) -> PaperRecord:
        best_oa = item.get("best_oa_location") or {}
        open_access = item.get("open_access") or {}
        venue = (
            ((item.get("primary_location") or {}).get("source") or {}).get("display_name")
            or ((best_oa.get("source") or {}).get("display_name"))
        )
        url = (item.get("primary_location") or {}).get("landing_page_url") or item.get("id")
        return PaperRecord(
            source="openalex",
            source_id=item.get("id", "unknown"),
            title=item.get("display_name") or "Untitled",
            authors=[
                ((authorship.get("author") or {}).get("display_name"))
                for authorship in item.get("authorships", [])
                if (authorship.get("author") or {}).get("display_name")
            ],
            abstract=self._decode_abstract(item.get("abstract_inverted_index")),
            year=item.get("publication_year"),
            publication_date=normalize_publication_date(item.get("publication_date")) or normalize_publication_date(item.get("publication_year")),
            publication_date_source="openalex.publication_date/year",
            venue=venue,
            doi=normalize_doi(item.get("doi")),
            url=url,
            pdf_url=best_oa.get("pdf_url"),
            citation_count=item.get("cited_by_count"),
            is_open_access=bool(open_access.get("is_oa") or best_oa.get("pdf_url")),
            keywords=[((item.get("primary_topic") or {}).get("display_name"))] if (item.get("primary_topic") or {}).get("display_name") else [],
            raw={key: value for key, value in {
                "pmcid": normalize_pmcid((item.get("ids") or {}).get("pmcid")),
                "pdf_urls": [loc["pdf_url"] for loc in item.get("locations") or [] if (loc or {}).get("pdf_url")],
            }.items() if value},
        )

    def _paper_from_crossref(self, item: dict[str, Any]) -> PaperRecord:
        date_field = "published" if (item.get("published") or {}).get("date-parts") else "issued"
        parts = ((item.get(date_field) or {}).get("date-parts") or [[]])[0]
        publication_date = normalize_publication_date(parts)
        links = item.get("link") or []
        pdf_url = next((link.get("URL") for link in links if link.get("content-type") == "application/pdf"), None)
        abstract = item.get("abstract")
        if abstract:
            abstract = html.unescape(re.sub(r"<[^>]+>", " ", abstract))
            abstract = re.sub(r"\s+", " ", abstract).strip()
        return PaperRecord(
            source="crossref",
            source_id=item.get("DOI") or item.get("URL") or item.get("title", ["unknown"])[0],
            title=(item.get("title") or ["Untitled"])[0],
            authors=[
                " ".join(part for part in [author.get("given"), author.get("family")] if part)
                for author in item.get("author", [])
                if author.get("given") or author.get("family")
            ],
            abstract=abstract,
            year=int(publication_date[:4]) if publication_date else None,
            publication_date=publication_date,
            publication_date_source=f"crossref.{date_field}",
            venue=(item.get("container-title") or [None])[0],
            doi=normalize_doi(item.get("DOI")),
            url=item.get("URL"),
            pdf_url=pdf_url,
            citation_count=item.get("is-referenced-by-count"),
            is_open_access=bool(pdf_url),
            keywords=[],
        )

    def _paper_from_europe_pmc(self, item: dict[str, Any]) -> PaperRecord:
        pdf_url = None
        landing_url = None
        for full_text in ((item.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
            if full_text.get("availabilityCode") != "OA":
                continue
            if full_text.get("documentStyle") == "pdf" and not pdf_url:
                pdf_url = full_text.get("url")
            if full_text.get("documentStyle") in {"html", "doi"} and not landing_url:
                landing_url = full_text.get("url")

        authors = [part.strip() for part in (item.get("authorString") or "").split(",") if part.strip()]
        year = int(item["pubYear"]) if str(item.get("pubYear", "")).isdigit() else None
        keywords: list[str] = []
        for heading in ((item.get("meshHeadingList") or {}).get("meshHeading") or [])[:6]:
            descriptor = heading.get("descriptorName")
            if descriptor:
                keywords.append(descriptor)

        return PaperRecord(
            source="europe_pmc",
            source_id=item.get("pmcid") or item.get("pmid") or item.get("id") or item.get("title", "unknown"),
            title=item.get("title") or "Untitled",
            authors=authors,
            abstract=item.get("abstractText"),
            year=year,
            publication_date=normalize_publication_date(item.get("firstPublicationDate")) or normalize_publication_date(year),
            publication_date_source="europe_pmc.firstPublicationDate/pubYear",
            venue=(((item.get("journalInfo") or {}).get("journal") or {}).get("title")) or item.get("journalTitle"),
            doi=normalize_doi(item.get("doi")),
            url=landing_url,
            pdf_url=pdf_url,
            citation_count=item.get("citedByCount"),
            is_open_access=(item.get("isOpenAccess") == "Y") or bool(pdf_url),
            keywords=keywords,
            raw={"pmcid": item["pmcid"]} if item.get("pmcid") else {},
        )

    def _parse_arxiv_feed(self, xml_text: str) -> list[PaperRecord]:
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        records: list[PaperRecord] = []
        for entry in root.findall("atom:entry", ns):
            title = self._clean_whitespace(entry.findtext("atom:title", default="", namespaces=ns))
            abstract = self._clean_whitespace(entry.findtext("atom:summary", default="", namespaces=ns))
            entry_id = entry.findtext("atom:id", default="", namespaces=ns)
            authors = [
                self._clean_whitespace(author.findtext("atom:name", default="", namespaces=ns))
                for author in entry.findall("atom:author", ns)
            ]
            published = entry.findtext("atom:published", default="", namespaces=ns)
            year = int(published[:4]) if published[:4].isdigit() else None
            pdf_url = None
            for link in entry.findall("atom:link", ns):
                if link.attrib.get("title") == "pdf":
                    pdf_url = link.attrib.get("href")
                    break
            records.append(
                PaperRecord(
                    source="arxiv",
                    source_id=entry_id.rsplit("/", 1)[-1],
                    title=title or "Untitled",
                    authors=[author for author in authors if author],
                    abstract=abstract,
                    year=year,
                    publication_date=normalize_publication_date(published[:10]) or normalize_publication_date(year),
                    publication_date_source="arxiv.first_submission",
                    venue="arXiv",
                    doi=None,
                    url=entry_id,
                    pdf_url=pdf_url,
                    citation_count=None,
                    is_open_access=True,
                    keywords=[],
                )
            )
        return records

    @staticmethod
    def _decode_abstract(abstract_index: dict[str, list[int]] | None) -> str | None:
        if not abstract_index:
            return None
        size = max((max(positions) for positions in abstract_index.values()), default=-1) + 1
        words = [""] * size
        for word, positions in abstract_index.items():
            for index in positions:
                words[index] = word
        return " ".join(word for word in words if word)

    @staticmethod
    def _clean_whitespace(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    async def _enrich_with_unpaywall(
        self,
        client: httpx.AsyncClient,
        records: list[PaperRecord],
        *,
        force_lookup: bool = False,
    ) -> tuple[list[PaperRecord], list[str]]:
        candidate_dois = [
            self._doi_for_oa_lookup(record.doi)
            for record in records
            if record.doi and (force_lookup or not record.pdf_url)
        ]
        unique_dois = [doi for doi in dict.fromkeys(doi for doi in candidate_dois if doi)]
        for record in records:
            if not self._doi_for_oa_lookup(record.doi):
                record.raw = {**record.raw, "unpaywall": {"status": "not_applicable", "reason": "missing_doi"}}
            elif record.pdf_url and not force_lookup and "unpaywall" not in record.raw:
                record.raw = {**record.raw, "unpaywall": {"status": "deferred", "reason": "try_existing_pdf_first"}}
        if not unique_dois:
            return records, []
        lookup_semaphore = asyncio.Semaphore(4)
        results = await asyncio.gather(
            *(self._lookup_oa_metadata(client, doi, lookup_semaphore) for doi in unique_dois),
            return_exceptions=True,
        )
        by_doi = dict(zip(unique_dois, results, strict=True))
        warnings: list[str] = []
        for doi, result in by_doi.items():
            if isinstance(result, Exception):
                warnings.append(f"DOI OA enrichment failed ({doi}): {self._format_exception(result)}")
            elif result[2]:
                warnings.append(f"Unpaywall lookup failed ({doi}): {result[2]}; used OpenAlex fallback.")

        for record in records:
            doi = self._doi_for_oa_lookup(record.doi)
            if doi not in by_doi or (record.pdf_url and not force_lookup):
                continue
            enrichment = by_doi[doi]
            if isinstance(enrichment, Exception):
                record.raw = {**record.raw, "unpaywall": {"status": "error", "doi": doi, "error": str(enrichment)}}
                continue
            source, payload, unpaywall_error = enrichment
            if source == "unpaywall":
                best = payload.get("best_oa_location") or {}
                locations = payload.get("oa_locations") or []
                pdf_urls = [location["url_for_pdf"] for location in [best, *locations] if location.get("url_for_pdf")]
                # A landing-page URL is not a PDF. Preserve it as a bibliographic link only.
                if pdf_urls:
                    record.raw = {**record.raw, "original_pdf_url": record.raw.get("original_pdf_url") or record.pdf_url}
                    record.pdf_url = pdf_urls[0]
                record.url = record.url or best.get("url_for_landing_page") or payload.get("doi_url")
                record.is_open_access = record.is_open_access or bool(payload.get("is_oa") or record.pdf_url)
                record.raw = {
                    **record.raw,
                    "unpaywall": {
                        "status": "ok",
                        "doi": doi,
                        "is_oa": payload.get("is_oa"),
                        "oa_status": payload.get("oa_status"),
                        "host_type": best.get("host_type"),
                        "license": best.get("license"),
                        "best_oa_location": best,
                        "oa_locations": locations,
                    },
                }
                continue

            openalex_record = self._paper_from_openalex(payload)
            record.title = record.title or openalex_record.title
            record.authors = record.authors or openalex_record.authors
            record.abstract = record.abstract or openalex_record.abstract
            record.year = record.year or openalex_record.year
            record.venue = record.venue or openalex_record.venue
            record.url = record.url or openalex_record.url
            if openalex_record.pdf_url:
                record.raw = {**record.raw, "original_pdf_url": record.raw.get("original_pdf_url") or record.pdf_url}
                record.pdf_url = openalex_record.pdf_url
            record.citation_count = max(record.citation_count or 0, openalex_record.citation_count or 0) or None
            record.is_open_access = record.is_open_access or openalex_record.is_open_access or bool(record.pdf_url)
            record.keywords = sorted(set(record.keywords + openalex_record.keywords))
            record.raw = {
                **record.raw,
                "unpaywall": {"status": "error", "doi": doi, "error": unpaywall_error},
                "openalex_doi_lookup": {
                    "id": payload.get("id"),
                    "oa_status": ((payload.get("open_access") or {}).get("oa_status")),
                    "pdf_url": openalex_record.pdf_url,
                },
            }
        return records, warnings

    def _doi_for_oa_lookup(self, value: str | None) -> str | None:
        doi = normalize_doi(value)
        if not doi:
            return None
        return SUBRESOURCE_DOI_SUFFIX_RE.sub("", doi)

    async def _lookup_oa_metadata(
        self,
        client: httpx.AsyncClient,
        doi: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, dict[str, Any], str | None]:
        async with semaphore:
            try:
                return "unpaywall", await self._lookup_unpaywall(client, doi), None
            except Exception as exc:
                unpaywall_error = type(exc).__name__

            try:
                return "openalex", await self._lookup_openalex_by_doi(client, doi), unpaywall_error
            except Exception as fallback_exc:
                raise RuntimeError(
                    "unpaywall="
                    f"{unpaywall_error}; "
                    "openalex="
                    f"{type(fallback_exc).__name__}"
                ) from fallback_exc

    async def _lookup_unpaywall(self, client: httpx.AsyncClient, doi: str) -> dict[str, Any]:
        try:
            return await self._get_json(
                client,
                f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
                {"email": self.settings.effective_unpaywall_email},
                "unpaywall_lookup",
                timeout=httpx.Timeout(10.0, connect=3.0),
            )
        except FALLBACK_HTTP_ERRORS:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {408, 409, 425, 429, 500, 502, 503, 504}:
                raise

        await asyncio.sleep(0.5)
        return await self._get_json(
            client,
            f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
            {"email": self.settings.effective_unpaywall_email},
            "unpaywall_lookup",
            timeout=httpx.Timeout(10.0, connect=3.0),
        )

    async def lookup_pmcid(self, client: httpx.AsyncClient, doi: str) -> str | None:
        """Map a DOI to its PubMed Central id (NCBI ID Converter); None when PMC has no copy."""
        data = await self._get_json(
            client,
            "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/",
            {"ids": doi, "format": "json", "tool": "paper-pilot", "email": self.settings.effective_unpaywall_email},
            "pmc_idconv",
            timeout=httpx.Timeout(10.0, connect=3.0),
        )
        return normalize_pmcid(((data.get("records") or [{}])[0]).get("pmcid"))

    async def _lookup_openalex_by_doi(self, client: httpx.AsyncClient, doi: str) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.settings.openalex_email:
            params["mailto"] = self.settings.openalex_email
        return await self._get_json(
            client,
            f"https://api.openalex.org/works/{quote(f'https://doi.org/{doi}', safe=':/')}",
            params,
            "openalex_doi_lookup",
            timeout=httpx.Timeout(20.0, connect=5.0),
        )

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        # httpx appends a second line of boilerplate and quotes the whole request URL.
        message = re.sub(r" for url '[^']*'", "", str(exc).strip())
        return message.splitlines()[0][:300] if message else repr(exc)

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, Any],
        namespace: str,
        timeout: httpx.Timeout | float | None = None,
    ) -> dict[str, Any]:
        cache_file = self._cache_file(namespace, url, params, "json")
        cached = self._read_cache(cache_file)
        if cached and cached["expires_at"] > time.time():
            return cached["payload"]
        try:
            response = await client.get(
                url, params=params, headers=self._auth_headers(url), **({"timeout": timeout} if timeout is not None else {}),
            )
            response.raise_for_status()
            payload = response.json()
            self._write_cache(cache_file, payload)
            return payload
        except Exception:
            if cached and namespace != "unpaywall_lookup" and not namespace.endswith("_search"):
                return cached["payload"]
            raise

    async def _get_text(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, Any],
        namespace: str,
    ) -> str:
        cache_file = self._cache_file(namespace, url, params, "txt")
        cached = self._read_cache(cache_file)
        if cached and cached["expires_at"] > time.time():
            return cached["payload"]
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.text
            self._write_cache(cache_file, payload)
            return payload
        except Exception:
            if cached and not namespace.endswith("_search"):
                return cached["payload"]
            raise

    def _cache_file(self, namespace: str, url: str, params: dict[str, Any], suffix: str) -> Path:
        serialized = json.dumps({"url": url, "params": params}, sort_keys=True, default=str)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return self.settings.cache_dir / f"{namespace}-{digest}.{suffix}.cache"

    def _read_cache(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "payload" not in data or "expires_at" not in data:
                return None
            return data
        except Exception:
            return None

    def _write_cache(self, path: Path, payload: Any) -> None:
        path.write_text(
            json.dumps(
                {
                    "expires_at": int(time.time()) + self.settings.cache_ttl_sec,
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
