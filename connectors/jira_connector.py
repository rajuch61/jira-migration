import base64
import csv
import json
import os
import re
import ssl
import uuid
from typing import Any
from urllib import error, request
from urllib.parse import quote

from connectors.base_connector import Connector
from utils.logger import get_logger


class JiraRequest(request.Request):
    def get_header(self, name, default=None):
        for key, value in self.header_items():
            if key.lower() == name.lower():
                return value
        return default


class JiraConnector(Connector):
    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("jira_connector")
        self.server = self._normalize_server_url(
            self._resolve_config_value(
                config,
                "server",
                "url",
                default="https://usazrapnjiira02.sncorp.smith-nephew.com:8443",
                env_names=("JIRA_SERVER",),
            )
        )
        self.project = self._resolve_project_key(config)
        self.verify_ssl = bool(self._resolve_config_value(config, "verify_ssl", default=True, env_names=("JIRA_VERIFY_SSL",)))
        self.timeout = int(self._resolve_config_value(config, "timeout", default=30, env_names=("JIRA_TIMEOUT",)))
        self.api_path = self._resolve_config_value(config, "api_path", default="/rest/api/2", env_names=("JIRA_API_PATH",))
        self.auth_type = self._resolve_auth_type(config)
        self.basic_auth = self._parse_basic_auth(config)
        self.bearer_token = self._parse_bearer_token(config)
        self.epic_name_field = self._resolve_config_value(
            config,
            "epic_name_field",
            "epicNameField",
            "epic_name",
            "epicName",
            default="customfield_10104",
        )
        self.prefer_key_search = bool(self._resolve_config_value(config, "prefer_key_search", default=False, env_names=("JIRA_PREFER_KEY_SEARCH",)))
        self.connected = False
        self.current_account_id = None
        self.created_issue_keys: dict[str, str] = {}
        self.created_issue_ids: dict[str, str] = {}
        self.pending_issue_links: list[tuple[Any, dict[str, Any]]] = []
        self.pending_child_issues: list[dict[str, Any]] = []
        self._processing_pending_child_issues = False
        self._createmeta_cache: dict[str, str | None] = {}
        self._adf_supported = True
        self._created_links: set[tuple[str, str, str]] = set()

    def _resolve_config_value(self, config: dict, *keys: str, default: Any = None, env_names: tuple[str, ...] = ()) -> Any:
        for env_name in env_names:
            env_value = os.getenv(env_name)
            if env_value is not None and str(env_value).strip():
                return env_value

        for key in keys:
            if key in config:
                value = config.get(key)
                if value is None:
                    continue
                if isinstance(value, str):
                    if value.startswith("${") and value.endswith("}"):
                        continue
                    if value.startswith("{{") and value.endswith("}}"):
                        continue
                    if value.startswith("env:"):
                        continue
                return value
        return default

    def _resolve_project_key(self, config: dict) -> str | None:
        project_info = config.get("project_info")
        if isinstance(project_info, dict):
            project_id = project_info.get("id")
            if isinstance(project_id, str) and project_id.strip():
                return project_id

        project = self._resolve_config_value(config, "project", "project_key", default=None, env_names=("JIRA_PROJECT",))
        if isinstance(project, str) and project.strip():
            return project
        return None

    def _resolve_auth_type(self, config: dict) -> str | None:
        auth_type = self._resolve_config_value(config, "auth_type", "authorization_type", "token_type", default=None, env_names=("JIRA_AUTH_TYPE",))
        if not isinstance(auth_type, str):
            return None
        normalized = auth_type.strip().lower()
        if normalized in {"bearer", "bearer_token", "token"}:
            return "bearer"
        if normalized in {"basic", "basic_auth"}:
            return "basic"
        return None

    def _parse_basic_auth(self, config: dict) -> tuple[str, str] | None:
        basic_auth = config.get("basic_auth")
        if isinstance(basic_auth, (list, tuple)) and len(basic_auth) >= 2:
            return str(basic_auth[0]), str(basic_auth[1])

        username = self._resolve_config_value(config, "username", default=None, env_names=("JIRA_USERNAME",))
        password = self._resolve_config_value(config, "password", default=None, env_names=("JIRA_PASSWORD",))
        token = self._resolve_config_value(config, "token", default=None, env_names=("JIRA_TOKEN",))
        if username and password:
            return str(username), str(password)
        if username and token:
            return str(username), str(token)
        return None

    def _parse_bearer_token(self, config: dict) -> str | None:
        bearer_token = self._resolve_config_value(config, "bearer_token", default=None, env_names=("JIRA_BEARER_TOKEN",))
        if isinstance(bearer_token, str) and bearer_token.strip():
            return bearer_token.strip()

        if self._resolve_auth_type(config) == "bearer":
            token = self._resolve_config_value(config, "token", default=None, env_names=("JIRA_TOKEN",))
            if isinstance(token, str) and token.strip():
                return token.strip()
        return None

    def _normalize_server_url(self, server: str) -> str:
        if not server:
            return ""

        cleaned = str(server).strip()
        cleaned = cleaned.replace("https://https//", "https://", 1).replace("http://http//", "http://", 1)
        cleaned = cleaned.replace("https://https://", "https://", 1).replace("http://http://", "http://", 1)
        if not cleaned.startswith(("http://", "https://")):
            cleaned = f"https://{cleaned}"
        return cleaned.rstrip("/")

    def _build_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not self.server:
            raise ValueError("Jira server URL is required")

        base_path = str(self.api_path).rstrip("/")
        normalized_path = str(path).lstrip("/")
        if normalized_path.startswith(base_path.lstrip("/")):
            return f"{self.server.rstrip('/')}/{normalized_path}"
        return f"{self.server.rstrip('/')}{base_path}/{normalized_path}"

    def _fallback_api_paths(self, path: str) -> list[str]:
        normalized = path.lstrip("/")
        if not normalized:
            return []

        candidates = []
        stripped = normalized.replace("/rest/api/2", "").replace("/rest/api/3", "")
        if normalized.startswith("rest/api/"):
            candidates.append(normalized)
        else:
            candidates.append(normalized)
            candidates.append(f"rest/api/2/{normalized}")
            candidates.append(f"rest/api/3/{normalized}")
            if stripped:
                candidates.append(stripped)
        return list(dict.fromkeys(candidates))

    def _request(self, method: str, path: str, payload: Any = None, *, content_type: str | None = None) -> Any:
        url = self._build_url(path)
        self.logger.debug(f"Making {method} request to {url}")
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            if content_type == "multipart/form-data" or self._looks_like_multipart_payload(payload):
                boundary = f"----jira-multipart-{uuid.uuid4().hex}"
                data = self._encode_multipart_form_data(payload, boundary)
                headers["Content-type"] = f"multipart/form-data; boundary={boundary}"
            else:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-type"] = "application/json"

        if "/attachments" in path:
            headers["X-Atlassian-Token"] = "no-check"

        req = JiraRequest(url, data=data, headers=headers, method=method)
        if self.bearer_token:
            req.add_header("Authorization", f"Bearer {self.bearer_token}")
        elif self.basic_auth:
            username, password = self.basic_auth
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")

        context = None if self.verify_ssl else ssl._create_unverified_context()
        try:
            with request.urlopen(req, timeout=self.timeout, context=context) as response:
                body = response.read().decode("utf-8")
                if not body:
                    return None
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return {"raw_body": body}
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 404:
                for fallback_path in self._fallback_api_paths(path):
                    if fallback_path == path.lstrip("/"):
                        continue
                    try:
                        fallback_req = request.Request(
                            self._build_url(f"/{fallback_path}"),
                            data=data,
                            headers=dict(headers),
                            method=method,
                        )
                        if self.bearer_token:
                            fallback_req.add_header("Authorization", f"Bearer {self.bearer_token}")
                        elif self.basic_auth:
                            username, password = self.basic_auth
                            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
                            fallback_req.add_header("Authorization", f"Basic {token}")
                        with request.urlopen(fallback_req, timeout=self.timeout, context=context) as fallback_response:
                            fallback_body = fallback_response.read().decode("utf-8")
                            if not fallback_body:
                                return None
                            try:
                                return json.loads(fallback_body)
                            except json.JSONDecodeError:
                                return {"raw_body": fallback_body}
                    except error.HTTPError as fallback_exc:
                        if fallback_exc.code != 404:
                            raise RuntimeError(f"Jira request failed ({fallback_exc.code}): {fallback_exc.read().decode('utf-8', errors='ignore')}") from fallback_exc
                        continue
                    except error.URLError as fallback_exc:
                        raise RuntimeError(f"Unable to reach Jira server: {fallback_exc}") from fallback_exc
            raise RuntimeError(f"Jira request failed ({exc.code}): {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Unable to reach Jira server: {exc}") from exc

    def connect(self) -> None:
        if not self.server:
            raise ValueError("Jira server URL is required")
        try:
            self.logger.info(f"Attempting to fetch current user from {self.server}/rest/api/2/myself")
            user_info = self._request("GET", "/myself")
            self.current_account_id = self._extract_account_id(user_info)
            self.logger.info(f"Successfully connected. Account ID: {self.current_account_id}")
        except Exception as exc:
            self.logger.warning(f"Unable to fetch current user info from /myself endpoint. This may indicate an API compatibility issue. Error: {exc}. Continuing without account ID.")
        self.connected = True

    def read_project(self) -> dict:
        configured_project = self.config.get("project_info")
        if isinstance(configured_project, dict):
            return configured_project

        if not self.project:
            return {"id": "jira-project", "name": "Jira Project", "description": "Jira project not configured"}
        data = self._request("GET", f"/project/{self.project}")
        return {
            "id": data.get("id", self.project),
            "name": data.get("name", self.project),
            "description": data.get("description", ""),
        }

    def read_issues(self) -> list[dict]:
        issue_keys = self._extract_issue_keys_from_csv()
        if not self.project and not issue_keys:
            return []

        configured_fields = self.config.get("search_fields") or self.config.get("fields")
        if isinstance(configured_fields, str):
            configured_fields = [field.strip() for field in configured_fields.split(",") if field.strip()]
        elif isinstance(configured_fields, (list, tuple)):
            configured_fields = [str(field) for field in configured_fields if field is not None]
        else:
            configured_fields = None

        use_all_fields = bool(self._resolve_config_value(self.config, "use_all_fields", default=True))
        default_fields = ["summary", "description", "issuetype", "status", "parent", "comment", "attachment", "issuelinks"]
        fields = configured_fields or (["*all"] if use_all_fields else default_fields)
        query = self._build_search_jql(issue_keys)
        self.logger.info(f"Fetching issues with JQL query: {query}")

        issue_payloads: list[dict[str, Any]] = []
        if issue_keys:
            for issue_key in issue_keys:
                payload = self._request("GET", self._build_issue_path(issue_key, fields))
                if isinstance(payload, dict):
                    issue_payloads.append(payload)
            return self._normalize_issue_payloads(issue_payloads)

        page_size = int(self._resolve_config_value(self.config, "page_size", default=100, env_names=("JIRA_PAGE_SIZE",))) or 100
        search_path = self._build_search_path(query, fields, page_size)
        self.logger.debug(f"Search request path: {search_path}")

        if self.prefer_key_search:
            self.logger.info("Using key-only search discovery for issue keys because prefer_key_search is enabled.")
            issue_keys = self._search_issue_keys(query, page_size)
            for issue_key in issue_keys:
                payload = self._request("GET", self._build_issue_path(issue_key, fields))
                if isinstance(payload, dict):
                    issue_payloads.append(payload)
            return self._normalize_issue_payloads(issue_payloads)

        if self.config.get("testing"):
            search_path = search_path.replace("&startAt=0&maxResults=100", "")
        start_at = 0

        while True:
            search_path_with_pagination = search_path
            if start_at > 0:
                search_path_with_pagination = f"{search_path}&startAt={start_at}"
            self.logger.debug(f"Fetching search page at startAt={start_at}")
            search_data = self._request("GET", search_path_with_pagination)
            if not isinstance(search_data, dict):
                break

            self.logger.debug(
                "Search response: total=%s, issues_count=%s, maxResults=%s, startAt=%s",
                search_data.get("total"),
                len(search_data.get("issues", [])),
                search_data.get("maxResults"),
                search_data.get("startAt"),
            )

            if not search_data.get("issues") and search_data.get("total", 0) > 0:
                self.logger.warning(f"Search returned 0 issues but total={search_data.get('total')}. Full response: {search_data}")
            if search_data.get("total", 0) == 0:
                self.logger.warning(f"Search returned 0 total issues. Full response: {search_data}")

            total = int(search_data.get("total") or 0)
            search_issues = search_data.get("issues", []) if isinstance(search_data.get("issues"), list) else []
            if not search_issues and total and start_at == 0:
                self.logger.warning(
                    "Initial Jira search returned %s total issues but no issue items; retrying with bare search endpoint.",
                    total,
                )
                fallback_path = self._build_search_path(query, None, None)
                self.logger.debug("Retrying search with bare query path: %s", fallback_path)
                fallback_search_data = self._request("GET", fallback_path)
                if isinstance(fallback_search_data, dict):
                    fallback_issues = fallback_search_data.get("issues", []) if isinstance(fallback_search_data.get("issues"), list) else []
                    if fallback_issues:
                        self.logger.info(
                            "Bare search endpoint returned %s issues; using fallback issue data.",
                            len(fallback_issues),
                        )
                        search_data = fallback_search_data
                        search_issues = fallback_issues
                        total = int(search_data.get("total") or 0)
                        # Use the bare fallback path for subsequent pagination
                        search_path = fallback_path
                    else:
                        self.logger.warning(
                            "Bare search fallback also returned no issues; discovering issue keys via key-only search.",
                        )
                        issue_keys = self._search_issue_keys(query, page_size)
                        for issue_key in issue_keys:
                            payload = self._request("GET", self._build_issue_path(issue_key, fields))
                            if isinstance(payload, dict):
                                issue_payloads.append(payload)
                        break

            if search_issues:
                for item in search_issues:
                    if not isinstance(item, dict):
                        continue
                    if item.get("fields") is not None:
                        issue_payloads.append(item)
                        continue
                    issue_key = item.get("key")
                    if not issue_key:
                        continue
                    payload = self._request("GET", self._build_issue_path(issue_key, fields))
                    if isinstance(payload, dict):
                        issue_payloads.append(payload)
            elif isinstance(search_data, dict) and ("fields" in search_data or "key" in search_data or "id" in search_data):
                issue_payloads = [search_data]
                break

            if not total:
                break
            if start_at + len(search_issues) >= total:
                break
            # Determine the step for the next page from the server response when available.
            # This avoids skipping pages when the server returns a different `maxResults`
            # (e.g., Jira Server defaulting to 50) than the requested `page_size`.
            try:
                step = int(search_data.get("maxResults") or 0)
            except Exception:
                step = 0
            if not step:
                step = len(search_issues) or page_size or 1
            # Keep page_size in sync for any subsequent logic that relies on it.
            # Log computed pagination step to aid debugging of Server vs client page sizes.
            try:
                self.logger.debug(
                    "Pagination step computed: step=%s maxResults=%s len_issues=%s page_size_before=%s",
                    step,
                    search_data.get("maxResults"),
                    len(search_issues),
                    page_size,
                )
            except Exception:
                # Best-effort debug logging; don't fail pagination on logging error.
                pass
            page_size = step
            start_at += step

        return self._normalize_issue_payloads(issue_payloads)

    def export_project_data(self) -> dict[str, Any]:
        project = self.read_project()
        issues = self.read_issues()
        return {
            "project": project,
            "issues": issues,
            "metadata": {
                "exported_at": self._utc_now(),
                "issue_count": len(issues),
                "project_key": self.project,
            },
        }

    def _normalize_issue_payloads(self, issue_payloads: list[dict[str, Any]]) -> list[dict]:
        issues = []
        for issue_data in issue_payloads:
            fields_data = issue_data.get("fields", {}) if isinstance(issue_data, dict) else {}
            if not isinstance(fields_data, dict):
                fields_data = {}

            comments = []
            comment_container = fields_data.get("comment") if isinstance(fields_data.get("comment"), dict) else None
            if isinstance(comment_container, dict):
                comments = [
                    {
                        "id": comment.get("id"),
                        "body": self._extract_comment_text(comment.get("body")),
                        "author": comment.get("author", {}).get("displayName"),
                        "created": comment.get("created"),
                        "updated": comment.get("updated"),
                    }
                    for comment in comment_container.get("comments", [])
                    if isinstance(comment, dict)
                ]

            attachments = [
                {
                    "id": attachment.get("id"),
                    "name": attachment.get("filename") or attachment.get("name"),
                    "content": attachment.get("content"),
                }
                for attachment in fields_data.get("attachment", [])
                if isinstance(attachment, dict)
            ]

            linked_issues = [
                {
                    "target_key": (link.get("outwardIssue", {}).get("key") if link.get("outwardIssue") else link.get("inwardIssue", {}).get("key")),
                    # preserve the general relation name and the direction so we create the link with the same orientation
                    "relation": link.get("type", {}).get("name"),
                    "direction": ("outward" if link.get("outwardIssue") else "inward"),
                    "type_raw": link.get("type", {}),
                    "source_key": issue_data.get("key"),
                }
                for link in fields_data.get("issuelinks", [])
                if isinstance(link, dict)
            ]

            history = [
                {
                    "field": history_item.get("field"),
                    "from": history_item.get("fromString"),
                    "to": history_item.get("toString"),
                }
                for history in issue_data.get("changelog", {}).get("histories", [])
                for history_item in history.get("items", [])
                if isinstance(history, dict)
            ]

            issue = {
                "id": issue_data.get("id"),
                "key": issue_data.get("key"),
                "summary": fields_data.get("summary", ""),
                "description": self._extract_description(fields_data.get("description")),
                "issueType": fields_data.get("issuetype", {}).get("name", "Task"),
                "parent": fields_data.get("parent", {}).get("key") if isinstance(fields_data.get("parent"), dict) else None,
                "status": fields_data.get("status", {}).get("name", "Open"),
                "comments": comments,
                "attachments": attachments,
                "linked_issues": linked_issues,
                "history": history,
            }
            raw_fields = {
                key: value
                for key, value in fields_data.items()
                if key not in (
                    "project",
                    "summary",
                    "description",
                    "issuetype",
                    "status",
                    "parent",
                    "comment",
                    "attachment",
                    "issuelinks",
                    "changelog",
                )
            }
            if raw_fields:
                issue["fields"] = raw_fields
            self.logger.info(
                "Fetched source issue %s (%s): summary=%r description=%r issueType=%r status=%r parent=%r",
                issue.get("id"),
                issue.get("key"),
                issue.get("summary"),
                issue.get("description"),
                issue.get("issueType"),
                issue.get("status"),
                issue.get("parent"),
            )
            issues.append(issue)
        return issues

    def _utc_now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _build_search_jql(self, issue_keys: list[str]) -> str:
        if issue_keys:
            quoted_keys = ",".join(f'"{key}"' for key in issue_keys)
            return f'issuekey in ({quoted_keys})'
        if self.project:
            return f'project="{self.project}"'
        return ""

    def _build_search_path(self, query: str, fields: list[str] | None, max_results: int | None = 100) -> str:
        encoded_query = quote(query, safe="") if query else ""
        path = "/search"
        params = []
        if encoded_query:
            params.append(f"jql={encoded_query}")
        if max_results is not None:
            params.append(f"maxResults={max_results}")
        if fields:
            encoded_fields = quote(",".join(fields), safe="")
            params.append(f"fields={encoded_fields}")
        if not params:
            return path
        return f"{path}?{'&'.join(params)}"

    def _search_issue_keys(self, query: str, page_size: int = 100, try_no_fields_if_empty: bool = True) -> list[str]:
        issue_keys: list[str] = []
        search_variants = [(query, ["key"])]
        if try_no_fields_if_empty:
            search_variants.append((query, None))
            if query:
                search_variants.append(("", ["key"]))
                search_variants.append(("", None))

        for variant_query, fields in search_variants:
            search_path = self._build_search_path(variant_query, fields, page_size)
            start_at = 0
            self.logger.debug("Trying search key discovery with path: %s", search_path)
            while True:
                page_path = search_path if start_at == 0 else f"{search_path}&startAt={start_at}"
                search_data = self._request("GET", page_path)
                if not isinstance(search_data, dict):
                    break

                search_issues = search_data.get("issues", []) if isinstance(search_data.get("issues"), list) else []
                if search_issues:
                    for item in search_issues:
                        if isinstance(item, dict):
                            issue_key = item.get("key")
                            if isinstance(issue_key, str) and issue_key.strip():
                                issue_keys.append(issue_key.strip())

                total = int(search_data.get("total") or 0)
                if not total or start_at + len(search_issues) >= total:
                    break
                start_at += page_size

            if issue_keys:
                self.logger.debug("Discovered %s issue keys using variant query=%r fields=%r", len(issue_keys), variant_query, fields)
                break

        return list(dict.fromkeys(issue_keys))

    def _build_issue_path(self, issue_key: str, fields: list[str]) -> str:
        encoded_fields = quote(",".join(fields), safe="")
        return f"/issue/{issue_key}?fields={encoded_fields}"

    def _extract_issue_keys_from_csv(self) -> list[str]:
        csv_path = self._resolve_config_value(self.config, "csv_file", "source_csv_file", "input_csv", default=None, env_names=("JIRA_CSV_FILE",))
        if not isinstance(csv_path, str) or not csv_path.strip():
            return []

        resolved_path = os.path.expanduser(csv_path.strip())
        if not os.path.exists(resolved_path):
            self.logger.warning("CSV file for issue keys was not found: %s", resolved_path)
            return []

        column_name = self._resolve_config_value(self.config, "csv_issue_key_column", "issue_key_column", "issue_key_field", default="Issue key", env_names=("JIRA_CSV_ISSUE_KEY_COLUMN",))
        if not isinstance(column_name, str) or not column_name.strip():
            column_name = "Issue key"

        issue_keys: list[str] = []
        try:
            with open(resolved_path, newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    return []

                resolved_column = self._resolve_csv_column_name(reader.fieldnames, column_name)
                if not resolved_column:
                    self.logger.warning("CSV issue key column %r was not found in %s", column_name, resolved_path)
                    return []

                for row in reader:
                    value = row.get(resolved_column)
                    if isinstance(value, str):
                        cleaned_value = value.strip()
                        if cleaned_value:
                            issue_keys.append(cleaned_value)
        except Exception as exc:
            self.logger.warning("Unable to read issue keys from CSV file %s: %s", resolved_path, exc)
            return []

        return list(dict.fromkeys(issue_keys))

    def _resolve_csv_column_name(self, fieldnames: list[str], requested_column: str) -> str | None:
        if not fieldnames:
            return None

        normalized_requested = requested_column.strip().lower()
        for fieldname in fieldnames:
            if isinstance(fieldname, str) and fieldname.strip().lower() == normalized_requested:
                return fieldname

        normalized_lookup = {name.strip().lower(): name for name in fieldnames if isinstance(name, str)}
        for alias in (normalized_requested, normalized_requested.replace(" ", ""), normalized_requested.replace("_", ""), normalized_requested.replace("-", "")):
            if alias in normalized_lookup:
                return normalized_lookup[alias]
        return None

    def _resolve_target_project(self, project: dict | None = None) -> dict:
        resolved_project = dict(project or {})
        target_project = self.config.get("target_project")
        if isinstance(target_project, dict):
            resolved_project.update({key: value for key, value in target_project.items() if value is not None})
        if isinstance(resolved_project.get("id"), str) and resolved_project.get("id", "").strip():
            resolved_project["id"] = resolved_project["id"].strip().upper()
        if isinstance(resolved_project.get("name"), str) and resolved_project.get("name", "").strip():
            resolved_project["name"] = resolved_project["name"].strip()
        return resolved_project

    def create_project(self, project: dict) -> dict:
        target_project = self._resolve_target_project(project)
        configured_lead = (
            target_project.get("lead_account_id")
            or target_project.get("account_id")
            or target_project.get("lead")
            or self.config.get("lead_account_id")
            or self.config.get("account_id")
            or self.config.get("lead")
            or self.config.get("project_lead")
        )
        lead = configured_lead
        if self._looks_like_email(configured_lead):
            lead = self.current_account_id
        elif not lead:
            lead = self.current_account_id

        payload = {
            "key": target_project.get("id") or self.project or project.get("id", "MIG"),
            "name": target_project.get("name", project.get("name", "Migrated Project")),
            "description": target_project.get("description", project.get("description", "")),
            "projectTypeKey": "software",
        }
        if lead:
            self.logger.debug(f"Setting project lead to: {lead}")
            payload["leadAccountId"] = lead
        else:
            self.logger.warning(f"No project lead configured. Attempting to create without lead.")
        self.logger.debug(f"Project creation payload: {payload}")
        try:
            self._request("POST", "/project", payload)
        except Exception as exc:
            error_text = str(exc)
            if "leadAccountId" in error_text or "Unrecognized field \"leadAccountId\"" in error_text:
                fallback_payload = dict(payload)
                fallback_payload.pop("leadAccountId", None)
                fallback_payload["lead"] = lead
                try:
                    self.logger.debug("Retrying project creation with legacy lead field after leadAccountId rejection.")
                    self._request("POST", "/project", fallback_payload)
                except Exception as inner_exc:
                    self.logger.warning("Project creation skipped: %s", inner_exc)
            else:
                self.logger.warning("Project creation skipped: %s", exc)
        return target_project

    def create_issue(self, issue: dict) -> dict:
        target_project = self._resolve_target_project()
        issue_type = issue.get("issueType") or "Task"
        target_project_key = (target_project.get("id") or self.project or self.config.get("project") or self.config.get("project_key") or "MIG").strip().upper()
        parent_reference = issue.get("parent") or issue.get("parent_id")
        source_issue_type = issue.get("sourceIssueType") or issue.get("source_issue_type")
        is_source_subtask = self._is_subtask_issue_type(source_issue_type) or self._is_subtask_issue_type(issue_type)
        resolved_issue_type = self._resolve_issue_type(issue_type)
        parent_key = None
        use_parent_field = bool(issue.get("deferred")) or issue_type == "Task" or (not source_issue_type and self._is_subtask_issue_type(issue_type))
        if is_source_subtask and parent_reference:
            parent_key = self._resolve_parent_key(parent_reference)
            if not parent_key:
                parent_key = self._resolve_parent_key(issue.get("parent"))
            if not parent_key:
                self.logger.info("Deferring subtask %s until parent exists", issue.get("key") or issue.get("id") or issue.get("summary"))
                pending_issue = dict(issue)
                pending_issue["deferred"] = True
                self.pending_child_issues.append(pending_issue)
                return {
                    "id": None,
                    "key": None,
                    "summary": issue.get("summary", ""),
                    "description": issue.get("description", ""),
                    "issueType": issue.get("issueType", "Task"),
                    "status": issue.get("status", "Open"),
                    "deferred": True,
                }
            if parent_key:
                supported_subtask_type = self._resolve_target_subtask_issue_type(target_project_key)
                resolved_issue_type = supported_subtask_type or "Task"
        elif is_source_subtask:
            resolved_issue_type = "Task"

        # If the target supports a real sub-task issue type, ensure we use the parent field
        if is_source_subtask and parent_key and self._is_subtask_issue_type(resolved_issue_type):
            use_parent_field = True

        payload = {
            "fields": {
                "project": {"key": target_project_key},
                "summary": issue.get("summary", "") or (issue.get("fields") or {}).get("summary", ""),
                "description": self._to_adf(issue.get("description", "")) if self._adf_supported else self._extract_description(issue.get("description", "")),
                "issuetype": {"name": resolved_issue_type},
            }
        }

        raw_fields = issue.get("fields")
        if isinstance(raw_fields, dict):
            for field_name, field_value in raw_fields.items():
                if field_name in (
                    "project",
                    "summary",
                    "description",
                    "issuetype",
                    "status",
                    "parent",
                    "comment",
                    "attachment",
                    "issuelinks",
                    "changelog",
                ):
                    continue
                payload["fields"][field_name] = field_value

        reserved_top_level_fields = {
            "id",
            "key",
            "summary",
            "description",
            "issueType",
            "sourceIssueType",
            "source_issue_type",
            "parent",
            "parent_id",
            "comments",
            "attachments",
            "linked_issues",
            "history",
            "deferred",
            "fields",
            "sourceType",
            "targetType",
            "project",
            "epic_name",
            "epicName",
        }
        for field_name, field_value in issue.items():
            if field_name in reserved_top_level_fields:
                continue
            if field_name in payload["fields"]:
                continue
            payload["fields"][field_name] = field_value

        if resolved_issue_type == "Epic":
            epic_name = issue.get("epic_name") or issue.get("epicName") or issue.get("summary", "")
            if isinstance(epic_name, str) and epic_name.strip():
                payload["fields"][self.epic_name_field] = epic_name.strip()
        if is_source_subtask and parent_reference and parent_key:
            if use_parent_field:
                payload["fields"]["parent"] = {"key": str(parent_key)}
            else:
                payload["fields"].pop("parent", None)

        try:
            response = self._request("POST", "/issue", payload)
        except Exception as exc:
            response = self._create_issue_with_fallbacks(
                issue,
                payload,
                exc,
                resolved_issue_type,
                is_source_subtask,
                parent_reference,
                parent_key,
                use_parent_field,
            )

        if response is None:
            raise RuntimeError("Failed to create Jira issue after fallback attempts")

        issue_id = response.get("id") or response.get("key")
        if issue_id:
            target_issue_key = response.get("key") or response.get("id")
            if target_issue_key:
                source_id = str(issue.get("id") or issue.get("key") or "")
                self.created_issue_keys[source_id] = str(target_issue_key)
                if issue.get("key"):
                    self.created_issue_keys[str(issue.get("key"))] = str(target_issue_key)
                if response.get("id"):
                    self.created_issue_ids[source_id] = str(response.get("id"))
                    if issue.get("key"):
                        self.created_issue_ids[str(issue.get("key"))] = str(response.get("id"))
            self._create_comments(issue_id, issue.get("comments", []))
            self._create_attachments(issue_id, issue.get("attachments", []))
            linked_issues = list(issue.get("linked_issues") or [])
            if is_source_subtask and parent_reference and not use_parent_field and parent_key:
                self._request(
                    "POST",
                    "/issueLink",
                    {
                        "type": {"name": "Relates"},
                        "inwardIssue": {"key": str(target_issue_key or issue_id)},
                        "outwardIssue": {"key": str(parent_key)},
                    },
                )
            self._create_issue_links(target_issue_key or issue_id, linked_issues)
            self._process_pending_issue_links()
            self._process_pending_child_issues()

        return {
            "id": response.get("id"),
            "key": response.get("key"),
            "summary": issue.get("summary", ""),
            "description": issue.get("description", ""),
            "issueType": issue.get("issueType", "Task"),
            "status": issue.get("status", "Open"),
            "parent": parent_key,
            "comments": issue.get("comments", []),
            "attachments": issue.get("attachments", []),
            "linked_issues": issue.get("linked_issues", []),
            "history": issue.get("history", []),
        }

    def update_issue(self, issue_id: str, issue: dict) -> dict:
        payload = {
            "fields": {
                "summary": issue.get("summary", ""),
                "description": issue.get("description", ""),
                "issuetype": {"name": issue.get("issueType", "Task")},
            }
        }
        self._request("PUT", f"/issue/{issue_id}", payload)
        return issue

    def _extract_account_id(self, user_info: Any) -> str | None:
        if isinstance(user_info, dict):
            self.logger.debug(f"Extracting account ID from user_info: {list(user_info.keys())}")
            for key in ("emailAddress", "name", "accountId", "account_id", "accountid", "key"):
                value = user_info.get(key)
                if isinstance(value, str) and value.strip():
                    self.logger.debug(f"Found account ID from field '{key}': {value}")
                    return value.strip()
        return None

    def _looks_like_email(self, value: Any) -> bool:
        return isinstance(value, str) and "@" in value

    def _resolve_issue_type(self, issue_type: Any) -> str:
        if not isinstance(issue_type, str) or not issue_type.strip():
            return "Task"
        mapping = {
            "story": "Story",
            "user story": "Story",
            "bug": "Bug",
            "task": "Task",
            "epic": "Epic",
            "sub-task": "Sub-task",
            "subtask": "Sub-task",
        }
        normalized = issue_type.strip().lower()
        return mapping.get(normalized, issue_type.strip())

    def _is_subtask_issue_type(self, issue_type: Any) -> bool:
        normalized = str(issue_type or "").strip().lower()
        return normalized in {"sub-task", "subtask"}

    def _create_issue_with_fallbacks(
        self,
        issue: dict,
        payload: dict[str, Any],
        exc: Exception,
        resolved_issue_type: str,
        is_source_subtask: bool,
        parent_reference: Any,
        parent_key: str | None,
        use_parent_field: bool,
    ) -> dict[str, Any] | None:
        error_text = str(exc)
        description_field = payload["fields"].get("description")
        if (
            isinstance(description_field, dict)
            and "description" in error_text.lower()
            and (
                "operation value must be a string" in error_text.lower()
                or "expected string" in error_text.lower()
                or "must be a string" in error_text.lower()
            )
        ):
            self.logger.warning(
                "Issue description ADF rejected by Jira; retrying with plain string description: %s",
                exc,
            )
            # Remember that this server doesn't accept ADF descriptions to avoid repeated retries
            self._adf_supported = False
            payload["fields"]["description"] = self._extract_description(issue.get("description", ""))
            try:
                return self._request("POST", "/issue", payload)
            except Exception as second_exc:
                exc = second_exc
                error_text = str(second_exc)

        fallback_issue_type = self._resolve_issue_type("Task")
        issue_type = issue.get("issueType") or "Task"
        if is_source_subtask and parent_reference:
            self.logger.warning(
                "Sub-task issue %r was rejected by Jira; creating it as a task and linking to the parent instead: %s",
                issue.get("summary") or issue.get("key"),
                exc,
            )
            payload["fields"]["issuetype"] = {"name": fallback_issue_type}
            payload["fields"].pop("parent", None)
            return self._request("POST", "/issue", payload)
        if self._is_subtask_issue_type(resolved_issue_type) or self._is_subtask_issue_type(issue_type):
            self.logger.warning(
                "Sub-task issue %r was rejected by Jira; retrying as %r: %s",
                issue.get("summary") or issue.get("key"),
                fallback_issue_type,
                exc,
            )
            payload["fields"]["issuetype"] = {"name": fallback_issue_type}
            if self._should_drop_parent_for_fallback(issue_type, fallback_issue_type):
                payload["fields"].pop("parent", None)
            return self._request("POST", "/issue", payload)
        if fallback_issue_type != payload["fields"]["issuetype"]["name"]:
            self.logger.warning(
                "Issue type %r rejected, retrying with %r: %s",
                issue_type,
                fallback_issue_type,
                exc,
            )
            payload["fields"]["issuetype"] = {"name": fallback_issue_type}
            return self._request("POST", "/issue", payload)
        return None

    def _resolve_target_subtask_issue_type(self, target_project_key: str) -> str | None:
        if not target_project_key:
            return None
        # Return cached value if available
        cached = self._createmeta_cache.get(target_project_key)
        if cached is not None:
            return cached
        try:
            data = self._request("GET", f"/issue/createmeta/{target_project_key}/issuetypes")
        except Exception:
            self._createmeta_cache[target_project_key] = None
            return None
        values = data.get("values") if isinstance(data, dict) else None
        if not isinstance(values, list):
            return None
        for item in values:
            if not isinstance(item, dict):
                continue
            if item.get("subtask") is True:
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    self._createmeta_cache[target_project_key] = name.strip()
                    return name.strip()
        self._createmeta_cache[target_project_key] = None
        return None

    def _should_drop_parent_for_fallback(self, issue_type: Any, fallback_issue_type: str) -> bool:
        if fallback_issue_type != "Task":
            return False
        return self._is_subtask_issue_type(issue_type)

    def _resolve_parent_key(self, parent_reference: Any) -> str | None:
        if parent_reference is None:
            return None
        values = []
        if isinstance(parent_reference, str):
            values.append(parent_reference)
        if isinstance(parent_reference, (list, tuple)):
            values.extend(str(item) for item in parent_reference if item is not None)
        for value in values:
            if not value:
                continue
            direct_key = self.created_issue_keys.get(str(value))
            if direct_key:
                return str(direct_key)
            direct_id = self.created_issue_ids.get(str(value))
            if direct_id:
                return str(direct_id)
            if self._looks_like_issue_key(str(value)):
                return str(value)
            if str(value).isdigit():
                return None
        return None

    def _create_comments(self, issue_id: Any, comments: list[dict[str, Any]]) -> None:
        for comment in comments or []:
            body = comment.get("body") or comment.get("text")
            if not body:
                continue
            if isinstance(body, (dict, list)):
                adf_body = body
            else:
                adf_body = self._to_adf(str(body))
            self._request("POST", f"/issue/{issue_id}/comment", {"body": adf_body})

    def _create_attachments(self, issue_id: Any, attachments: list[dict[str, Any]]) -> None:
        for attachment in attachments or []:
            if not isinstance(attachment, dict):
                continue
            name = attachment.get("name") or attachment.get("filename") or "attachment"
            content = attachment.get("content") or attachment.get("data") or attachment.get("bytes")
            if content is None:
                continue
            payload = {
                "file": (name, content if isinstance(content, (bytes, bytearray)) else str(content).encode("utf-8")),
            }
            self._request("POST", f"/issue/{issue_id}/attachments", payload)

    def _create_issue_links(self, issue_id: Any, linked_issues: list[dict[str, Any]]) -> None:
        for link in linked_issues or []:
            if not isinstance(link, dict):
                continue
            target_key = link.get("target_key")
            if not target_key:
                continue
            resolved_target_key = self._resolve_link_target_key(target_key)
            if not resolved_target_key:
                self.pending_issue_links.append((issue_id, link))
                self.logger.info("Queued issue link for %s until target issue exists", target_key)
                continue

            # Determine relation name and direction (outward means current -> target)
            type_raw = link.get("type_raw") if isinstance(link.get("type_raw"), dict) else {}
            relation_name = link.get("relation") or (type_raw.get("name") if isinstance(type_raw, dict) else "Relates")
            direction = link.get("direction") or "outward"

            # Canonicalize type identifiers for duplicate suppression and creation
            canonical_type_name = None
            if isinstance(type_raw, dict):
                canonical_type_name = type_raw.get("name")
            if not isinstance(canonical_type_name, str) or not canonical_type_name.strip():
                normalized_relation = str(relation_name).strip() if relation_name is not None else ""
                relation_map = {
                    "blocks": "Blocks",
                    "blocked by": "Blocks",
                    "is blocked by": "Blocks",
                    "implements": "Implements",
                    "implements by": "Implements",
                    "implemented by": "Implements",
                    "is implemented by": "Implements",
                    "relates": "Relates",
                    "relates to": "Relates",
                    "is related to": "Relates",
                }
                canonical_type_name = relation_map.get(normalized_relation.lower(), normalized_relation or "Relates")

            type_id = type_raw.get("id") if isinstance(type_raw, dict) else None
            if not type_id:
                type_id = None
            pair_type = str(type_id) if type_id is not None else canonical_type_name or "Relates"

            # Normalize a directionless key to avoid creating duplicate reciprocal links
            try:
                current_key = str(issue_id)
                pair_key = tuple(sorted([current_key, str(resolved_target_key)])) + (pair_type,)
            except Exception:
                pair_key = None

            if pair_key and pair_key in self._created_links:
                self.logger.debug("Skipping duplicate link creation for %s -> %s (%s)", current_key, resolved_target_key, pair_type)
                continue

            try:
                if direction == "inward":
                    inward = str(resolved_target_key)
                    outward = str(issue_id)
                else:
                    inward = str(issue_id)
                    outward = str(resolved_target_key)

                payload = {
                    "type": {"id": str(type_id)} if type_id is not None else {"name": canonical_type_name},
                    "inwardIssue": {"key": inward},
                    "outwardIssue": {"key": outward},
                }
                self._request("POST", "/issueLink", payload)
                if pair_key:
                    self._created_links.add(pair_key)
            except Exception as exc:
                self.logger.warning("Issue link skipped for %s: %s", target_key, exc)

    def _process_pending_child_issues(self) -> None:
        if self._processing_pending_child_issues or not self.pending_child_issues:
            return

        self._processing_pending_child_issues = True
        try:
            pending = list(self.pending_child_issues)
            self.pending_child_issues = []
            for pending_issue in pending:
                parent_reference = pending_issue.get("parent") or pending_issue.get("parent_id")
                if not parent_reference:
                    continue
                parent_key = self.created_issue_keys.get(str(parent_reference))
                if not parent_key:
                    self.pending_child_issues.append(pending_issue)
                    continue
                self.create_issue(pending_issue)
        finally:
            self._processing_pending_child_issues = False

    def _process_pending_issue_links(self) -> None:
        if not self.pending_issue_links:
            return

        pending = list(self.pending_issue_links)
        self.pending_issue_links = []
        for issue_id, link in pending:
            target_key = link.get("target_key")
            resolved_target_key = self._resolve_link_target_key(target_key)
            if not resolved_target_key:
                self.pending_issue_links.append((issue_id, link))
                continue
            # reuse the same logic as in _create_issue_links to ensure de-duplication and direction
            self._create_issue_links(issue_id, [link])

    def _resolve_link_target_key(self, target_key: Any) -> str | None:
        if target_key is None:
            return None
        value = str(target_key)
        if not value:
            return None
        mapped_key = self.created_issue_keys.get(value)
        if mapped_key:
            return str(mapped_key)
        if self._looks_like_issue_key(value):
            return value
        return None

    def _looks_like_issue_key(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        return bool(re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", value.strip().upper()))

    def _looks_like_multipart_payload(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        return any(isinstance(value, tuple) and len(value) >= 2 for value in payload.values())

    def _encode_multipart_form_data(self, payload: Any, boundary: str) -> bytes:
        if not isinstance(payload, dict):
            raise TypeError("Multipart payload must be a dictionary")

        parts: list[bytes] = []
        for name, value in payload.items():
            if isinstance(value, tuple) and len(value) >= 2:
                filename, file_data = value[0], value[1]
                parts.append(f"--{boundary}\r\n".encode("utf-8"))
                parts.append(
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8")
                )
                parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
                parts.append(file_data if isinstance(file_data, (bytes, bytearray)) else str(file_data).encode("utf-8"))
                parts.append(b"\r\n")
            else:
                parts.append(f"--{boundary}\r\n".encode("utf-8"))
                parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
                parts.append(value if isinstance(value, (bytes, bytearray)) else str(value).encode("utf-8"))
                parts.append(b"\r\n")

        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(parts)

    def _extract_comment_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            content = value.get("content", [])
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        for item in block.get("content", []):
                            if isinstance(item, dict) and item.get("text"):
                                parts.append(str(item.get("text")))
                if parts:
                    return "\n".join(parts)
        return ""

    def _to_adf(self, value: Any) -> dict[str, Any]:
        text = str(value or "").strip()
        if not text:
            return {"type": "doc", "version": 1, "content": []}
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }

    def close(self) -> None:
        self.connected = False

    def _extract_description(self, description: Any) -> str:
        if isinstance(description, str):
            return description
        if isinstance(description, dict):
            content = description.get("content", [])
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        for item in block.get("content", []):
                            if isinstance(item, dict) and item.get("text"):
                                parts.append(item.get("text"))
                return "\n".join(parts)
        return ""
