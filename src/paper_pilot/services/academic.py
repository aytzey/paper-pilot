from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from paper_pilot.config import Settings
from paper_pilot.models import PaperRecord, combine_papers, normalize_doi

SEMANTIC_SCHOLAR_FIELDS = ",".join(
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


class AcademicSearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": self._user_agent()}
        if self.settings.semantic_scholar_api_key:
            headers["x-api-key"] = self.settings.semantic_scholar_api_key
        return headers

    def _user_agent(self) -> str:
        if self.settings.openalex_email:
            return f"paper-pilot/0.4 ({self.settings.openalex_email})"
        return "paper-pilot/0.4"

    async def search_literature(
        self,
        topic: str,
        limit_per_source: int = 5,
        from_year: int | None = None,
        to_year: int | None = None,
        open_access_only: bool = True,
    ) -> SearchBundle:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=self._headers(),
            trust_env=True,
            verify=self.settings.ssl_verify,
        ) as client:
            tasks = [
                self._search_semantic_scholar(client, topic, limit_per_source, from_year, to_year, open_access_only),
                self._search_openalex(client, topic, limit_per_source, from_year, to_year, open_access_only),
                self._search_arxiv(client, topic, limit_per_source),
                self._search_crossref(client, topic, limit_per_source, from_year, to_year),
                self._search_europe_pmc(client, topic, limit_per_source, from_year, to_year, open_access_only),
                self._search_doaj(client, topic, limit_per_source, from_year, to_year),
            ]
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            warnings: list[str] = []
            combined: list[PaperRecord] = []
            for source_name, result in zip(
                ["semantic_scholar", "openalex", "arxiv", "crossref", "europe_pmc", "doaj"],
                gathered,
                strict=True,
            ):
                if isinstance(result, Exception):
                    warnings.append(f"{source_name} search failed: {self._format_exception(result)}")
                    continue
                combined.extend(result)

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
        return SearchBundle(results=merged, warnings=warnings)

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
                search_results = await self._search_semantic_scholar(client, seed_doi or seed_title, 1, None, None, False)
                if not search_results:
                    raise RuntimeError("seed record not found")

                paper_id = search_results[0].source_id
                params = {
                    "limit": limit,
                    "fields": RECOMMENDATION_FIELDS,
                    "from": "recent",
                }
                response = await client.get(
                    f"https://api.semanticscholar.org/recommendations/v1/papers/forpaper/{paper_id}",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()
                records = [
                    self._paper_from_semantic_scholar(item, related_score=1.0)
                    for item in data.get("recommendedPapers", [])
                ]
                records, _ = await self._enrich_with_unpaywall(client, records)
                if open_access_only:
                    records = [record for record in records if record.is_open_access or record.pdf_url] or records
                return SearchBundle(results=combine_papers(records), warnings=[])
        except Exception as exc:
            fallback = await self.search_literature(seed_title, limit_per_source=max(limit // 2, 1), open_access_only=open_access_only)
            fallback.warnings.append(
                f"Semantic Scholar recommendation endpoint unavailable ({exc}); fell back to keyword search."
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
    ) -> list[PaperRecord]:
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
        data = await self._get_json(
            client,
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params,
            "semantic_scholar_search",
        )
        return [self._paper_from_semantic_scholar(item) for item in data.get("data", [])]

    async def _search_openalex(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
        open_access_only: bool,
    ) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "search": topic,
            "per-page": limit,
        }
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
        return [self._paper_from_openalex(item) for item in data.get("results", [])]

    async def _search_arxiv(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
    ) -> list[PaperRecord]:
        params = {
            "search_query": f"all:{topic}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        text = await self._get_text(client, "https://export.arxiv.org/api/query", params, "arxiv_search")
        return self._parse_arxiv_feed(text)

    async def _search_crossref(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
    ) -> list[PaperRecord]:
        params: dict[str, Any] = {
            "query.bibliographic": topic,
            "rows": limit,
            "mailto": self.settings.openalex_email or None,
        }
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
        return [self._paper_from_crossref(item) for item in data.get("message", {}).get("items", [])]

    async def _search_europe_pmc(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
        open_access_only: bool,
    ) -> list[PaperRecord]:
        query = topic
        if open_access_only:
            query = f"{query} OPEN_ACCESS:y"
        if from_year and to_year:
            query = f"{query} FIRST_PDATE:[{from_year}-01-01 TO {to_year}-12-31]"
        elif from_year:
            query = f"{query} FIRST_PDATE:[{from_year}-01-01 TO 2100-12-31]"
        elif to_year:
            query = f"{query} FIRST_PDATE:[1900-01-01 TO {to_year}-12-31]"

        params = {
            "query": query,
            "format": "json",
            "pageSize": limit,
            "resultType": "core",
        }
        data = await self._get_json(
            client,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params,
            "europepmc_search",
        )
        return [self._paper_from_europe_pmc(item) for item in data.get("resultList", {}).get("result", [])]

    async def _search_doaj(
        self,
        client: httpx.AsyncClient,
        topic: str,
        limit: int,
        from_year: int | None,
        to_year: int | None,
    ) -> list[PaperRecord]:
        # DOAJ indexes only peer-reviewed open-access journal articles.
        data = await self._get_json(
            client,
            f"https://doaj.org/api/search/articles/{quote(topic, safe='')}",
            {"pageSize": min(max(limit, 1), 100), "page": 1},
            "doaj_search",
        )
        records = [self._paper_from_doaj(item) for item in data.get("results", [])]
        # DOAJ query syntax is fragile; filter by year client-side instead.
        if from_year or to_year:
            lo = from_year or 0
            hi = to_year or 9999
            records = [r for r in records if r.year is None or lo <= r.year <= hi]
        return records

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
        journal = bibjson.get("journal") or {}
        year = int(bibjson["year"]) if str(bibjson.get("year", "")).strip().isdigit() else None
        return PaperRecord(
            source="doaj",
            source_id=item.get("id") or doi or bibjson.get("title", "unknown"),
            title=bibjson.get("title") or "Untitled",
            authors=[author.get("name", "") for author in bibjson.get("author", []) if author.get("name")],
            abstract=bibjson.get("abstract"),
            year=year,
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
            venue=item.get("venue"),
            doi=doi,
            url=item.get("url"),
            pdf_url=pdf_info.get("url"),
            citation_count=item.get("citationCount"),
            is_open_access=bool(item.get("isOpenAccess") or pdf_info.get("url")),
            keywords=item.get("fieldsOfStudy") or [],
            related_score=related_score,
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
            venue=venue,
            doi=normalize_doi(item.get("doi")),
            url=url,
            pdf_url=best_oa.get("pdf_url"),
            citation_count=item.get("cited_by_count"),
            is_open_access=bool(open_access.get("is_oa") or best_oa.get("pdf_url")),
            keywords=[((item.get("primary_topic") or {}).get("display_name"))] if (item.get("primary_topic") or {}).get("display_name") else [],
        )

    def _paper_from_crossref(self, item: dict[str, Any]) -> PaperRecord:
        issued = item.get("issued", {}).get("date-parts", [[None]])
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
            year=issued[0][0],
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
            venue=(((item.get("journalInfo") or {}).get("journal") or {}).get("title")) or item.get("journalTitle"),
            doi=normalize_doi(item.get("doi")),
            url=landing_url,
            pdf_url=pdf_url,
            citation_count=item.get("citedByCount"),
            is_open_access=(item.get("isOpenAccess") == "Y") or bool(pdf_url),
            keywords=keywords,
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
    ) -> tuple[list[PaperRecord], list[str]]:
        if not self.settings.unpaywall_enabled:
            return records, []

        candidate_dois = [
            self._doi_for_oa_lookup(record.doi)
            for record in records
            if record.doi and (not record.pdf_url or not record.is_open_access)
        ]
        unique_dois = [doi for doi in dict.fromkeys(doi for doi in candidate_dois if doi)]
        if not unique_dois:
            return records, []

        selected_dois = unique_dois[:20]
        lookup_semaphore = asyncio.Semaphore(4)
        results = await asyncio.gather(
            *(self._lookup_oa_metadata(client, doi, lookup_semaphore) for doi in selected_dois),
            return_exceptions=True,
        )
        by_doi: dict[str, tuple[str, dict[str, Any]]] = {}
        warnings: list[str] = []
        for doi, result in zip(selected_dois, results, strict=True):
            if isinstance(result, Exception):
                warnings.append(f"DOI OA enrichment failed ({doi}): {self._format_exception(result)}")
                continue
            by_doi[doi] = result

        for record in records:
            doi = self._doi_for_oa_lookup(record.doi)
            if not doi:
                continue
            enrichment = by_doi.get(doi)
            if not enrichment:
                continue
            source, payload = enrichment
            if source == "unpaywall":
                best = payload.get("best_oa_location") or {}
                record.pdf_url = record.pdf_url or best.get("url_for_pdf") or best.get("url")
                record.url = record.url or best.get("url_for_landing_page") or payload.get("doi_url")
                record.is_open_access = record.is_open_access or bool(payload.get("is_oa") or record.pdf_url)
                record.raw = {
                    **record.raw,
                    "unpaywall": {
                        "oa_status": payload.get("oa_status"),
                        "host_type": best.get("host_type"),
                        "license": best.get("license"),
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
            record.pdf_url = record.pdf_url or openalex_record.pdf_url
            record.citation_count = max(record.citation_count or 0, openalex_record.citation_count or 0) or None
            record.is_open_access = record.is_open_access or openalex_record.is_open_access or bool(record.pdf_url)
            record.keywords = sorted(set(record.keywords + openalex_record.keywords))
            record.raw = {
                **record.raw,
                "openalex_doi_lookup": {
                    "id": payload.get("id"),
                    "oa_status": ((payload.get("open_access") or {}).get("oa_status")),
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
    ) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            unpaywall_error: Exception | None = None
            try:
                return "unpaywall", await self._lookup_unpaywall(client, doi)
            except Exception as exc:
                unpaywall_error = exc

            try:
                return "openalex", await self._lookup_openalex_by_doi(client, doi)
            except Exception as fallback_exc:
                raise RuntimeError(
                    "unpaywall="
                    f"{self._format_exception(unpaywall_error)}; "
                    "openalex="
                    f"{self._format_exception(fallback_exc)}"
                ) from fallback_exc

    async def _lookup_unpaywall(self, client: httpx.AsyncClient, doi: str) -> dict[str, Any]:
        try:
            return await self._get_json(
                client,
                f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
                {"email": self.settings.unpaywall_email},
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
            {"email": self.settings.unpaywall_email},
            "unpaywall_lookup",
            timeout=httpx.Timeout(10.0, connect=3.0),
        )

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
        message = str(exc).strip()
        if message:
            return message
        return repr(exc)

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
            response = await client.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            self._write_cache(cache_file, payload)
            return payload
        except Exception:
            if cached:
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
            if cached:
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
