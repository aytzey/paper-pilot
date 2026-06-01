from __future__ import annotations

import asyncio
import json
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from pyzotero import zotero as pyzotero

from paper_pilot.config import Settings
from paper_pilot.models import DownloadedDocument, PaperRecord, normalize_doi, normalize_title
from paper_pilot.services.reporting import ReportService


class ZoteroService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "enabled": self.settings.zotero_enabled,
            "mode": self.settings.zotero_mode,
            "library_type": self.settings.zotero_library_type,
            "library_id": self.settings.effective_zotero_library_id,
        }
        if self.settings.zotero_mode == "local":
            status["connector_url"] = self.settings.zotero_connector_url
            status["bridge_url"] = self.settings.zotero_bridge_url
            status["data_dir"] = str(self._zotero_data_dir())

            api_check = self._local_api_check()
            status["local_api_reachable"] = api_check["reachable"]
            if not api_check["reachable"] and "remediation" in api_check:
                status["local_api_error"] = api_check.get("error")
                status["local_api_remediation"] = api_check["remediation"]

            bridge = self._bridge_status()
            status["bridge_reachable"] = bridge["reachable"]
            if bridge.get("version"):
                status["bridge_version"] = bridge["version"]
            local_writes_supported = self.settings.zotero_library_type == "user"
            status["write_capability"] = (
                "full"
                if local_writes_supported and api_check["reachable"] and bridge["reachable"]
                else "metadata-only"
            )
            if not bridge["reachable"]:
                if bridge.get("error"):
                    status["bridge_error"] = bridge["error"]
                status["write_note"] = (
                    "Full local sync requires a Zotero bridge plugin that exposes /execute. "
                    "zoty-bridge is compatible."
                )
                if "remediation" in bridge:
                    status["bridge_remediation"] = bridge["remediation"]
        return status

    def _client(self) -> pyzotero.Zotero:
        library_id = self.settings.effective_zotero_library_id
        if self.settings.zotero_mode == "local":
            if not library_id:
                raise RuntimeError(
                    "Library ID could not be resolved for local Zotero. "
                    "`ZOTERO_LOCAL=true` is sufficient for user libraries; set `ZOTERO_LIBRARY_ID` for group libraries."
                )
            return pyzotero.Zotero(
                library_id,
                self.settings.zotero_library_type,
                self.settings.zotero_api_key,
                local=True,
            )
        if self.settings.zotero_mode != "web":
            raise RuntimeError(
                "Zotero is disabled. Set `ZOTERO_LIBRARY_ID` + `ZOTERO_API_KEY` for web mode, "
                "or `ZOTERO_LOCAL=true` for local mode."
            )
        return pyzotero.Zotero(
            library_id,
            self.settings.zotero_library_type,
            self.settings.zotero_api_key,
        )

    def _local_api_reachable(self) -> bool:
        return self._local_api_check()["reachable"]

    def _local_api_check(self) -> dict[str, Any]:
        if self.settings.zotero_mode != "local":
            return {"reachable": False, "error": "not_local_mode"}
        library_scope = "users" if self.settings.zotero_library_type == "user" else "groups"
        library_id = self.settings.effective_zotero_library_id
        if self.settings.zotero_library_type == "group" and not library_id:
            return {
                "reachable": False,
                "error": "not_configured",
                "remediation": (
                    "Local group libraries require ZOTERO_LIBRARY_ID. "
                    "Set it to the target group library ID and retry."
                ),
            }
        library_id = library_id or "0"
        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get(
                    f"http://127.0.0.1:23119/api/{library_scope}/{library_id}/collections",
                    params={"limit": 1},
                )
                if response.is_success:
                    return {"reachable": True}
                return {
                    "reachable": False,
                    "error": "api_error",
                    "status_code": response.status_code,
                    "remediation": (
                        "Zotero local API returned an error. "
                        "Ensure extensions.zotero.httpServer.localAPI.enabled is set to true "
                        "in Zotero advanced preferences."
                    ),
                }
        except httpx.ConnectError:
            return {
                "reachable": False,
                "error": "connection_refused",
                "remediation": (
                    "Cannot connect to Zotero on port 23119. "
                    "Make sure Zotero is running on this machine."
                ),
            }
        except httpx.TimeoutException:
            return {
                "reachable": False,
                "error": "timeout",
                "remediation": (
                    "Connection to Zotero timed out. "
                    "Zotero may be busy or the local API may be disabled."
                ),
            }
        except Exception as exc:
            return {
                "reachable": False,
                "error": "unknown",
                "detail": str(exc),
                "remediation": "An unexpected error occurred while contacting Zotero.",
            }

    def _bridge_status(self) -> dict[str, Any]:
        if not self.settings.zotero_bridge_url:
            return {
                "reachable": False,
                "error": "not_configured",
                "remediation": (
                    "ZOTERO_BRIDGE_URL is not set. "
                    "Install a bridge plugin like zoty-bridge and set "
                    "ZOTERO_BRIDGE_URL=http://127.0.0.1:24119 to enable full write support."
                ),
            }
        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get(urljoin(self.settings.zotero_bridge_url.rstrip("/") + "/", "status"))
                response.raise_for_status()
                payload = response.json()
            return {
                "reachable": True,
                "version": payload.get("version"),
            }
        except httpx.ConnectError:
            return {
                "reachable": False,
                "error": "connection_refused",
                "remediation": (
                    f"Cannot reach bridge at {self.settings.zotero_bridge_url}. "
                    "Ensure Zotero is running and the bridge plugin (e.g. zoty-bridge) is installed and active."
                ),
            }
        except httpx.TimeoutException:
            return {
                "reachable": False,
                "error": "timeout",
                "remediation": (
                    f"Connection to bridge at {self.settings.zotero_bridge_url} timed out. "
                    "The bridge plugin may be unresponsive."
                ),
            }
        except Exception as exc:
            return {
                "reachable": False,
                "error": "unknown",
                "detail": str(exc),
                "remediation": (
                    f"Unexpected error contacting bridge at {self.settings.zotero_bridge_url}."
                ),
            }

    def _require_local_write_support(self) -> None:
        if self.settings.zotero_mode != "local":
            return
        if self.settings.zotero_library_type != "user":
            raise RuntimeError("Local bridge writes are currently supported only for `user` libraries.")
        api_check = self._local_api_check()
        if not api_check["reachable"]:
            raise RuntimeError(
                api_check.get("remediation", "Local Zotero API is not reachable.")
            )
        if not self.settings.zotero_bridge_url:
            raise RuntimeError(
                "Local Zotero writes require `ZOTERO_BRIDGE_URL`. Enable the `/execute` endpoint "
                "with a plugin like `zoty-bridge`."
            )
        if not self._bridge_status()["reachable"]:
            raise RuntimeError(
                "Cannot reach the local Zotero bridge endpoint. Zotero must be running with "
                "`extensions.zotero.httpServer.localAPI.enabled=true`. "
                "For full local sync, a plugin like `zoty-bridge` must also be active."
            )

    async def list_collections(self, query: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_collections_sync, query)

    def _list_collections_sync(self, query: str | None = None) -> list[dict[str, Any]]:
        client = self._client()
        collections = client.everything(client.collections())
        items = [
            {
                "key": collection.get("key"),
                "name": collection.get("data", {}).get("name"),
                "parentCollection": collection.get("data", {}).get("parentCollection"),
            }
            for collection in collections
        ]
        if query:
            lowered = query.lower()
            items = [item for item in items if lowered in (item.get("name") or "").lower()]
        return items

    async def resolve_collection(
        self,
        existing_collection_key: str | None,
        existing_collection_name: str | None,
        create_collection_name: str | None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._resolve_collection_sync,
            existing_collection_key,
            existing_collection_name,
            create_collection_name,
        )

    def _resolve_collection_sync(
        self,
        existing_collection_key: str | None,
        existing_collection_name: str | None,
        create_collection_name: str | None,
    ) -> dict[str, Any]:
        collections = self._list_collections_sync()
        if existing_collection_key:
            for collection in collections:
                if collection.get("key") == existing_collection_key:
                    return {"key": collection["key"], "name": collection.get("name"), "created": False}
            raise RuntimeError(f"Zotero collection not found: {existing_collection_key}")

        if existing_collection_name:
            lowered = existing_collection_name.lower().strip()
            for collection in collections:
                name = (collection.get("name") or "").lower().strip()
                if name == lowered:
                    return {"key": collection["key"], "name": collection.get("name"), "created": False}

        target_name = create_collection_name or existing_collection_name
        if not target_name:
            raise RuntimeError("An existing collection key/name or a new collection name is required for writes.")

        if self.settings.zotero_mode == "local":
            self._require_local_write_support()
            return self._create_collection_via_bridge(target_name)

        client = self._client()
        response = client.create_collections([{"name": target_name}])
        new_key = next(iter((response.get("success") or {}).values()), None)
        if not new_key:
            raise RuntimeError(f"Failed to create collection: {response}")
        return {"key": new_key, "name": target_name, "created": True}

    async def sync_topic(
        self,
        collection_key: str,
        papers: list[PaperRecord],
        downloads: list[DownloadedDocument],
        report_markdown: str,
        topic: str,
        attach_pdfs: bool = True,
    ) -> dict[str, Any]:
        if self.settings.zotero_mode == "local":
            return await asyncio.to_thread(
                self._sync_topic_local_sync,
                collection_key,
                papers,
                downloads,
                report_markdown,
                topic,
                attach_pdfs,
            )
        return await asyncio.to_thread(
            self._sync_topic_web_sync,
            collection_key,
            papers,
            downloads,
            report_markdown,
            topic,
            attach_pdfs,
        )

    def _sync_topic_web_sync(
        self,
        collection_key: str,
        papers: list[PaperRecord],
        downloads: list[DownloadedDocument],
        report_markdown: str,
        topic: str,
        attach_pdfs: bool,
    ) -> dict[str, Any]:
        client = self._client()
        download_map = {document.paper.dedupe_key(): document for document in downloads}
        created_items: list[str] = []
        reused_items: list[str] = []
        failed_items: list[str] = []

        for paper in papers:
            try:
                existing = self._find_existing_item(client, paper)
                if existing:
                    client.addto_collection(collection_key, existing)
                    reused_items.append(existing.get("key") or existing.get("data", {}).get("key"))
                    continue

                item_type = "preprint" if paper.source == "arxiv" else "journalArticle"
                template = client.item_template(item_type)
                template["title"] = paper.title
                template["collections"] = [collection_key]
                template["creators"] = [self._author_to_creator(author) for author in paper.authors] or []
                template["abstractNote"] = paper.abstract or ""
                template["date"] = str(paper.year) if paper.year else ""
                template["url"] = paper.url or ""
                template["tags"] = [{"tag": f"topic:{topic}"}]
                if paper.doi:
                    template["DOI"] = paper.doi
                if paper.venue:
                    template["publicationTitle"] = paper.venue
                template["extra"] = f"Imported by paper-pilot\nSource: {paper.source}\nSource ID: {paper.source_id}"

                response = client.create_items([template])
                item_key = next(iter((response.get("success") or {}).values()), None)
                if not item_key:
                    raise RuntimeError(f"Failed to create Zotero item: {response}")
                created_items.append(item_key)

                if attach_pdfs and paper.dedupe_key() in download_map:
                    client.attachment_simple([str(download_map[paper.dedupe_key()].path)], parentid=item_key)
            except Exception as exc:  # keep syncing the rest; report what failed
                failed_items.append(f"{paper.title}: {exc}")

        note_template = client.item_template("note")
        note_template["collections"] = [collection_key]
        note_template["tags"] = [{"tag": f"topic:{topic}"}, {"tag": "research-report"}]
        note_template["note"] = ReportService.markdown_to_note_html(report_markdown)
        note_response = client.create_items([note_template])
        note_key = next(iter((note_response.get("success") or {}).values()), None)

        return {
            "collection_key": collection_key,
            "created_item_keys": created_items,
            "reused_item_keys": reused_items,
            "failed_items": failed_items,
            "note_key": note_key,
            "mode": "web",
        }

    def _sync_topic_local_sync(
        self,
        collection_key: str,
        papers: list[PaperRecord],
        downloads: list[DownloadedDocument],
        report_markdown: str,
        topic: str,
        attach_pdfs: bool,
    ) -> dict[str, Any]:
        self._require_local_write_support()
        client = self._client()
        download_map = {document.paper.dedupe_key(): document for document in downloads}
        created_items: list[str] = []
        reused_items: list[str] = []
        failed_items: list[str] = []

        for paper in papers:
            try:
                existing = self._find_existing_item(client, paper)
                if existing:
                    item_key = existing.get("key") or existing.get("data", {}).get("key")
                    if not item_key:
                        raise RuntimeError(f"Could not resolve key for existing Zotero item: {paper.title}")
                    self._add_item_to_collection_via_bridge(item_key, collection_key)
                    reused_items.append(item_key)
                    if attach_pdfs and paper.dedupe_key() in download_map:
                        self._attach_pdf_via_bridge(item_key, download_map[paper.dedupe_key()].path)
                    continue

                connector_item = self._connector_item_for_paper(paper, topic)
                self._push_to_connector(connector_item, paper.url or (f"https://doi.org/{paper.doi}" if paper.doi else ""))
                created = self._wait_for_item(client, paper)
                if not created:
                    raise RuntimeError(f"Local Zotero item key not found: {paper.title}")
                item_key = created.get("key") or created.get("data", {}).get("key")
                if not item_key:
                    raise RuntimeError(f"Local Zotero item key not found: {paper.title}")

                self._add_item_to_collection_via_bridge(item_key, collection_key)
                if attach_pdfs and paper.dedupe_key() in download_map:
                    self._attach_pdf_via_bridge(item_key, download_map[paper.dedupe_key()].path)
                created_items.append(item_key)
            except Exception as exc:  # keep syncing the rest; report what failed
                failed_items.append(f"{paper.title}: {exc}")

        note_key = self._create_note_via_bridge(collection_key, report_markdown, topic)
        return {
            "collection_key": collection_key,
            "created_item_keys": created_items,
            "reused_item_keys": reused_items,
            "failed_items": failed_items,
            "note_key": note_key,
            "mode": "local",
        }

    def _connector_item_for_paper(self, paper: PaperRecord, topic: str) -> dict[str, Any]:
        is_arxiv = paper.source == "arxiv" or (paper.url and "arxiv.org" in paper.url)
        item: dict[str, Any] = {
            "itemType": "preprint" if is_arxiv else "journalArticle",
            "title": paper.title,
            "creators": [self._author_to_creator(author) for author in paper.authors] or [],
            "abstractNote": paper.abstract or "",
            "date": str(paper.year) if paper.year else "",
            "url": paper.url or "",
            "tags": [{"tag": f"topic:{topic}"}, {"tag": f"source:{paper.source}"}],
            "extra": f"Imported by paper-pilot\nSource: {paper.source}\nSource ID: {paper.source_id}",
        }
        if paper.doi:
            item["DOI"] = paper.doi
        if paper.venue:
            item["publicationTitle"] = paper.venue
        if is_arxiv:
            archive_id = paper.source_id
            if archive_id.startswith("http"):
                archive_id = archive_id.rstrip("/").split("/")[-1]
            archive_id = archive_id.replace("arXiv:", "").replace(".pdf", "")
            item["archive"] = "arXiv"
            item["archiveID"] = f"arXiv:{archive_id}"
        return item

    def _push_to_connector(self, item: dict[str, Any], source_url: str) -> None:
        payload = {"items": [{k: v for k, v in item.items() if not k.startswith("_")}], "uri": source_url}
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                self.settings.zotero_connector_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Zotero-Allowed-Request": "true",
                },
            )
            if response.status_code not in {200, 201}:
                raise RuntimeError(f"Zotero connector failed to create item: {response.status_code} {response.text}")

    def _bridge_execute(self, code: str) -> dict[str, Any]:
        if not self.settings.zotero_bridge_url:
            raise RuntimeError("Zotero bridge URL is not configured.")
        payload_raw = json.dumps({"code": code}).encode("utf-8")
        request = urllib.request.Request(
            urljoin(self.settings.zotero_bridge_url.rstrip("/") + "/", "execute"),
            data=payload_raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(f"Zotero bridge JS error: {payload.get('error', 'unknown')}")
        result = payload.get("result")
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                return {"raw": result}
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        if isinstance(result, dict):
            return result
        return payload

    def _create_collection_via_bridge(self, name: str) -> dict[str, Any]:
        result = self._bridge_execute(
            f"""return (async () => {{
    const collection = new Zotero.Collection;
    collection.libraryID = 1;
    collection.name = {json.dumps(name)};
    await collection.saveTx();
    return JSON.stringify({{key: collection.key, name: collection.name, created: true}});
}})();"""
        )
        if not result.get("key"):
            raise RuntimeError(f"Failed to create local Zotero collection: {result}")
        return result

    def _add_item_to_collection_via_bridge(self, item_key: str, collection_key: str) -> None:
        result = self._bridge_execute(
            f"""return (async () => {{
    const item = await Zotero.Items.getByLibraryAndKey(1, {json.dumps(item_key)});
    const collection = await Zotero.Collections.getByLibraryAndKey(1, {json.dumps(collection_key)});
    if (!item || !collection) {{
        return JSON.stringify({{error: 'not found', itemKey: {json.dumps(item_key)}, collectionKey: {json.dumps(collection_key)}}});
    }}
    await Zotero.DB.executeTransaction(async () => {{
        await collection.addItem(item.id);
    }});
    return JSON.stringify({{status: 'added'}});
}})();"""
        )
        if result.get("error"):
            raise RuntimeError(f"Failed to add item to local Zotero collection: {result}")

    def _attach_pdf_via_bridge(self, item_key: str, path: Path) -> None:
        staged_path = self._stage_path_for_local_zotero(path)
        result = self._bridge_execute(
            f"""return (async () => {{
    const item = await Zotero.Items.getByLibraryAndKey(1, {json.dumps(item_key)});
    if (!item) {{
        return JSON.stringify({{error: 'parent item not found', itemKey: {json.dumps(item_key)}}});
    }}
    const attachment = await Zotero.Attachments.importFromFile({{
        file: {json.dumps(str(staged_path))},
        parentItemID: item.id
    }});
    return JSON.stringify({{status: 'attached', key: attachment.key}});
}})();"""
        )
        if result.get("error"):
            raise RuntimeError(f"Failed to attach PDF to local Zotero item: {result}")
        if staged_path != path and staged_path.exists():
            staged_path.unlink(missing_ok=True)

    def _create_note_via_bridge(self, collection_key: str, report_markdown: str, topic: str) -> str | None:
        note_html = ReportService.markdown_to_note_html(report_markdown)
        result = self._bridge_execute(
            f"""return (async () => {{
    const collection = await Zotero.Collections.getByLibraryAndKey(1, {json.dumps(collection_key)});
    const note = new Zotero.Item('note');
    note.libraryID = 1;
    note.setNote({json.dumps(note_html)});
    note.addTag({json.dumps(f"topic:{topic}")});
    note.addTag('research-report');
    await note.saveTx();
    if (collection) {{
        await Zotero.DB.executeTransaction(async () => {{
            await collection.addItem(note.id);
        }});
    }}
    return JSON.stringify({{key: note.key}});
}})();"""
        )
        return result.get("key")

    def _wait_for_item(
        self,
        client: pyzotero.Zotero,
        paper: PaperRecord,
        timeout_sec: float = 6.0,
        poll_sec: float = 0.35,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            match = self._find_existing_item(client, paper)
            if match:
                return match
            time.sleep(poll_sec)
        return None

    def _zotero_data_dir(self) -> Path:
        """The Zotero data directory (default ~/Zotero), overridable via ZOTERO_DATA_DIR.

        Cross-platform via pathlib: ~/Zotero resolves to C:\\Users\\<you>\\Zotero on Windows,
        /Users/<you>/Zotero on macOS, /home/<you>/Zotero on Linux. Set ZOTERO_DATA_DIR if you
        relocated Zotero's data directory or run a sandboxed (e.g. Flatpak) install.
        """
        if self.settings.zotero_data_dir:
            return Path(self.settings.zotero_data_dir).expanduser().resolve()
        return (Path.home() / "Zotero").resolve()

    def _stage_path_for_local_zotero(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        data_dir = self._zotero_data_dir()
        # If the file already lives where Zotero can read it (its data dir or the user's home),
        # import it in place; otherwise copy it into a staging folder Zotero can reach.
        for root in (data_dir, Path.home().resolve()):
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        staging_dir = data_dir / ".paper-pilot-staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged = staging_dir / resolved.name
        shutil.copy2(resolved, staged)
        return staged

    def _find_existing_item(self, client: pyzotero.Zotero, paper: PaperRecord) -> dict[str, Any] | None:
        queries = [value for value in (paper.doi, paper.title) if value]
        for query in queries:
            matches = client.items(q=query, limit=15)
            for match in matches:
                data = match.get("data", {})
                if paper.doi and normalize_doi(data.get("DOI")) == normalize_doi(paper.doi):
                    return match
                if normalize_title(data.get("title")) == normalize_title(paper.title):
                    return match
        return None

    @staticmethod
    def _author_to_creator(author: str) -> dict[str, str]:
        parts = author.strip().split()
        if len(parts) <= 1:
            return {"creatorType": "author", "name": author.strip()}
        return {
            "creatorType": "author",
            "firstName": " ".join(parts[:-1]),
            "lastName": parts[-1],
        }
