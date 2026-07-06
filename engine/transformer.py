from typing import Any


class Transformer:
    def __init__(self, transformations: dict | None = None):
        self.transformations = transformations or {}

    def transform_issue(self, issue: dict, source_type: str, target_type: str) -> dict:
        transformed = dict(issue)
        transformed["sourceType"] = source_type
        transformed["targetType"] = target_type
        transformed["sourceIssueType"] = transformed.get("issueType")

        if "issueType" in self.transformations:
            transformed["issueType"] = self.transformations["issueType"].get(transformed.get("issueType"), transformed.get("issueType"))

        if "status" in self.transformations:
            transformed["status"] = self.transformations["status"].get(transformed.get("status"), transformed.get("status"))

        for field_name in ("parent", "comments", "attachments", "linked_issues", "history"):
            if field_name in issue:
                transformed[field_name] = issue[field_name]

        return transformed

    def transform_project(self, project: dict) -> dict:
        return {
            "id": project.get("id", "generated-project"),
            "name": project.get("name", "Imported Project"),
            "description": project.get("description", ""),
        }
