from typing import Any


class ValidationError(Exception):
    pass


class Validator:
    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def validate_issue(self, issue: dict) -> list[str]:
        errors: list[str] = []
        if self.config.get("require_summary", True) and not str(issue.get("summary", "")).strip():
            errors.append("Missing summary")
        if self.config.get("require_issue_type", True) and not str(issue.get("issueType", "")).strip():
            errors.append("Missing issue type")
        if self.config.get("require_status", True) and not str(issue.get("status", "")).strip():
            errors.append("Missing status")
        return errors

    def validate_project(self, project: dict) -> list[str]:
        errors: list[str] = []
        if not str(project.get("name", "")).strip():
            errors.append("Missing project name")
        return errors
