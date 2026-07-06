import importlib
import tempfile
import unittest


class JsonConnectorTests(unittest.TestCase):
    def test_read_project_uses_project_config_when_provided(self):
        connector_module = importlib.import_module("connectors.json_connector")
        JsonConnector = connector_module.JsonConnector

        with tempfile.TemporaryDirectory() as tmp_dir:
            connector = JsonConnector(
                {
                    "type": "json",
                    "location": tmp_dir,
                    "project_info": {
                        "id": "cfg-project",
                        "name": "Configured Project",
                        "description": "Defined in config",
                    },
                }
            )

            project = connector.read_project()

            self.assertEqual(project["id"], "cfg-project")
            self.assertEqual(project["name"], "Configured Project")
            self.assertEqual(project["description"], "Defined in config")


if __name__ == "__main__":
    unittest.main()
