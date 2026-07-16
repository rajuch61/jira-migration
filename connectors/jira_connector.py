import base64
import json
import os
import re
import ssl
import uuid
from typing import Any
from urllib import error, request

from connectors.base_connector import Connector
from utils.logger import get_logger


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
        self.connected = False
        self.current_account_id = None
        self.created_issue_keys: dict[str, str] = {}
        self.created_issue_ids: dict[str, str] = {}
        self.pending_issue_links: list[tuple[Any, dict[str, Any]]] = []
        self.pending_child_issues: list[dict[str, Any]] = []
        self._processing_pending_child_issues = False

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
        normalized_path = path.lstrip("/")
        if normalized_path.startswith(base_path.lstrip("/")):
            return f"{self.server}/{normalized_path}"
        return f"{self.server}/{base_path}/{normalized_path}"

    def _request(self, method: str, path: str, payload: Any = None, *, content_type: str | None = None) -> Any:
        url = self._build_url(path)
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

        req = request.Request(url, data=data, headers=headers, method=method)
        if "/attachments" in path:
            req.add_header("X-Atlassian-Token", "no-check")
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
                return json.loads(body) if body else None
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Jira request failed ({exc.code}): {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Unable to reach Jira server: {exc}") from exc

    def connect(self) -> None:
        if not self.server:
            raise ValueError("Jira server URL is required")
        user_info = self._request("GET", "/myself")
        self.current_account_id = self._extract_account_id(user_info)
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
        if not self.project:
            return []

        query = f'project="{self.project}"'
        data = self._request(
            "POST",
            "/search/jql",
            {
                "jql": query,
                "maxResults": 100,
                "fields": ["summary", "description", "issuetype", "status", "parent", "comment", "attachment", "issuelinks"],
            },
        )
        issues = []
        for item in data.get("issues", []):
            fields = item.get("fields", {})
            issue = {
                "id": item.get("id"),
                "key": item.get("key"),
                "summary": fields.get("summary", ""),
                "description": self._extract_description(fields.get("description")),
                "issueType": fields.get("issuetype", {}).get("name", "Task"),
                "parent": fields.get("parent", {}).get("key"),
                "status": fields.get("status", {}).get("name", "Open"),
                "comments": [
                    {
                        "id": comment.get("id"),
                        "body": self._extract_comment_text(comment.get("body")),
                        "author": comment.get("author", {}).get("displayName"),
                        "created": comment.get("created"),
                        "updated": comment.get("updated"),
                    }
                    for comment in fields.get("comment", {}).get("comments", [])
                ],
                "attachments": [
                    {
                        "id": attachment.get("id"),
                        "name": attachment.get("filename") or attachment.get("name"),
                        "content": attachment.get("content"),
                    }
                    for attachment in fields.get("attachment", [])
                    if isinstance(attachment, dict)
                ],
                "linked_issues": [
                    {
                        "target_key": link.get("outwardIssue", {}).get("key") or link.get("inwardIssue", {}).get("key"),
                        "relation": link.get("type", {}).get("name"),
                    }
                    for link in fields.get("issuelinks", [])
                    if isinstance(link, dict)
                ],
                "history": [
                    {
                        "field": history_item.get("field"),
                        "from": history_item.get("fromString"),
                        "to": history_item.get("toString"),
                    }
                    for history in item.get("changelog", {}).get("histories", [])
                    for history_item in history.get("items", [])
                    if isinstance(history, dict)
                ],
            }
            self.logger.info(
                "Fetched source issue %s (%s): summary=%r description=%r issueType=%r status=%r parent=%r comments=%d attachments=%d linked_issues=%d",
                issue.get("id"),
                issue.get("key"),
                issue.get("summary"),
                issue.get("description"),
                issue.get("issueType"),
                issue.get("status"),
                issue.get("parent"),
                len(issue.get("comments", []) or []),
                len(issue.get("attachments", []) or []),
                len(issue.get("linked_issues", []) or []),
            )
            issues.append(issue)
        return issues

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
            payload["leadAccountId"] = lead
        try:
            self._request("POST", "/project", payload)
        except Exception as exc:
            self.logger.warning("Project creation skipped: %s", exc)
        return target_project

    def create_issue(self, issue: dict) -> dict:
        target_project = self._resolve_target_project()
        issue_type = issue.get("issueType") or "Task"
        target_project_key = (target_project.get("id") or self.project or self.config.get("project") or self.config.get("project_key") or "MIG").strip().upper()
        parent_reference = issue.get("parent") or issue.get("parent_id")
        source_issue_type = issue.get("sourceIssueType") or issue.get("source_issue_type")
        is_source_subtask = self._is_subtask_issue_type(source_issue_type)
        preferred_issue_type = "Sub-task" if is_source_subtask else issue_type
        resolved_issue_type = self._resolve_issue_type(preferred_issue_type)
        parent_key = None
        target_subtask_issue_type = None
        if is_source_subtask:
            target_subtask_issue_type = self._resolve_target_subtask_issue_type(target_project_key)
            if target_subtask_issue_type:
                resolved_issue_type = target_subtask_issue_type
        payload = {
            "fields": {
                "project": {"key": target_project_key},
                "summary": issue.get("summary", ""),
                "description": self._to_adf(issue.get("description", "")),
                "issuetype": {"name": resolved_issue_type},
            }
        }
        if is_source_subtask and parent_reference:
            parent_key = self._resolve_parent_key(parent_reference)
            if not parent_key:
                parent_key = self._resolve_parent_key(issue.get("parent"))
            if parent_key:
                payload["fields"]["parent"] = {"key": str(parent_key)}
            else:
                self.logger.info("Deferring subtask %s until parent exists", issue.get("key") or issue.get("id") or issue.get("summary"))
                self.pending_child_issues.append(dict(issue))
                return {
                    "id": None,
                    "key": None,
                    "summary": issue.get("summary", ""),
                    "description": issue.get("description", ""),
                    "issueType": issue.get("issueType", "Task"),
                    "status": issue.get("status", "Open"),
                    "deferred": True,
                }

        try:
            response = self._request("POST", "/issue", payload)
        except Exception as exc:
            fallback_issue_type = self._resolve_issue_type("Task")
            if is_source_subtask and parent_reference:
                self.logger.warning("Sub-task issue %r was rejected by Jira; creating it as a task and linking to the parent instead: %s", issue.get("summary") or issue.get("key"), exc)
                payload["fields"]["issuetype"] = {"name": fallback_issue_type}
                payload["fields"].pop("parent", None)
                response = self._request("POST", "/issue", payload)
            elif self._is_subtask_issue_type(resolved_issue_type) or self._is_subtask_issue_type(issue_type):
                self.logger.warning("Sub-task issue %r was rejected by Jira; retrying as %r: %s", issue.get("summary") or issue.get("key"), fallback_issue_type, exc)
                payload["fields"]["issuetype"] = {"name": fallback_issue_type}
                response = self._request("POST", "/issue", payload)
            elif fallback_issue_type != payload["fields"]["issuetype"]["name"]:
                self.logger.warning("Issue type %r rejected, retrying with %r: %s", issue_type, fallback_issue_type, exc)
                payload["fields"]["issuetype"] = {"name": fallback_issue_type}
                response = self._request("POST", "/issue", payload)
            else:
                raise

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
            for key in ("accountId", "account_id", "accountid"):
                value = user_info.get(key)
                if isinstance(value, str) and value.strip():
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

    def _resolve_target_subtask_issue_type(self, target_project_key: str) -> str | None:
        if not target_project_key:
            return None
        try:
            data = self._request("GET", f"/issue/createmeta/{target_project_key}/issuetypes")
        except Exception:
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
            if self._looks_like_issue_key(str(value)):
                return str(value)
            if str(value).isdigit():
                return str(value)
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
            try:
                payload = {
                    "type": {"name": link.get("relation") or "Relates"},
                    "inwardIssue": {"key": str(issue_id)},
                    "outwardIssue": {"key": str(resolved_target_key)},
                }
                self._request("POST", "/issueLink", payload)
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
            try:
                payload = {
                    "type": {"name": link.get("relation") or "Relates"},
                    "inwardIssue": {"key": str(issue_id)},
                    "outwardIssue": {"key": str(resolved_target_key)},
                }
                self._request("POST", "/issueLink", payload)
            except Exception as exc:
                self.logger.warning("Issue link skipped for %s: %s", target_key, exc)

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
