import importlib
import json
from pathlib import Path
from typing import Any

from connectors.json_connector import JsonConnector
from connectors.jira_connector import JiraConnector
from engine.mapper import Mapper
from engine.transformer import Transformer
from engine.validator import Validator
from utils.logger import get_logger


class MigrationEngine:
    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger()
        self.validator = Validator(config.get("validation", {}))
        self.transformer = Transformer(config.get("transformations", {}))
        self.mapper = Mapper(config.get("mapping_file"))
        self.source_connector = self._create_connector(config.get("source", {}), config)
        self.target_connector = self._create_connector(config.get("target", {}), config)

    @classmethod
    def from_file(cls, config_path: str | Path) -> "MigrationEngine":
        path = Path(config_path)
        config = json.loads(path.read_text(encoding="utf-8"))
        return cls(config)

    def _create_connector(self, connector_config: dict, shared_config: dict | None = None):
        connector_type = connector_config.get("type", "json").lower()
        module_name = connector_config.get("module")
        class_name = connector_config.get("class")

        effective_config = dict(connector_config)
        if shared_config:
            project_info = shared_config.get("project_info")
            if isinstance(project_info, dict) and "project_info" not in effective_config:
                effective_config["project_info"] = project_info

            target_project = shared_config.get("target_project")
            if isinstance(target_project, dict) and "target_project" not in effective_config:
                effective_config["target_project"] = target_project

            shared_search_fields = shared_config.get("search_fields")
            if shared_search_fields is not None and "search_fields" not in effective_config and "fields" not in effective_config:
                effective_config["search_fields"] = shared_search_fields

            shared_use_all_fields = shared_config.get("use_all_fields")
            if shared_use_all_fields is not None and "use_all_fields" not in effective_config:
                effective_config["use_all_fields"] = shared_use_all_fields

        if module_name and class_name:
            module = importlib.import_module(module_name)
            connector_class = getattr(module, class_name)
            return connector_class(effective_config)

        if connector_type == "jira":
            return JiraConnector(effective_config)

        return JsonConnector(effective_config)

    def run(self) -> None:
        self.logger.info("Starting migration")
        self.source_connector.connect()
        self.target_connector.connect()

        source_project = self.source_connector.read_project()
        source_issues = self.source_connector.read_issues()
        self._write_issue_exports(source_issues, [])
        self.logger.info("Loaded %s issues from source", len(source_issues))

        transformed_project = self.transformer.transform_project(source_project)
        project_errors = self.validator.validate_project(transformed_project)
        if project_errors:
            self.logger.error("Project validation failed: %s", project_errors)
            raise ValueError(project_errors)

        target_project = self.target_connector.create_project(transformed_project)
        self.logger.info("Created project '%s'", target_project.get("name"))

        migrated = 0
        created_target_issues: list[dict] = []
        ordered_issues = self._order_issues_for_creation(source_issues)

        for issue in ordered_issues:
            transformed_issue = self.transformer.transform_issue(issue, self.config.get("source", {}).get("type", "json"), self.config.get("target", {}).get("type", "json"))
            validation_errors = self.validator.validate_issue(transformed_issue)
            if validation_errors:
                self.logger.warning("Skipping issue %s due to %s", transformed_issue.get("id"), validation_errors)
                continue

            target_issue = self.target_connector.create_issue(transformed_issue)
            if target_issue.get("deferred"):
                self.logger.info("Deferred issue %s until parent exists", issue.get("id"))
                continue
            target_key = str(target_issue.get("key") or target_issue.get("id") or issue.get("id"))
            self.mapper.add_mapping(str(issue.get("id")), target_key)
            if issue.get("key"):
                self.mapper.add_mapping(str(issue.get("key")), target_key)
            created_target_issues.append(target_issue)
            migrated += 1
            self.logger.info("Migrated issue %s", issue.get("id"))

        self._write_issue_exports(source_issues, created_target_issues)
        self.mapper.save()
        self.logger.info("Migration completed. %s issues migrated", migrated)

        self.source_connector.close()
        self.target_connector.close()

    def _write_issue_exports(self, source_issues: list[dict], target_issues: list[dict]) -> None:
        project_root = Path(__file__).resolve().parent.parent
        workspace_root = project_root

        def resolve_dir(configured_location: Any, default_name: str) -> Path:
            if isinstance(configured_location, str) and configured_location.strip():
                path = Path(configured_location)
                if not path.is_absolute():
                    path = (workspace_root / path).resolve()
                return path
            return (workspace_root / default_name).resolve()

        source_dir = resolve_dir(self.config.get("source", {}).get("location"), "source")
        target_dir = resolve_dir(self.config.get("target", {}).get("location"), "target")
        source_dir.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        source_path = source_dir / "issues.json"
        target_path = target_dir / "issues.json"
        source_path.write_text(json.dumps(source_issues, indent=2), encoding="utf-8")
        target_path.write_text(json.dumps(target_issues, indent=2), encoding="utf-8")

        self.logger.info("Wrote %s source issues to %s", len(source_issues), source_path)
        self.logger.info("Wrote %s target issues to %s", len(target_issues), target_path)

    def _order_issues_for_creation(self, issues: list[dict]) -> list[dict]:
        issue_lookup = {}
        for issue in issues:
            if issue.get("id") is not None:
                issue_lookup[str(issue.get("id"))] = issue
            if issue.get("key"):
                issue_lookup[str(issue.get("key"))] = issue

        ordered: list[dict] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(issue: dict) -> None:
            if not isinstance(issue, dict):
                return
            issue_id = str(issue.get("id") or issue.get("key") or "")
            if not issue_id:
                return
            if issue_id in visited:
                return
            if issue_id in visiting:
                return

            visiting.add(issue_id)
            parent_ref = issue.get("parent")
            if parent_ref:
                parent_issue = issue_lookup.get(str(parent_ref))
                if isinstance(parent_issue, dict):
                    parent_id = str(parent_issue.get("id") or parent_issue.get("key") or "")
                    if parent_id and parent_id != issue_id:
                        visit(parent_issue)

            visiting.remove(issue_id)
            visited.add(issue_id)
            ordered.append(issue)

        for issue in issues:
            visit(issue)
        return ordered
