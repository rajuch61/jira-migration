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
        self.retry_count = int(self._resolve_config_value(config, "retry_count", default=3, env_names=("JIRA_RETRY_COUNT",)))
        self.retry_delay = float(self._resolve_config_value(config, "retry_delay", default=1.0, env_names=("JIRA_RETRY_DELAY",)))
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
        self._field_name_to_id_cache: dict[str, str | None] = {}
        self.custom_field_mapping = config.get("custom_field_mapping") if isinstance(config.get("custom_field_mapping"), dict) else {}
        self._resolved_custom_field_ids: dict[str, str] = {}
        self._user_reference_cache: dict[str, dict[str, str] | None] = {}
        self._sprint_field_id: str | None = None
        self._sprint_id_cache: dict[tuple[str, str], int | None] = {}
        self._date_created_field_id: str | None = None
        self._date_created_field_setup_attempted = False
        self._date_created_field_screens_ensured: set[str] = set()
        self._link_types_by_name: dict[str, str] | None = None
        self._created_link_type_names: set[str] = set()

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
        api_token = self._resolve_config_value(config, "api_token", default=None, env_names=("JIRA_API_TOKEN",))
        if username and password:
            return str(username), str(password)
        if username and token:
            return str(username), str(token)
        if username and api_token:
            return str(username), str(api_token)
        return None

    def _parse_bearer_token(self, config: dict) -> str | None:
        bearer_token = self._resolve_config_value(config, "bearer_token", default=None, env_names=("JIRA_BEARER_TOKEN",))
        if isinstance(bearer_token, str) and bearer_token.strip():
            return bearer_token.strip()

        if self._resolve_auth_type(config) != "bearer":
            return None

        token = self._resolve_config_value(config, "token", "api_token", default=None, env_names=("JIRA_TOKEN",))
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

        context = None if self.verify_ssl else ssl._create_unverified_context()
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            req = JiraRequest(url, data=data, headers=headers, method=method)
            if self.bearer_token:
                req.add_header("Authorization", f"Bearer {self.bearer_token}")
            elif self.basic_auth:
                username, password = self.basic_auth
                token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
                req.add_header("Authorization", f"Basic {token}")

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
                last_error = exc
                if attempt < self.retry_count:
                    self.logger.warning("Transient Jira connection error on attempt %s/%s for %s: %s", attempt + 1, self.retry_count + 1, path, exc)
                    if self.retry_delay > 0:
                        import time
                        time.sleep(self.retry_delay)
                    continue
                raise RuntimeError(f"Unable to reach Jira server: {exc}") from exc

        if last_error is not None:
            raise RuntimeError(f"Unable to reach Jira server: {last_error}") from last_error
        raise RuntimeError("Jira request failed without response")

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

        configured_fields = (
            self.config.get("search_fields")
            or self.config.get("fields")
            or self.config.get("allowed_fields")
        )
        if isinstance(configured_fields, str):
            configured_fields = [field.strip() for field in configured_fields.split(",") if field.strip()]
        # The base set of fields is always required for core migration logic (summary,
        # description, issue type/status/parent, comments, attachments, links, the
        # original created/updated timestamps needed for the migration provenance note,
        # and assignee/reporter so those are always attempted on migration). Any
        # "search_fields"/"fields"/"allowed_fields" configured on top (e.g. duedate,
        # priority, labels) are additive extras, not a replacement of the base set.
        fields = ["summary", "description", "issuetype", "status", "parent", "comment", "attachment", "issuelinks", "created", "updated", "assignee", "reporter"]
        if isinstance(configured_fields, list):
            for extra_field in configured_fields:
                if extra_field not in fields:
                    fields.append(extra_field)

        # Resolve any name-mapped custom fields (config: custom_field_mapping) so their
        # instance-specific field ids are included in the fields fetched from this server.
        self._resolved_custom_field_ids = {}
        for source_field_name in self.custom_field_mapping:
            field_id = self._resolve_field_id_by_name(source_field_name)
            if field_id:
                self._resolved_custom_field_ids[source_field_name] = field_id
                if field_id not in fields:
                    fields.append(field_id)

        # Sprint is always resolved (not just when custom_field_mapping opts in) so the
        # issue's current sprint assignment can be migrated to the matching sprint on the
        # target board (see create_issue()/_resolve_target_sprint_id()).
        self._sprint_field_id = self._resolve_field_id_by_name("Sprint")
        if self._sprint_field_id and self._sprint_field_id not in fields:
            fields.append(self._sprint_field_id)

        query = self._build_search_jql(issue_keys)
        self.logger.info(f"Fetching issues with JQL query: {query}")

        issue_payloads: list[dict[str, Any]] = []
        if issue_keys:
            for issue_key in issue_keys:
                payload = self._request("GET", self._build_issue_path(issue_key, fields, expand="changelog"))
                if isinstance(payload, dict):
                    issue_payloads.append(payload)
            return self._normalize_issue_payloads(issue_payloads)

        page_size = int(self._resolve_config_value(self.config, "page_size", default=100, env_names=("JIRA_PAGE_SIZE",))) or 100
        search_path = self._build_search_path(query, fields, page_size, expand="changelog")
        self.logger.debug(f"Search request path: {search_path}")

        if self.prefer_key_search:
            self.logger.info("Using key-only search discovery for issue keys because prefer_key_search is enabled.")
            issue_keys = self._search_issue_keys(query, page_size)
            for issue_key in issue_keys:
                payload = self._request("GET", self._build_issue_path(issue_key, fields, expand="changelog"))
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
                    fields_data = item.get("fields")
                    if isinstance(fields_data, dict) and all(key in fields_data for key in ("comment", "attachment", "issuelinks")):
                        issue_payloads.append(item)
                        continue
                    issue_key = item.get("key")
                    if not issue_key:
                        continue
                    payload = self._request("GET", self._build_issue_path(issue_key, fields, expand="changelog"))
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

            sprint_name = None
            if self._sprint_field_id:
                sprint_name = self._extract_current_sprint_name(fields_data.get(self._sprint_field_id))

            custom_fields = {}
            for source_field_name, field_id in self._resolved_custom_field_ids.items():
                if field_id in fields_data:
                    custom_fields[source_field_name] = fields_data.get(field_id)

            issue = {
                "id": issue_data.get("id"),
                "key": issue_data.get("key"),
                "summary": fields_data.get("summary", ""),
                "description": self._extract_description(fields_data.get("description")),
                "issueType": fields_data.get("issuetype", {}).get("name", "Task"),
                "parent": fields_data.get("parent", {}).get("key") if isinstance(fields_data.get("parent"), dict) else None,
                "status": fields_data.get("status", {}).get("name", "Open"),
                "dueDate": fields_data.get("duedate"),
                "originalCreated": fields_data.get("created"),
                "originalUpdated": fields_data.get("updated"),
                "assignee": fields_data.get("assignee"),
                "reporter": fields_data.get("reporter"),
                "comments": comments,
                "attachments": attachments,
                "linked_issues": linked_issues,
                "history": history,
                "sprint": sprint_name,
                "customFields": custom_fields,
            }
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

    def _build_search_path(self, query: str, fields: list[str] | None, max_results: int | None = 100, expand: str | None = None) -> str:
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
        if expand:
            params.append(f"expand={quote(expand, safe='')}")
        if not params:
            return path
        return f"{path}?{'&'.join(params)}"

    def _build_issue_path(self, issue_key: str, fields: list[str], expand: str | None = None) -> str:
        encoded_fields = quote(",".join(fields), safe="")
        path = f"/issue/{issue_key}?fields={encoded_fields}"
        if expand:
            path = f"{path}&expand={quote(expand, safe='')}"
        return path

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
                "summary": issue.get("summary", ""),
                "description": self._to_adf(issue.get("description", "")) if self._adf_supported else self._extract_description(issue.get("description", "")),
                "issuetype": {"name": resolved_issue_type},
            }
        }
        if resolved_issue_type == "Epic":
            epic_name = issue.get("epic_name") or issue.get("epicName") or issue.get("summary", "")
            if isinstance(epic_name, str) and epic_name.strip():
                payload["fields"][self.epic_name_field] = epic_name.strip()
        assignee = issue.get("assignee")
        if assignee is not None:
            assignee_field = self._normalize_user_reference(assignee)
            if assignee_field is not None:
                payload["fields"]["assignee"] = assignee_field
        reporter = issue.get("reporter")
        if reporter is not None:
            reporter_field = self._normalize_user_reference(reporter)
            if reporter_field is not None:
                payload["fields"]["reporter"] = reporter_field
        due_date = issue.get("dueDate") or issue.get("duedate")
        if isinstance(due_date, str) and due_date.strip():
            # Jira's "duedate" field expects a plain "YYYY-MM-DD" string; source payloads
            # from the search API are already in that format, so pass through as-is.
            payload["fields"]["duedate"] = due_date.strip()[:10]
        custom_field_values = issue.get("customFields")
        if isinstance(custom_field_values, dict) and self.custom_field_mapping:
            for source_field_name, value in custom_field_values.items():
                if value is None or value == "":
                    continue
                target_field_name = self.custom_field_mapping.get(source_field_name)
                if not target_field_name:
                    continue
                target_field_id = self._resolve_field_id_by_name(target_field_name)
                if target_field_id:
                    payload["fields"][target_field_id] = value
        sprint_name = issue.get("sprint")
        # Sub-tasks always inherit their parent's sprint automatically in Jira Cloud and
        # reject an explicit Sprint value on creation ("subtasks cannot be associated to
        # a sprint"), so only attempt this for top-level (non-subtask) issues.
        if isinstance(sprint_name, str) and sprint_name.strip() and not is_source_subtask:
            sprint_field_id = self._resolve_field_id_by_name("Sprint")
            sprint_id = self._resolve_target_sprint_id(sprint_name.strip(), target_project_key)
            if sprint_field_id and sprint_id is not None:
                payload["fields"][sprint_field_id] = sprint_id
        original_created = issue.get("originalCreated")
        if isinstance(original_created, str) and original_created.strip():
            # Jira Cloud's native "created" system field always reflects the actual API
            # call time and cannot be overridden, so the source's real creation timestamp
            # is preserved in a dedicated "Date Created" custom field instead.
            date_created_field_id = self._resolve_or_create_date_created_field(target_project_key)
            if date_created_field_id:
                payload["fields"][date_created_field_id] = original_created.strip()
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
            self._replay_history_field_changes(issue_id, issue.get("history", []))
            if is_source_subtask and parent_reference and not use_parent_field and parent_key:
                # The target project does not support a real Sub-task issue type (or the
                # target rejected it), so preserve the parent/child relationship as an
                # explicit "Relates" link instead of silently dropping it.
                try:
                    self._request(
                        "POST",
                        "/issueLink",
                        {
                            "type": {"name": "Relates"},
                            "inwardIssue": {"key": str(target_issue_key or issue_id)},
                            "outwardIssue": {"key": str(parent_key)},
                        },
                    )
                except Exception as exc:
                    self.logger.warning("Unable to link sub-task %s to parent %s: %s", target_issue_key or issue_id, parent_key, exc)
            # NOTE: issuelinks captured from the source (e.g. "Implements"/"Blocks") are
            # intentionally NOT created here. They are created in a dedicated final phase
            # (see MigrationEngine.run) after every issue in the batch has been migrated,
            # so link targets always resolve on the first attempt instead of relying on a
            # best-effort pending queue.

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

    def _resolve_field_id_by_name(self, field_name: str) -> str | None:
        """Resolve a Jira field's id (e.g. "customfield_10015") from its display name.

        Field ids for custom fields are instance-specific, so migrating a custom field
        by name requires looking it up via GET /field on whichever server this connector
        instance talks to. Results are cached for the lifetime of the connector.
        """
        if not field_name:
            return None
        if field_name in self._field_name_to_id_cache:
            return self._field_name_to_id_cache[field_name]
        try:
            data = self._request("GET", "/field")
        except Exception as exc:
            self.logger.warning("Unable to fetch field metadata to resolve %r: %s", field_name, exc)
            return None
        if not isinstance(data, list):
            return None
        exact_match = None
        case_insensitive_match = None
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            field_id = item.get("id")
            if not isinstance(name, str) or not isinstance(field_id, str):
                continue
            self._field_name_to_id_cache.setdefault(name, field_id)
            if name == field_name:
                exact_match = field_id
            elif case_insensitive_match is None and name.lower() == field_name.lower():
                case_insensitive_match = field_id
        resolved = exact_match or case_insensitive_match
        self._field_name_to_id_cache[field_name] = resolved
        if not resolved:
            self.logger.warning("No Jira field named %r was found on %s", field_name, self.server)
        return resolved

    def _extract_current_sprint_name(self, raw_value: Any) -> str | None:
        """Extract the current sprint's name from a source issue's raw Sprint field value.

        Jira Server's "Sprint" custom field returns a list, either of structured objects
        (newer versions: {"id", "name", "state", ...}) or of legacy Greenhopper-style
        strings (e.g. "com.atlassian.greenhopper.service.sprint.Sprint@...[id=2,...,
        state=ACTIVE,name=TM Sprint 1,...]"). An issue can carry more than one sprint
        entry if it moved between sprints, so prefer the ACTIVE one; otherwise fall back
        to the most recently listed entry (Jira appends sprints in chronological order).
        """
        if not isinstance(raw_value, list) or not raw_value:
            return None
        parsed: list[tuple[str, str | None]] = []
        for entry in raw_value:
            if isinstance(entry, dict):
                name = entry.get("name")
                state = entry.get("state")
                if isinstance(name, str) and name.strip():
                    parsed.append((name.strip(), state if isinstance(state, str) else None))
            elif isinstance(entry, str):
                name_match = re.search(r"name=([^,\]]+)", entry)
                state_match = re.search(r"state=([^,\]]+)", entry)
                if name_match:
                    parsed.append((name_match.group(1).strip(), state_match.group(1).strip() if state_match else None))
        if not parsed:
            return None
        for name, state in reversed(parsed):
            if isinstance(state, str) and state.upper() == "ACTIVE":
                return name
        return parsed[-1][0]

    def _resolve_target_sprint_id(self, sprint_name: str, target_project_key: str) -> int | None:
        """Resolve a source sprint name to the matching sprint's id on the target's board.

        Uses the Jira Agile REST API (/rest/agile/1.0), which is separate from the core
        /rest/api/2 path this connector otherwise talks to, so full URLs are passed to
        _request() directly (it treats any "http..." path as already-absolute).
        """
        if not sprint_name or not target_project_key:
            return None
        cache_key = (target_project_key, sprint_name.lower())
        if cache_key in self._sprint_id_cache:
            return self._sprint_id_cache[cache_key]

        sprint_id: int | None = None
        try:
            boards_response = self._request(
                "GET", f"{self.server}/rest/agile/1.0/board?projectKeyOrId={quote(target_project_key, safe='')}"
            )
            boards = boards_response.get("values", []) if isinstance(boards_response, dict) else []
            for board in boards:
                if not isinstance(board, dict) or board.get("id") is None:
                    continue
                board_id = board["id"]
                start_at = 0
                while sprint_id is None:
                    sprints_response = self._request(
                        "GET",
                        f"{self.server}/rest/agile/1.0/board/{board_id}/sprint?startAt={start_at}&maxResults=50",
                    )
                    if not isinstance(sprints_response, dict):
                        break
                    for sprint in sprints_response.get("values", []):
                        if isinstance(sprint, dict) and isinstance(sprint.get("name"), str) and sprint["name"].strip().lower() == sprint_name.lower():
                            sprint_id = sprint.get("id")
                            break
                    if sprint_id is not None or sprints_response.get("isLast", True):
                        break
                    start_at += 50
                if sprint_id is not None:
                    break
        except Exception as exc:
            self.logger.warning("Unable to resolve target sprint %r for project %s: %s", sprint_name, target_project_key, exc)

        self._sprint_id_cache[cache_key] = sprint_id
        if sprint_id is None:
            self.logger.warning(
                "No matching sprint named %r found on any target board for project %s; leaving Sprint unset.",
                sprint_name, target_project_key,
            )
        return sprint_id

    def _resolve_or_create_date_created_field(self, target_project_key: str) -> str | None:
        """Resolve (or create) a "Date Created" custom field on the target to hold the
        source issue's original creation timestamp.

        Jira Cloud's native "created" system field always reflects the actual API call
        time and cannot be set/overridden on creation, so this preserves the real source
        timestamp in a dedicated custom field instead. Field creation and lookup are only
        attempted once per connector instance (cached), since the field is global to the
        target site rather than per-project.
        """
        if self._date_created_field_id is not None:
            self._ensure_field_on_project_screens(self._date_created_field_id, target_project_key)
            return self._date_created_field_id
        if self._date_created_field_setup_attempted:
            return None
        self._date_created_field_setup_attempted = True

        field_id = self._resolve_field_id_by_name("Date Created")
        if not field_id:
            try:
                created = self._request(
                    "POST",
                    "/field",
                    {
                        "name": "Date Created",
                        "description": (
                            "Original creation date/time preserved from the migrated source "
                            "issue (Jira Cloud's built-in Created field cannot be overridden "
                            "via the API)."
                        ),
                        "type": "com.atlassian.jira.plugin.system.customfieldtypes:datetime",
                    },
                )
                field_id = created.get("id") if isinstance(created, dict) else None
                if field_id:
                    self._field_name_to_id_cache["Date Created"] = field_id
                    self.logger.info("Created new custom field 'Date Created' (%s) on %s", field_id, self.server)
            except Exception as exc:
                self.logger.warning("Unable to create 'Date Created' custom field on %s: %s", self.server, exc)
                return None

        if not field_id:
            return None
        self._date_created_field_id = field_id
        self._ensure_field_on_project_screens(field_id, target_project_key)
        return field_id

    def _ensure_field_on_project_screens(self, field_id: str, target_project_key: str) -> None:
        """Best-effort: make sure `field_id` is present on every screen used by the given
        project, since a custom field's value can't be set via the API until it is on the
        appropriate screen(s). Safe to call repeatedly (results cached per project).
        """
        if target_project_key in self._date_created_field_screens_ensured:
            return
        self._date_created_field_screens_ensured.add(target_project_key)
        try:
            project = self._request("GET", f"/project/{quote(target_project_key, safe='')}")
            project_id = project.get("id") if isinstance(project, dict) else None
            if not project_id:
                return
            scheme_response = self._request("GET", f"/issuetypescreenscheme/project?projectId={project_id}")
            scheme_values = scheme_response.get("values", []) if isinstance(scheme_response, dict) else []
            screen_scheme_ids: set[str] = set()
            for value in scheme_values:
                issue_type_screen_scheme = value.get("issueTypeScreenScheme") if isinstance(value, dict) else None
                scheme_id = issue_type_screen_scheme.get("id") if isinstance(issue_type_screen_scheme, dict) else None
                if not scheme_id:
                    continue
                mapping_response = self._request("GET", f"/issuetypescreenscheme/mapping?issueTypeScreenSchemeId={scheme_id}")
                mapping_values = mapping_response.get("values", []) if isinstance(mapping_response, dict) else []
                for mapping in mapping_values:
                    screen_scheme_id = mapping.get("screenSchemeId") if isinstance(mapping, dict) else None
                    if screen_scheme_id:
                        screen_scheme_ids.add(str(screen_scheme_id))

            if not screen_scheme_ids:
                return
            query = "&".join(f"id={sid}" for sid in screen_scheme_ids)
            screen_scheme_response = self._request("GET", f"/screenscheme?{query}")
            screen_scheme_list = screen_scheme_response.get("values", []) if isinstance(screen_scheme_response, dict) else []
            screen_ids: set[str] = set()
            for screen_scheme in screen_scheme_list:
                screens = screen_scheme.get("screens") if isinstance(screen_scheme, dict) else None
                if isinstance(screens, dict):
                    for screen_id in screens.values():
                        if screen_id is not None:
                            screen_ids.add(str(screen_id))

            for screen_id in screen_ids:
                try:
                    tabs_response = self._request("GET", f"/screens/{screen_id}/tabs")
                except Exception:
                    continue
                tabs = tabs_response if isinstance(tabs_response, list) else [tabs_response]
                for tab in tabs:
                    tab_id = tab.get("id") if isinstance(tab, dict) else None
                    if tab_id is None:
                        continue
                    try:
                        self._request("POST", f"/screens/{screen_id}/tabs/{tab_id}/fields", {"fieldId": field_id})
                    except Exception:
                        # Field may already be on this tab, or the tab may not accept it;
                        # either way this is best-effort provisioning.
                        continue
        except Exception as exc:
            self.logger.warning(
                "Unable to ensure 'Date Created' field is on project %s's screens: %s", target_project_key, exc
            )

    def _resolve_target_subtask_issue_type(self, target_project_key: str) -> str | None:
        if not target_project_key:
            return None
        # Return cached value if available. Only successful lookups are cached; a
        # transient failure (e.g. the project's issue type scheme not being ready yet)
        # must not permanently disable sub-task creation for the rest of the run.
        cached = self._createmeta_cache.get(target_project_key)
        if cached is not None:
            return cached
        try:
            data = self._request("GET", f"/issue/createmeta/{target_project_key}/issuetypes")
        except Exception as exc:
            self.logger.warning("Unable to determine sub-task issue type for project %s: %s", target_project_key, exc)
            return None
        # Jira Cloud's REST API v2 returns issue types under the "issueTypes" key for this
        # endpoint; the paginated v3-style shape uses "values" instead. Support both so the
        # lookup works regardless of which shape the target server returns.
        values = None
        if isinstance(data, dict):
            values = data.get("issueTypes") if isinstance(data.get("issueTypes"), list) else data.get("values")
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
            # Only allow explicit mappings for parent resolution. Raw source keys should not
            # be used as target issue keys unless they were already migrated. Otherwise, we
            # risk linking to source project keys in the target Jira system.
            continue
        return None

    def _normalize_user_reference(self, user_value: Any) -> dict[str, str] | None:
        # Account ids (and even usernames/keys) are NOT portable across separate Jira
        # instances - the same person has a different accountId on the source Server
        # and the target Cloud site. Resolve the source's user reference (preferring
        # email, the identifier most likely to be shared across both systems) against
        # THIS Jira instance's own user directory via /user/search instead of blindly
        # reusing the source's id, which would silently fail (or worse, point at the
        # wrong person) on the target.
        query = self._extract_user_search_query(user_value)
        if not query:
            return None
        if query in self._user_reference_cache:
            return self._user_reference_cache[query]
        resolved = self._search_user_reference(query)
        self._user_reference_cache[query] = resolved
        if not resolved:
            self.logger.warning(
                "Unable to find a matching user for %r on %s; leaving assignee/reporter unset for this issue.",
                query,
                self.server,
            )
        return resolved

    def _extract_user_search_query(self, user_value: Any) -> str | None:
        if user_value is None:
            return None
        if isinstance(user_value, str):
            value = user_value.strip()
            return value or None
        if isinstance(user_value, dict):
            for key in ("emailAddress", "name", "accountId", "key", "displayName"):
                value = user_value.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _search_user_reference(self, query: str) -> dict[str, str] | None:
        try:
            results = self._request("GET", f"/user/search?query={quote(query, safe='')}")
        except Exception as exc:
            self.logger.warning("User search failed for %r on %s: %s", query, self.server, exc)
            return None
        if not isinstance(results, list):
            return None
        for item in results:
            if not isinstance(item, dict):
                continue
            account_id = item.get("accountId")
            if isinstance(account_id, str) and account_id.strip():
                return {"accountId": account_id.strip()}
            name = item.get("name") or item.get("key")
            if isinstance(name, str) and name.strip():
                return {"name": name.strip()}
        return None

    def _create_comments(self, issue_id: Any, comments: list[dict[str, Any]]) -> None:
        for comment in comments or []:
            body = comment.get("body") or comment.get("text")
            if not body:
                continue
            if isinstance(body, (dict, list)):
                text_body = self._extract_comment_text(body)
            else:
                text_body = str(body)

            if not text_body:
                continue

            payload = {"body": text_body}
            if isinstance(comment.get("created"), str):
                payload["created"] = comment.get("created")
            if isinstance(comment.get("updated"), str):
                payload["updated"] = comment.get("updated")

            try:
                self._request("POST", f"/issue/{issue_id}/comment", payload)
            except Exception as exc:
                if "created" in payload or "updated" in payload:
                    fallback_payload = {"body": text_body}
                    try:
                        self._request("POST", f"/issue/{issue_id}/comment", fallback_payload)
                        self.logger.debug(
                            "Created comment without preserved timestamps because target rejected metadata: %s",
                            exc,
                        )
                        continue
                    except Exception:
                        pass
                raise

    # Jira Cloud's REST API has no endpoint to write/backdate History (changelog)
    # entries directly, and any field update Jira DOES log always uses the real
    # "now" timestamp plus the authenticated API user as author - never the original
    # historical date/author. This is a curated allow-list of simple, low-risk fields
    # we can safely replay as real update calls after issue creation so genuine (but
    # re-dated/re-authored) History entries appear in the target's native History
    # tab. Fields that require id-resolution or workflow transitions (status, sprint,
    # fixVersions, components, assignee/reporter, links, attachments, rank, etc.) are
    # intentionally excluded to avoid fragile/incorrect side effects.
    _HISTORY_REPLAY_FIELD_MAP = {
        "description": "description",
        "summary": "summary",
        "priority": "priority",
        "environment": "environment",
        "duedate": "duedate",
        "due date": "duedate",
        "labels": "labels",
    }

    def _replay_history_field_changes(self, issue_id: Any, history: list[dict[str, Any]]) -> None:
        for entry in history or []:
            if not isinstance(entry, dict):
                continue
            field_name = entry.get("field")
            if not isinstance(field_name, str):
                continue
            target_field = self._HISTORY_REPLAY_FIELD_MAP.get(field_name.strip().lower())
            if not target_field:
                self.logger.debug(
                    "History replay: skipping field %r on %s (not in the safe replay allow-list)",
                    field_name, issue_id,
                )
                continue

            to_value = entry.get("to")
            if target_field == "priority":
                if not isinstance(to_value, str) or not to_value.strip():
                    continue
                field_value: Any = {"name": to_value.strip()}
            elif target_field == "labels":
                field_value = to_value.split() if isinstance(to_value, str) and to_value.strip() else []
            else:
                field_value = to_value if isinstance(to_value, str) else ""

            try:
                self._request("PUT", f"/issue/{issue_id}", {"fields": {target_field: field_value}})
            except Exception as exc:
                self.logger.warning(
                    "History replay: unable to set %s=%r on %s: %s", target_field, to_value, issue_id, exc,
                )

    def _create_attachments(self, issue_id: Any, attachments: list[dict[str, Any]]) -> None:
        for attachment in attachments or []:
            if not isinstance(attachment, dict):
                continue
            name = attachment.get("name") or attachment.get("filename") or "attachment"
            content = attachment.get("content") or attachment.get("data") or attachment.get("bytes")
            if content is None:
                continue
            if isinstance(content, str) and self._looks_like_attachment_url(content):
                downloaded = self._download_attachment(content)
                if downloaded is None:
                    self.logger.warning("Skipping attachment %s because it could not be downloaded", name)
                    continue
                content = downloaded
            payload = {
                "file": (name, content if isinstance(content, (bytes, bytearray)) else str(content).encode("utf-8")),
            }
            self._request("POST", f"/issue/{issue_id}/attachments", payload)

    def _download_attachment(self, url: str) -> bytes | None:
        if not isinstance(url, str) or not url.strip():
            return None
        attachment_url = url.strip()
        download_connector = self
        if hasattr(self, "source_connector") and isinstance(self.source_connector, JiraConnector):
            download_connector = self.source_connector

        base_server = getattr(download_connector, "server", None) or getattr(self, "server", None)
        if attachment_url.startswith("/"):
            if base_server:
                attachment_url = f"{base_server.rstrip('/')}{attachment_url}"
            else:
                attachment_url = f"{self.server.rstrip('/')}{attachment_url}"
        elif not attachment_url.lower().startswith(("http://", "https://")):
            attachment_url = self._build_url(attachment_url)

        self.logger.debug("Downloading attachment from %s", attachment_url)
        req = JiraRequest(attachment_url, method="GET")
        if getattr(download_connector, "bearer_token", None):
            req.add_header("Authorization", f"Bearer {download_connector.bearer_token}")
        elif getattr(download_connector, "basic_auth", None):
            username, password = download_connector.basic_auth
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")

        context = None if self.verify_ssl else ssl._create_unverified_context()
        try:
            with request.urlopen(req, timeout=self.timeout, context=context) as response:
                return response.read()
        except Exception as exc:
            self.logger.warning("Failed to download attachment %s: %s", url, exc)
            return None

    def _looks_like_attachment_url(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        stripped = value.strip()
        return stripped.lower().startswith(("http://", "https://")) or stripped.startswith("/")

    def create_issue_links(self, issue_id: Any, linked_issues: list[dict[str, Any]]) -> None:
        """Public entry point used by the migration engine's dedicated link-creation
        phase, once every issue in the batch has already been migrated."""
        self._create_issue_links(issue_id, linked_issues)
        self._process_pending_issue_links()

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
            raw_relation_name = link.get("relation") or (link.get("type_raw", {}).get("name") if isinstance(link.get("type_raw"), dict) else "Relates")
            # Source systems (e.g. Jira Server) can export link type names with a leading
            # sequence number (e.g. "1 Implements"). The target Jira Cloud instance only
            # recognizes the canonical name (e.g. "Implements"), so always canonicalize
            # before sending the payload, not just for the internal dedupe key.
            relation_name = self._canonicalize_relation_name(raw_relation_name)
            type_raw = link.get("type_raw") if isinstance(link.get("type_raw"), dict) else {}
            relation_name = self._resolve_or_create_link_type(relation_name, type_raw)
            direction = link.get("direction") or "outward"

            try:
                current_key = str(issue_id)
                pair_key = self._build_link_dedupe_key(current_key, str(resolved_target_key), relation_name)
            except Exception:
                pair_key = None

            if pair_key and pair_key in self._created_links:
                self.logger.debug("Skipping duplicate link creation for %s -> %s (%s)", current_key, resolved_target_key, relation_name)
                continue

            try:
                if direction == "inward":
                    inward = str(resolved_target_key)
                    outward = str(issue_id)
                else:
                    inward = str(issue_id)
                    outward = str(resolved_target_key)

                payload = {
                    "type": {"name": relation_name},
                    "inwardIssue": {"key": inward},
                    "outwardIssue": {"key": outward},
                }
                self._request("POST", "/issueLink", payload)
                if pair_key:
                    self._created_links.add(pair_key)
            except Exception as exc:
                self.logger.warning("Issue link skipped for %s: %s", target_key, exc)

    def _build_link_dedupe_key(self, source_key: str, target_key: str, relation_name: Any) -> tuple[str, str, str]:
        source = str(source_key)
        target = str(target_key)
        pair = tuple(sorted([source, target]))
        canonical_relation = self._canonicalize_relation_name(relation_name)
        return pair[0], pair[1], canonical_relation

    def _canonicalize_relation_name(self, relation_name: Any) -> str:
        if relation_name is None:
            return "Relates"

        text = str(relation_name).strip()
        if not text:
            return "Relates"

        compact = re.sub(r"^\d+\s*", "", text).strip()
        normalized = compact.lower()
        if normalized in {"implements", "implemented by"}:
            return "Implements"
        if normalized in {"blocks", "blocked by"}:
            return "Blocks"
        if normalized in {"clones", "cloned by"}:
            return "Clones"
        if normalized in {"contains", "contained by"}:
            return "Contains"
        return compact

    def _fetch_target_link_types(self) -> dict[str, str]:
        """Return a mapping of lower-cased link type name -> exact name as configured
        on the target Jira instance, fetched once and cached for the connector's lifetime."""
        if self._link_types_by_name is not None:
            return self._link_types_by_name

        link_types: dict[str, str] = {}
        try:
            response = self._request("GET", "/issueLinkType", None)
            for link_type in (response.get("issueLinkTypes") or []) if isinstance(response, dict) else []:
                name = link_type.get("name") if isinstance(link_type, dict) else None
                if name:
                    link_types[str(name).strip().lower()] = str(name).strip()
        except Exception as exc:
            self.logger.warning("Unable to fetch issue link types from target: %s", exc)

        self._link_types_by_name = link_types
        return link_types

    def _resolve_or_create_link_type(self, relation_name: str, type_raw: dict[str, Any]) -> str:
        """Ensure a link type with the given canonical name exists on the target Jira
        instance, creating it if necessary (e.g. a custom link type like "Implements"
        that only exists on the source). Falls back to "Relates" if it cannot be
        resolved or created."""
        link_types = self._fetch_target_link_types()
        existing_name = link_types.get(relation_name.strip().lower())
        if existing_name:
            return existing_name

        if relation_name in self._created_link_type_names:
            # Already attempted (and presumably created or failed) this run; avoid retrying.
            return relation_name if relation_name.strip().lower() in link_types else "Relates"

        self._created_link_type_names.add(relation_name)
        inward_hint = str(type_raw.get("inward")).strip() if isinstance(type_raw, dict) and type_raw.get("inward") else f"is {relation_name.lower()} by"
        outward_hint = str(type_raw.get("outward")).strip() if isinstance(type_raw, dict) and type_raw.get("outward") else relation_name

        try:
            self._request(
                "POST",
                "/issueLinkType",
                {"name": relation_name, "inward": inward_hint, "outward": outward_hint},
            )
            self.logger.info("Created missing issue link type '%s' on target", relation_name)
            link_types[relation_name.strip().lower()] = relation_name
            return relation_name
        except Exception as exc:
            self.logger.warning(
                "Unable to create issue link type '%s' on target (%s); falling back to 'Relates'",
                relation_name,
                exc,
            )
            return "Relates"

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
        value = str(target_key).strip()
        if not value:
            return None
        mapped_key = self.created_issue_keys.get(value)
        if mapped_key:
            return str(mapped_key)
        # Only use explicitly mapped target keys for link resolution. Raw source keys
        # look like Jira issue keys but should not be used in the target system unless
        # they were explicitly resolved by the migration to a target issue.
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
