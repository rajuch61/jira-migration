import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


class JiraConnectorTests(unittest.TestCase):
    def test_jira_connector_accepts_basic_auth_config(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        config = {
            "type": "jira",
            "server": "https://example.atlassian.net",
            "basic_auth": ["user@example.com", "token"],
            "project": "ABC",
            "verify_ssl": False,
        }

        connector = JiraConnector(config)

        self.assertEqual(connector.server, "https://example.atlassian.net")
        self.assertEqual(connector.basic_auth, ("user@example.com", "token"))
        self.assertEqual(connector.project, "ABC")
        self.assertFalse(connector.verify_ssl)

    def test_jira_connector_accepts_api_token_for_bearer_auth(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        config = {
            "type": "jira",
            "server": "https://example.atlassian.net",
            "auth_type": "bearer",
            "api_token": "bearer-token-value",
            "project": "ABC",
            "verify_ssl": False,
        }

        connector = JiraConnector(config)

        self.assertEqual(connector.server, "https://example.atlassian.net")
        self.assertEqual(connector.bearer_token, "bearer-token-value")
        self.assertIsNone(connector.basic_auth)

    def test_jira_connector_accepts_api_token_with_username_for_basic_auth(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        config = {
            "type": "jira",
            "server": "https://example.atlassian.net",
            "auth_type": "basic",
            "username": "user@example.com",
            "api_token": "api-token-value",
            "project": "ABC",
            "verify_ssl": False,
        }

        connector = JiraConnector(config)

        self.assertEqual(connector.server, "https://example.atlassian.net")
        self.assertEqual(connector.basic_auth, ("user@example.com", "api-token-value"))
        self.assertIsNone(connector.bearer_token)

    def test_normalizes_repeated_scheme_prefixes(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://https://https//usazrapnjira02.sncorp.smith-nephew.com:8443",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        self.assertEqual(connector.server, "https://usazrapnjira02.sncorp.smith-nephew.com:8443")

    def test_read_project_uses_project_info_from_config(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "project_info": {
                    "id": "cfg-project",
                    "name": "Configured Project",
                    "description": "Defined in config",
                },
                "verify_ssl": False,
            }
        )

        project = connector.read_project()

        self.assertEqual(project["id"], "cfg-project")
        self.assertEqual(project["name"], "Configured Project")
        self.assertEqual(project["description"], "Defined in config")

    def test_read_issues_uses_search_endpoint_with_jql_query(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        with patch.object(connector, "_request", return_value={"issues": []}) as request_mock:
            connector.read_issues()

        expected_path = "/search?jql=project%3D%22ABC%22&maxResults=100&fields=summary%2Cdescription%2Cissuetype%2Cstatus%2Cparent%2Ccomment%2Cattachment%2Cissuelinks%2Ccreated%2Cupdated%2Cassignee%2Creporter&expand=changelog"
        request_mock.assert_any_call("GET", expected_path)

    def test_read_issues_fetches_individual_issues_from_csv_issue_keys(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".csv", delete=False) as handle:
            handle.write("Issue key,Summary\nCSTEST-721,Test 1\nCSTEST-720,Test 2\n")
            csv_path = handle.name

        try:
            connector = JiraConnector(
                {
                    "type": "jira",
                    "server": "https://example.atlassian.net",
                    "project": "ABC",
                    "csv_file": csv_path,
                    "csv_issue_key_column": "Issue key",
                    "verify_ssl": False,
                }
            )

            issue_payload_1 = {
                "id": "10037",
                "key": "CSTEST-721",
                "fields": {
                    "summary": "Test 1",
                    "description": None,
                    "issuetype": {"name": "Task"},
                    "status": {"name": "Open"},
                },
            }
            issue_payload_2 = {
                "id": "10038",
                "key": "CSTEST-720",
                "fields": {
                    "summary": "Test 2",
                    "description": None,
                    "issuetype": {"name": "Task"},
                    "status": {"name": "Open"},
                },
            }

            with patch.object(connector, "_request", side_effect=[[], issue_payload_1, issue_payload_2]) as request_mock:
                issues = connector.read_issues()

            self.assertEqual(len(issues), 2)
            self.assertEqual(request_mock.call_count, 3)
            self.assertEqual(request_mock.call_args_list[1].args[1], "/issue/CSTEST-721?fields=summary%2Cdescription%2Cissuetype%2Cstatus%2Cparent%2Ccomment%2Cattachment%2Cissuelinks%2Ccreated%2Cupdated%2Cassignee%2Creporter&expand=changelog")
            self.assertEqual(request_mock.call_args_list[2].args[1], "/issue/CSTEST-720?fields=summary%2Cdescription%2Cissuetype%2Cstatus%2Cparent%2Ccomment%2Cattachment%2Cissuelinks%2Ccreated%2Cupdated%2Cassignee%2Creporter&expand=changelog")
        finally:
            os.unlink(csv_path)

    def test_read_issues_logs_fetched_issue_details(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        payload = {
            "issues": [
                {
                    "id": "10037",
                    "key": "ABC-1",
                    "fields": {
                        "summary": "Added summary",
                        "description": {"content": [{"content": [{"text": "Detail"}]}]},
                        "issuetype": {"name": "Task"},
                        "status": {"name": "Open"},
                        "parent": {"key": "ABC-0"},
                    },
                }
            ]
        }

        issue_detail_payload = payload["issues"][0]
        search_payload = {"issues": [payload["issues"][0]]}

        with patch.object(connector, "_request", side_effect=[search_payload, issue_detail_payload]), patch.object(connector.logger, "info") as info_mock:
            issues = connector.read_issues()

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["summary"], "Added summary")
        info_mock.assert_any_call(
            "Fetched source issue %s (%s): summary=%r description=%r issueType=%r status=%r parent=%r",
            "10037",
            "ABC-1",
            "Added summary",
            "Detail",
            "Task",
            "Open",
            "ABC-0",
        )

    def test_read_issues_requests_summary_and_description_fields(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        with patch.object(connector, "_request", return_value={"issues": []}) as request_mock:
            connector.read_issues()

        expected_path = "/search?jql=project%3D%22ABC%22&maxResults=100&fields=summary%2Cdescription%2Cissuetype%2Cstatus%2Cparent%2Ccomment%2Cattachment%2Cissuelinks%2Ccreated%2Cupdated%2Cassignee%2Creporter&expand=changelog"
        request_mock.assert_any_call("GET", expected_path)

    def test_read_issues_falls_back_to_key_search_when_search_returns_no_items(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        search_response = {"total": 1, "startAt": 0, "maxResults": 100, "issues": []}
        issue_payload = {
            "id": "10037",
            "key": "ABC-1",
            "fields": {
                "summary": "Added summary",
                "description": {"content": [{"content": [{"text": "Detail"}]}]},
                "issuetype": {"name": "Task"},
                "status": {"name": "Open"},
            },
        }

        with patch.object(connector, "_request", side_effect=[[], search_response, {"issues": [{"key": "ABC-1"}]}, issue_payload]) as request_mock:
            issues = connector.read_issues()

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["summary"], "Added summary")
        self.assertEqual(request_mock.call_args_list[1].args[1], "/search?jql=project%3D%22ABC%22&maxResults=100&fields=summary%2Cdescription%2Cissuetype%2Cstatus%2Cparent%2Ccomment%2Cattachment%2Cissuelinks%2Ccreated%2Cupdated%2Cassignee%2Creporter&expand=changelog")
        self.assertEqual(request_mock.call_args_list[2].args[1], "/search?jql=project%3D%22ABC%22")
        self.assertEqual(request_mock.call_args_list[3].args[1], "/issue/ABC-1?fields=summary%2Cdescription%2Cissuetype%2Cstatus%2Cparent%2Ccomment%2Cattachment%2Cissuelinks%2Ccreated%2Cupdated%2Cassignee%2Creporter&expand=changelog")

    def test_read_issues_paginates_search_results(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        common_fields = {"comment": {"comments": []}, "attachment": [], "issuelinks": []}
        responses = [
            {
                "total": 250,
                "startAt": 0,
                "maxResults": 100,
                "issues": [{"key": "ABC-1", "fields": {"summary": "One", "issuetype": {"name": "Task"}, "status": {"name": "Open"}, **common_fields}}],
            },
            {
                "total": 250,
                "startAt": 100,
                "maxResults": 100,
                "issues": [{"key": "ABC-2", "fields": {"summary": "Two", "issuetype": {"name": "Task"}, "status": {"name": "Open"}, **common_fields}}],
            },
            {
                "total": 250,
                "startAt": 200,
                "maxResults": 100,
                "issues": [{"key": "ABC-3", "fields": {"summary": "Three", "issuetype": {"name": "Task"}, "status": {"name": "Open"}, **common_fields}}],
            },
            {
                "total": 250,
                "startAt": 300,
                "maxResults": 100,
                "issues": [],
            },
        ]

        with patch.object(connector, "_request", side_effect=[[], *responses]) as request_mock:
            issues = connector.read_issues()

        self.assertEqual(len(issues), 3)
        self.assertEqual([issue["key"] for issue in issues], ["ABC-1", "ABC-2", "ABC-3"])
        self.assertGreaterEqual(request_mock.call_count, 3)

    def test_export_project_data_includes_project_and_issues(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        with patch.object(connector, "read_project", return_value={"id": "ABC", "name": "Example"}), patch.object(connector, "read_issues", return_value=[{"key": "ABC-1"}]):
            exported = connector.export_project_data()

        self.assertEqual(exported["project"]["name"], "Example")
        self.assertEqual(exported["issues"][0]["key"], "ABC-1")
        self.assertIn("exported_at", exported["metadata"])

    def test_read_issues_extracts_attachments_links_and_history(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        payload = {
            "issues": [
                {
                    "id": "10037",
                    "key": "ABC-1",
                    "fields": {
                        "summary": "Added summary",
                        "description": {"content": [{"content": [{"text": "Detail"}]}]},
                        "issuetype": {"name": "Task"},
                        "status": {"name": "Open"},
                        "parent": {"key": "ABC-0"},
                        "comment": {"comments": []},
                        "attachment": [{"id": "att-1", "filename": "file.txt", "content": "abc"}],
                        "issuelinks": [{"type": {"name": "Relates"}, "outwardIssue": {"key": "ABC-2"}}],
                    },
                    "changelog": {"histories": [{"created": "2024-01-01", "items": [{"field": "status", "fromString": "Open", "toString": "Done"}]}]},
                }
            ]
        }

        with patch.object(connector, "_request", return_value=payload):
            issues = connector.read_issues()

        self.assertEqual(issues[0]["attachments"][0]["name"], "file.txt")
        self.assertEqual(issues[0]["linked_issues"][0]["target_key"], "ABC-2")
        self.assertEqual(issues[0]["history"][0]["field"], "status")

    def test_create_issue_skips_duplicate_issue_links_for_inverse_relation_names(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["ABC-2"] = "TGT-2"
        connector._link_types_by_name = {"implements": "Implements"}

        with patch.object(connector, "_request", return_value={"id": "456", "key": "ABC-456"}) as request_mock:
            connector._create_issue_links(
                "ABC-1",
                [
                    {"target_key": "ABC-2", "relation": "Implements", "direction": "outward"},
                    {"target_key": "ABC-2", "relation": "Implemented by", "direction": "inward"},
                ],
            )

        self.assertEqual(request_mock.call_count, 1)
        self.assertEqual(request_mock.call_args_list[0].args[2]["type"]["name"], "Implements")

    def test_create_issue_retries_with_default_task_type_when_requested_type_is_invalid(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload):
            if payload["fields"]["issuetype"]["name"] == "Story":
                raise RuntimeError("invalid issue type")
            return {"id": "456", "key": "ABC-456"}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            response = connector.create_issue({"summary": "x", "description": "y", "issueType": "Story"})

        self.assertEqual(response["key"], "ABC-456")
        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(request_mock.call_args_list[1].args[2]["fields"]["issuetype"]["name"], "Task")

    def test_request_retries_transient_url_errors(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
                "retry_count": 2,
                "retry_delay": 0,
            }
        )

        class FakeResponse:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

        with patch.object(connector_module.request, "urlopen", side_effect=[connector_module.error.URLError("temporary"), FakeResponse('{"ok": true}')]) as request_mock:
            response = connector._request("GET", "/myself")

        self.assertEqual(response, {"ok": True})
        self.assertEqual(request_mock.call_count, 2)

    def test_create_issue_sends_description_in_adf_format(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        with patch.object(connector, "_request", return_value={"id": "456", "key": "ABC-456"}) as request_mock:
            connector.create_issue({"summary": "x", "description": "hello", "issueType": "Story"})

        self.assertEqual(
            request_mock.call_args.args[2]["fields"]["description"],
            {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "hello"}],
                    }
                ],
            },
        )

    def test_create_issue_includes_epic_name_field_for_epics(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
                "epic_name_field": "customfield_10104",
            }
        )

        with patch.object(connector, "_request", return_value={"id": "456", "key": "ABC-456"}) as request_mock:
            connector.create_issue({"summary": "Epic summary", "description": "hello", "issueType": "Epic", "epic_name": "Epic summary"})

        self.assertEqual(request_mock.call_args.args[2]["fields"]["customfield_10104"], "Epic summary")

    def test_create_issue_retries_with_plain_description_when_adf_is_rejected(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "POST" and path == "/issue":
                if isinstance(payload["fields"]["description"], dict):
                    raise RuntimeError("Jira request failed (400): {\"errorMessages\":[],\"errors\":{\"description\":\"Operation value must be a string\"}}")
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            response = connector.create_issue({"summary": "x", "description": "hello", "issueType": "Story"})

        self.assertEqual(response["key"], "ABC-456")
        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(request_mock.call_args_list[1].args[2]["fields"]["description"], "hello")

    def test_create_issue_returns_metadata_fields_for_export(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["10037"] = "ABC-1"

        with patch.object(connector, "_request", return_value={"id": "456", "key": "ABC-456"}), patch.object(connector, "_create_comments"), patch.object(connector, "_create_attachments"), patch.object(connector, "_create_issue_links"):
            result = connector.create_issue(
                {
                    "id": "10051",
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Sub-task",
                    "sourceIssueType": "Sub-task",
                    "parent": "10037",
                    "comments": [{"body": "hello"}],
                    "attachments": [{"name": "a.txt", "content": "abc"}],
                    "linked_issues": [{"target_key": "ABC-2", "relation": "Relates"}],
                    "history": [{"field": "status"}],
                }
            )

        self.assertEqual(result["parent"], "ABC-1")
        self.assertEqual(result["comments"], [{"body": "hello"}])
        self.assertEqual(result["attachments"], [{"name": "a.txt", "content": "abc"}])
        self.assertEqual(result["linked_issues"], [{"target_key": "ABC-2", "relation": "Relates"}])
        self.assertEqual(result["history"], [{"field": "status"}])

    def test_create_issue_prefers_a_supported_subtask_type_for_source_subtasks(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["10037"] = "ABC-1"

        def side_effect(method, path, payload=None):
            if method == "GET" and path == "/issue/createmeta/ABC/issuetypes":
                return {"values": [{"name": "Subtask", "subtask": True}]}
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect):
            connector.create_issue({"id": "10051", "summary": "x", "description": "hello", "issueType": "Sub-task", "sourceIssueType": "Sub-task", "parent": "10037"})

        self.assertEqual(connector.created_issue_keys["10051"], "ABC-456")

    def test_create_issue_supports_real_jira_cloud_createmeta_response_shape(self):
        # Jira Cloud's actual /issue/createmeta/{key}/issuetypes response nests issue
        # types under "issueTypes", not "values". A previous bug only checked "values",
        # which silently made every sub-task fall back to a plain Task + "Relates" link.
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["10037"] = "ABC-1"

        def side_effect(method, path, payload=None):
            if method == "GET" and path == "/issue/createmeta/ABC/issuetypes":
                return {"issueTypes": [{"name": "Sub-task", "subtask": True}]}
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue({"id": "10051", "summary": "x", "description": "hello", "issueType": "Sub-task", "sourceIssueType": "Sub-task", "parent": "10037"})

        create_call = request_mock.call_args_list[1]
        self.assertEqual(create_call.args[2]["fields"]["issuetype"]["name"], "Sub-task")
        self.assertEqual(create_call.args[2]["fields"]["parent"], {"key": "ABC-1"})

    def test_create_project_uses_target_project_override(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "basic_auth": ["user@example.com", "token"],
                "target_project": {
                    "id": "TARGET",
                    "name": "Target Project",
                    "description": "Target description",
                    "lead_account_id": "account-123",
                },
                "verify_ssl": False,
            }
        )

        with patch.object(connector, "_request", return_value={}) as request_mock:
            connector.create_project({"id": "ABC", "name": "Source Project", "description": "Source description"})

        request_mock.assert_called_once_with(
            "POST",
            "/project",
            {
                "key": "TARGET",
                "name": "Target Project",
                "description": "Target description",
                "projectTypeKey": "software",
                "leadAccountId": "account-123",
            },
        )

    def test_read_issues_extracts_comment_text_and_timestamps_from_adf_body(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        payload = {
            "issues": [
                {
                    "id": "10037",
                    "key": "ABC-1",
                    "fields": {
                        "summary": "Added summary",
                        "description": {"content": [{"content": [{"text": "Detail"}]}]},
                        "issuetype": {"name": "Task"},
                        "status": {"name": "Open"},
                        "comment": {
                            "comments": [
                                {
                                    "id": "c-1",
                                    "body": {
                                        "type": "doc",
                                        "version": 1,
                                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}],
                                    },
                                    "created": "2024-01-02T03:04:05.000Z",
                                    "updated": "2024-01-02T03:05:06.000Z",
                                }
                            ]
                        },
                        "attachment": [],
                        "issuelinks": [],
                    },
                }
            ]
        }

        with patch.object(connector, "_request", return_value=payload):
            issues = connector.read_issues()

        self.assertEqual(issues[0]["comments"][0]["body"], "Hello")
        self.assertEqual(issues[0]["comments"][0]["created"], "2024-01-02T03:04:05.000Z")
        self.assertEqual(issues[0]["comments"][0]["updated"], "2024-01-02T03:05:06.000Z")

    def test_create_issue_posts_comment_body_as_plain_text(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload):
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Story",
                    "comments": [
                        {
                            "body": {
                                "type": "doc",
                                "version": 1,
                                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}],
                            }
                        }
                    ],
                }
            )

        self.assertEqual(
            request_mock.call_args_list[1].args[2]["body"],
            "Hello",
        )

    def test_create_issue_posts_comments_after_issue_creation(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload):
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Story",
                    "comments": [{"body": "First comment", "author": "Alice"}],
                }
            )

        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(request_mock.call_args_list[1].args[1], "/issue/456/comment")
        self.assertEqual(
            request_mock.call_args_list[1].args[2]["body"],
            "First comment",
        )

    def test_create_issue_does_not_add_provenance_prefix_to_comments(self):
        # Comments are posted with their original body text as-is, with no injected
        # "(originally posted ... by ...)" prefix - the target's native comment
        # author/timestamp on the Jira UI reflects the API call itself, and that is
        # not annotated in the comment body.
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload):
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Story",
                    "comments": [
                        {"body": "Test 1", "author": "Hemanth.Raghav", "created": "2026-07-23T19:23:38.020+0000"},
                        {"body": "test 2", "author": "Hemanth.Raghav", "created": "2026-07-23T19:23:43.477+0000"},
                    ],
                }
            )

        self.assertEqual(request_mock.call_args_list[1].args[2]["body"], "Test 1")
        self.assertEqual(request_mock.call_args_list[2].args[2]["body"], "test 2")

    def test_create_issue_resolves_assignee_and_reporter_on_target_by_email(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "GET" and path.startswith("/user/search?query=assignee%40example.com"):
                return [{"accountId": "target-assignee-account-id"}]
            if method == "GET" and path.startswith("/user/search?query=reporter%40example.com"):
                return [{"accountId": "target-reporter-account-id"}]
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Story",
                    "assignee": {"emailAddress": "assignee@example.com", "displayName": "Assignee Person"},
                    "reporter": {"emailAddress": "reporter@example.com", "displayName": "Reporter Person"},
                }
            )

        create_call = next(call for call in request_mock.call_args_list if call.args[0] == "POST" and call.args[1] == "/issue")
        fields = create_call.args[2]["fields"]
        self.assertEqual(fields["assignee"], {"accountId": "target-assignee-account-id"})
        self.assertEqual(fields["reporter"], {"accountId": "target-reporter-account-id"})

    def test_create_issue_omits_assignee_when_no_matching_target_user_found(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "GET" and path.startswith("/user/search"):
                return []
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Story",
                    "assignee": {"emailAddress": "nobody@example.com"},
                }
            )

        create_call = next(call for call in request_mock.call_args_list if call.args[0] == "POST" and call.args[1] == "/issue")
        self.assertNotIn("assignee", create_call.args[2]["fields"])

    def test_create_issue_resolves_sprint_via_agile_api_for_top_level_issue(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "TM",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "GET" and path == "/field":
                return [{"id": "customfield_10020", "name": "Sprint"}]
            if method == "GET" and path.startswith("https://example.atlassian.net/rest/agile/1.0/board?"):
                return {"values": [{"id": 37}]}
            if method == "GET" and path.startswith("https://example.atlassian.net/rest/agile/1.0/board/37/sprint"):
                return {"values": [{"id": 91, "name": "TM Sprint 1"}], "isLast": True}
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "TM-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Story",
                    "sprint": "TM Sprint 1",
                }
            )

        create_call = next(call for call in request_mock.call_args_list if call.args[0] == "POST" and call.args[1] == "/issue")
        self.assertEqual(create_call.args[2]["fields"]["customfield_10020"], 91)

    def test_create_issue_skips_sprint_field_for_subtasks(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "TM",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "GET" and path == "/field":
                return [{"id": "customfield_10020", "name": "Sprint"}]
            if method == "POST" and path == "/issue":
                return {"id": "789", "key": "TM-789"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Sub-task",
                    "sprint": "TM Sprint 1",
                }
            )

        create_call = next(call for call in request_mock.call_args_list if call.args[0] == "POST" and call.args[1] == "/issue")
        self.assertNotIn("customfield_10020", create_call.args[2]["fields"])
        agile_calls = [call for call in request_mock.call_args_list if "agile" in call.args[1]]
        self.assertEqual(agile_calls, [])

    def test_create_issue_maps_original_created_to_existing_date_created_field(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "TM",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "GET" and path == "/field":
                return [{"id": "customfield_10337", "name": "Date Created"}]
            if method == "POST" and path == "/issue":
                return {"id": "500", "key": "TM-500"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Task",
                    "originalCreated": "2020-01-15T10:30:00.000+0000",
                }
            )

        create_call = next(call for call in request_mock.call_args_list if call.args[0] == "POST" and call.args[1] == "/issue")
        self.assertEqual(create_call.args[2]["fields"]["customfield_10337"], "2020-01-15T10:30:00.000+0000")
        field_creation_calls = [call for call in request_mock.call_args_list if call.args[0] == "POST" and call.args[1] == "/field"]
        self.assertEqual(field_creation_calls, [])

    def test_create_issue_creates_date_created_field_and_provisions_screens_when_missing(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "TM",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "GET" and path == "/field":
                return []
            if method == "POST" and path == "/field":
                return {"id": "customfield_10337", "name": "Date Created"}
            if method == "GET" and path == "/project/TM":
                return {"id": "10034"}
            if method == "GET" and path == "/issuetypescreenscheme/project?projectId=10034":
                return {"values": [{"issueTypeScreenScheme": {"id": "10037"}}]}
            if method == "GET" and path == "/issuetypescreenscheme/mapping?issueTypeScreenSchemeId=10037":
                return {"values": [{"screenSchemeId": "10055"}]}
            if method == "GET" and path == "/screenscheme?id=10055":
                return {"values": [{"screens": {"default": 10055}}]}
            if method == "GET" and path == "/screens/10055/tabs":
                return {"id": 10058, "name": "Field Tab"}
            if method == "POST" and path == "/screens/10055/tabs/10058/fields":
                return {}
            if method == "POST" and path == "/issue":
                return {"id": "501", "key": "TM-501"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Task",
                    "originalCreated": "2020-01-15T10:30:00.000+0000",
                }
            )

        create_call = next(call for call in request_mock.call_args_list if call.args[0] == "POST" and call.args[1] == "/issue")
        self.assertEqual(create_call.args[2]["fields"]["customfield_10337"], "2020-01-15T10:30:00.000+0000")
        screen_field_calls = [call for call in request_mock.call_args_list if call.args[0] == "POST" and call.args[1] == "/screens/10055/tabs/10058/fields"]
        self.assertEqual(len(screen_field_calls), 1)

    def test_create_issue_skips_date_created_field_when_original_created_missing(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "TM",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "POST" and path == "/issue":
                return {"id": "502", "key": "TM-502"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Task",
                }
            )

        create_call = next(call for call in request_mock.call_args_list if call.args[0] == "POST" and call.args[1] == "/issue")
        self.assertNotIn("customfield_10337", create_call.args[2]["fields"])
        field_lookup_calls = [call for call in request_mock.call_args_list if call.args[0] == "GET" and call.args[1] == "/field"]
        self.assertEqual(field_lookup_calls, [])

    def test_create_issue_replays_allowed_history_fields_as_target_updates(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Story",
                    "history": [
                        {"field": "description", "from": "old text", "to": "new text"},
                        {"field": "Priority", "from": "Low", "to": "High"},
                        {"field": "Labels", "from": "", "to": "urgent backend"},
                        {"field": "status", "from": "Open", "to": "In Progress"},
                        {"field": "Link", "from": None, "to": "This issue relates to ABC-1"},
                    ],
                }
            )

        put_calls = [call for call in request_mock.call_args_list if call.args[0] == "PUT"]
        self.assertEqual(len(put_calls), 3)
        self.assertEqual(put_calls[0].args[1], "/issue/456")
        self.assertEqual(put_calls[0].args[2], {"fields": {"description": "new text"}})
        self.assertEqual(put_calls[1].args[2], {"fields": {"priority": {"name": "High"}}})
        self.assertEqual(put_calls[2].args[2], {"fields": {"labels": ["urgent", "backend"]}})

    def test_create_issue_skips_history_replay_when_no_history(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload=None):
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Story",
                }
            )

        put_calls = [call for call in request_mock.call_args_list if call.args[0] == "PUT"]
        self.assertEqual(put_calls, [])

    def test_request_sends_binary_attachment_payload_as_multipart(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        class DummyResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"{}"

        with patch("connectors.jira_connector.request.urlopen", return_value=DummyResponse()) as urlopen_mock:
            response = connector._request("POST", "/issue/123/attachments", {"file": ("file.txt", b"abc")})

        self.assertEqual(response, {})
        request_obj = urlopen_mock.call_args.args[0]
        self.assertTrue(request_obj.get_header("Content-type").startswith("multipart/form-data; boundary="))
        self.assertEqual(request_obj.get_header("X-Atlassian-Token"), "no-check")
        self.assertIsInstance(request_obj.data, bytes)

    def test_download_attachment_from_url(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        class DummyResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"hello-world"

        with patch("connectors.jira_connector.request.urlopen", return_value=DummyResponse()) as urlopen_mock:
            result = connector._download_attachment("https://example.atlassian.net/secure/attachment/1/file.txt")

        self.assertEqual(result, b"hello-world")
        request_obj = urlopen_mock.call_args.args[0]
        self.assertEqual(request_obj.get_header("Authorization"), None)

    def test_create_issue_posts_attachments_after_issue_creation(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        def side_effect(method, path, payload):
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Story",
                    "attachments": [{"name": "file.txt", "content": "abc"}],
                }
            )

        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(request_mock.call_args_list[1].args[1], "/issue/456/attachments")

    def test_create_issue_migrates_source_subtasks_as_tasks_and_links_them_to_parents(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["10037"] = "ABC-10037"

        def side_effect(method, path, payload):
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue(
                {
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Sub-task",
                    "parent": "10037",
                    "sourceIssueType": "Sub-task",
                }
            )

        self.assertEqual(request_mock.call_count, 3)
        self.assertEqual(request_mock.call_args_list[1].args[2]["fields"]["issuetype"]["name"], "Task")
        self.assertEqual(request_mock.call_args_list[2].args[1], "/issueLink")
        self.assertEqual(request_mock.call_args_list[2].args[2]["outwardIssue"]["key"], "ABC-10037")
        self.assertNotIn("parent", request_mock.call_args_list[1].args[2]["fields"])

    def test_create_issue_uses_task_type_for_source_subtasks_with_parent(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["10037"] = "ABC-1"

        with patch.object(connector, "_request", return_value={"id": "456", "key": "ABC-456"}) as request_mock:
            connector.create_issue({"id": "10051", "summary": "x", "description": "hello", "issueType": "Task", "sourceIssueType": "Sub-task", "parent": "10037"})

        self.assertEqual(request_mock.call_args.args[2]["fields"]["issuetype"]["name"], "Task")
        self.assertEqual(request_mock.call_args.args[2]["fields"]["parent"], {"key": "ABC-1"})

    def test_create_issue_retries_pending_subtask_after_parent_is_created(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )

        with patch.object(connector, "_request", return_value={"id": "456", "key": "ABC-456"}) as request_mock:
            result = connector.create_issue({"id": "10051", "summary": "x", "description": "hello", "issueType": "Sub-task", "parent": "10037"})
            self.assertIsNone(result["key"])

            connector.created_issue_keys["10037"] = "ABC-1"
            connector._process_pending_child_issues()

        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(request_mock.call_args.args[2]["fields"]["parent"], {"key": "ABC-1"})

    def test_create_issue_uses_migrated_parent_key_for_subtasks(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["10037"] = "ABC-1"

        with patch.object(connector, "_request", return_value={"id": "456", "key": "ABC-456"}) as request_mock:
            connector.create_issue({"id": "10051", "summary": "x", "description": "hello", "issueType": "Sub-task", "parent": "10037"})

        self.assertEqual(request_mock.call_args.args[2]["fields"]["parent"], {"key": "ABC-1"})

    def test_create_issue_uses_migrated_parent_key_for_parent_id(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["10037"] = "ABC-1"

        with patch.object(connector, "_request", return_value={"id": "456", "key": "ABC-456"}) as request_mock:
            connector.create_issue({"id": "10051", "summary": "x", "description": "hello", "issueType": "Sub-task", "parent_id": "10037"})

        self.assertEqual(request_mock.call_args.args[2]["fields"]["parent"], {"key": "ABC-1"})

    def test_create_issue_links_creates_links_for_a_migrated_issue(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["10037"] = "ABC-1"

        def side_effect(method, path, payload):
            if method == "POST" and path == "/issue":
                return {"id": "456", "key": "ABC-456"}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            created = connector.create_issue(
                {
                    "id": "10051",
                    "summary": "x",
                    "description": "hello",
                    "issueType": "Task",
                    "linked_issues": [{"target_key": "ABC-2", "relation": "Relates"}],
                }
            )
            # Links are not created inline; they are created explicitly once every
            # issue in the migration batch exists, so link targets always resolve.
            self.assertEqual(request_mock.call_count, 1)

            connector.created_issue_keys["ABC-2"] = "TGT-2"
            # Pre-seed the target link type cache so this test focuses purely on link
            # creation/resolution, not on link-type discovery/creation (covered by
            # dedicated tests below).
            connector._link_types_by_name = {"relates": "Relates"}
            connector.create_issue_links(created["key"], created["linked_issues"])

        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(request_mock.call_args_list[1].args[1], "/issueLink")
        self.assertEqual(request_mock.call_args_list[1].args[2]["outwardIssue"], {"key": "TGT-2"})

    def test_create_issue_links_creates_missing_link_type_on_target_when_absent(self):
        # Some source Jira instances have custom link types (e.g. "1 Implements",
        # canonicalized to "Implements") that don't exist on the target Jira Cloud
        # instance at all. The migration must create the missing link type on the
        # target rather than send a payload that Jira Cloud will reject with 404.
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["ABC-1"] = "TGT-1"
        connector.created_issue_keys["ABC-2"] = "TGT-2"

        def side_effect(method, path, payload):
            if method == "GET" and path == "/issueLinkType":
                return {"issueLinkTypes": [{"id": "10003", "name": "Relates", "inward": "relates to", "outward": "relates to"}]}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue_links(
                "TGT-1",
                [
                    {
                        "target_key": "ABC-2",
                        "relation": "1 Implements",
                        "direction": "outward",
                        "type_raw": {"id": "10500", "name": "1 Implements", "inward": "Implemented by", "outward": "Implements"},
                    }
                ],
            )

        calls = request_mock.call_args_list
        self.assertEqual(calls[0].args[:2], ("GET", "/issueLinkType"))
        self.assertEqual(
            calls[1].args,
            ("POST", "/issueLinkType", {"name": "Implements", "inward": "Implemented by", "outward": "Implements"}),
        )
        self.assertEqual(
            calls[2].args,
            (
                "POST",
                "/issueLink",
                {
                    "type": {"name": "Implements"},
                    "inwardIssue": {"key": "TGT-1"},
                    "outwardIssue": {"key": "TGT-2"},
                },
            ),
        )

    def test_create_issue_links_reuses_existing_target_link_type_name(self):
        # If the target already has a link type matching the canonicalized relation
        # name (case-insensitively), reuse its exact configured name and never try
        # to create a duplicate link type.
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["ABC-1"] = "TGT-1"
        connector.created_issue_keys["ABC-2"] = "TGT-2"

        def side_effect(method, path, payload):
            if method == "GET" and path == "/issueLinkType":
                return {"issueLinkTypes": [{"id": "10000", "name": "Blocks", "inward": "is blocked by", "outward": "blocks"}]}
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue_links(
                "TGT-1",
                [{"target_key": "ABC-2", "relation": "1 Blocks", "direction": "outward"}],
            )

        calls = request_mock.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[:2], ("GET", "/issueLinkType"))
        self.assertEqual(calls[1].args[0], "POST")
        self.assertEqual(calls[1].args[1], "/issueLink")
        self.assertEqual(calls[1].args[2]["type"], {"name": "Blocks"})

    def test_create_issue_links_falls_back_to_relates_when_link_type_creation_fails(self):
        # If creating the missing link type on the target fails (e.g. insufficient
        # permissions), fall back to the universally-available "Relates" type rather
        # than dropping the link entirely.
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "verify_ssl": False,
            }
        )
        connector.created_issue_keys["ABC-1"] = "TGT-1"
        connector.created_issue_keys["ABC-2"] = "TGT-2"

        def side_effect(method, path, payload):
            if method == "GET" and path == "/issueLinkType":
                return {"issueLinkTypes": []}
            if method == "POST" and path == "/issueLinkType":
                raise RuntimeError("Jira request failed (403): Forbidden")
            return {}

        with patch.object(connector, "_request", side_effect=side_effect) as request_mock:
            connector.create_issue_links(
                "TGT-1",
                [{"target_key": "ABC-2", "relation": "1 Implements", "direction": "outward"}],
            )

        calls = request_mock.call_args_list
        self.assertEqual(calls[-1].args[0], "POST")
        self.assertEqual(calls[-1].args[1], "/issueLink")
        self.assertEqual(calls[-1].args[2]["type"], {"name": "Relates"})

    def test_create_project_uses_current_account_id_when_email_is_configured(self):
        connector_module = importlib.import_module("connectors.jira_connector")
        JiraConnector = connector_module.JiraConnector

        connector = JiraConnector(
            {
                "type": "jira",
                "server": "https://example.atlassian.net",
                "project": "ABC",
                "target_project": {
                    "id": "TARGET",
                    "name": "Target Project",
                    "description": "Target description",
                    "lead": "user@example.com",
                },
                "verify_ssl": False,
            }
        )
        connector.current_account_id = "account-456"

        with patch.object(connector, "_request", return_value={}) as request_mock:
            connector.create_project({"id": "ABC", "name": "Source Project", "description": "Source description"})

        request_mock.assert_called_once_with(
            "POST",
            "/project",
            {
                "key": "TARGET",
                "name": "Target Project",
                "description": "Target description",
                "projectTypeKey": "software",
                "leadAccountId": "account-456",
            },
        )


if __name__ == "__main__":
    unittest.main()
