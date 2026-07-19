import json
import tempfile
import unittest
from pathlib import Path

from engine.migration_engine import MigrationEngine


class MigrationEngineTests(unittest.TestCase):
    def test_common_project_info_is_applied_to_all_connectors(self):
        config = {
            "project_info": {
                "id": "shared-project",
                "name": "Shared Project",
                "description": "Defined in migration config",
            },
            "source": {
                "type": "json",
                "location": "./source",
            },
            "target": {
                "type": "json",
                "location": "./target",
            },
        }

        engine = MigrationEngine(config)

        self.assertEqual(engine.source_connector.config.get("project_info")["id"], "shared-project")
        self.assertEqual(engine.target_connector.config.get("project_info")["name"], "Shared Project")

    def test_shared_search_fields_are_applied_to_jira_connector(self):
        config = {
            "search_fields": ["summary", "description", "assignee"],
            "source": {"type": "jira", "server": "https://example.atlassian.net", "project": "ABC"},
            "target": {"type": "json", "location": "./target"},
        }

        engine = MigrationEngine(config)

        self.assertEqual(engine.source_connector.config.get("search_fields"), ["summary", "description", "assignee"])

    def test_shared_use_all_fields_is_applied_to_jira_connector(self):
        config = {
            "use_all_fields": False,
            "source": {"type": "jira", "server": "https://example.atlassian.net", "project": "ABC"},
            "target": {"type": "json", "location": "./target"},
        }

        engine = MigrationEngine(config)

        self.assertIs(engine.source_connector.config.get("use_all_fields"), False)

    def test_export_writes_source_and_target_issues_to_local_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "source": {"type": "json", "location": str(root / "source")},
                "target": {"type": "json", "location": str(root / "target")},
            }
            engine = MigrationEngine(config)
            source_issues = [{"id": "1", "summary": "One"}]
            target_issues = [{"id": "1", "summary": "One", "targetType": "json"}]

            engine._write_issue_exports(source_issues, target_issues)

            self.assertEqual(json.loads((root / "source" / "issues.json").read_text(encoding="utf-8")), source_issues)
            self.assertEqual(json.loads((root / "target" / "issues.json").read_text(encoding="utf-8")), target_issues)


if __name__ == "__main__":
    unittest.main()
