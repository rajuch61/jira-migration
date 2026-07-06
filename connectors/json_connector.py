import json
from pathlib import Path
from typing import Any

from connectors.base_connector import Connector


class JsonConnector(Connector):
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_path = Path(self.config.get("location", "./source")).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def connect(self) -> None:
        self.base_path.mkdir(parents=True, exist_ok=True)

    def read_project(self) -> dict:
        project_file = self.base_path / "project.json"
        if project_file.exists():
            return json.loads(project_file.read_text(encoding="utf-8"))

        configured_project = self.config.get("project") or self.config.get("project_info")
        if isinstance(configured_project, dict):
            return configured_project

        return {}

    def read_issues(self) -> list[dict]:
        issues_file = self.base_path / "issues.json"
        if issues_file.exists():
            data = json.loads(issues_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("issues", [])
        return []

    def create_project(self, project: dict) -> dict:
        target_dir = self.base_path.parent / "target"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "project.json"
        target_path.write_text(json.dumps(project, indent=2), encoding="utf-8")
        return project

    def create_issue(self, issue: dict) -> dict:
        target_dir = self.base_path.parent / "target"
        target_dir.mkdir(parents=True, exist_ok=True)
        issues_path = target_dir / "issues.json"
        existing = []
        if issues_path.exists():
            existing = json.loads(issues_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                existing = existing.get("issues", [])
        existing.append(issue)
        issues_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        return issue

    def update_issue(self, issue_id: str, issue: dict) -> dict:
        target_dir = self.base_path.parent / "target"
        target_dir.mkdir(parents=True, exist_ok=True)
        issues_path = target_dir / "issues.json"
        existing = []
        if issues_path.exists():
            existing = json.loads(issues_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                existing = existing.get("issues", [])
        updated = []
        for item in existing:
            if item.get("id") == issue_id:
                updated.append(issue)
            else:
                updated.append(item)
        issues_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        return issue

    def close(self) -> None:
        return None
