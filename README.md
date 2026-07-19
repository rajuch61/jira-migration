# Jira Migration Framework

This project provides a configurable migration framework that can move data between local JSON files and Jira instances. The core migration engine is shared, while the connector layer handles the source and target systems.

## Supported scenarios

- Local JSON to local JSON migration
- Jira to Jira migration
- Jira as source or target with shared migration rules

## What is included

- Config-driven migration engine
- JSON connector for local file-based source and target data
- Jira connector for Jira Cloud or Jira Server/Data Center
- Project and issue transformation support
- Validation rules for required issue fields
- Mapping generation for source-to-target issue IDs
- Structured logging to the logs directory
- Generated output in the target folder

## Project structure

- config/migration.json
- config/environments.json
- connectors/base_connector.py
- connectors/json_connector.py
- connectors/jira_connector.py
- engine/migration_engine.py
- engine/transformer.py
- engine/validator.py
- engine/mapper.py
- source/issues.json
- source/project.json
- target/
- main.py
- run_local.bat
- run_prod.bat
- requirements.txt

## Quick start

1. Install Python 3.13+
2. From the project root, run one of the following:
   - Windows local demo: run_local.bat
   - Windows Jira run: run_prod.bat
   - Or: py -3 main.py --env local
   - Or: py -3 main.py --env prod

## Configuration

The runtime config is split into two files:

- config/migration.json: shared migration behavior and common project metadata
- config/environments.json: environment-specific source/target connector settings

### Shared migration config

Example structure:

```json
{
  "project_info": {
    "id": "shared-project",
    "name": "Shared Project",
    "description": "Configured in migration.json"
  },
  "transformations": {
    "issueType": {
      "Story": "User Story",
      "Task": "Task"
    },
    "status": {
      "To Do": "Open"
    }
  },
  "use_all_fields": true,
  "search_fields": [
    "summary",
    "description",
    "issuetype",
    "status",
    "parent",
    "comment",
    "attachment",
    "issuelinks",
    "assignee",
    "reporter",
    "labels",
    "components",
    "fixVersions",
    "priority",
    "sprint"
  ],
  "validation": {
    "require_summary": true,
    "require_issue_type": true,
    "require_status": true
  },
  "mapping_file": "./target/mapping.json"
}
```

### Environment-specific config

Example for Jira:

```json
{
  "prod": {
    "source": {
      "type": "jira",
      "server": "https://your-source.atlassian.net",
      "project": "ABC",
      "basic_auth": ["user@example.com", "token"],
      "verify_ssl": false,
      "module": "connectors.jira_connector",
      "class": "JiraConnector"
    },
    "target": {
      "type": "jira",
      "server": "https://your-target.atlassian.net",
      "project": "XYZ",
      "basic_auth": ["user@example.com", "token"],
      "verify_ssl": false,
      "module": "connectors.jira_connector",
      "class": "JiraConnector"
    }
  }
}
```

## Supported connector settings

### JSON connector

Required:
- type: json
- location: folder containing project.json and issues.json

Optional:
- project_info: project metadata to use when no project file exists

### Jira connector

Required:
- type: jira
- server: Jira base URL
- basic_auth or username/password/token

Optional:
- project: Jira project key
- project_info.id: shared project key from migration.json
- verify_ssl: true/false
- timeout: request timeout in seconds
- api_path: Jira REST API base path
- use_all_fields: true/false to use Jira `*all` field expansion by default when no explicit search fields are configured
- search_fields: Jira field names to fetch instead of using `*all` or the connector default list

Shared migration config also supports `use_all_fields` and `search_fields` at the top level so they can be defined once in `config/migration.json` instead of per-environment.

### Shared search field configuration
You can define `search_fields` in `config/migration.json` to have the Jira source connector use the same field set across environments. When `search_fields` is configured in `migration.json`, it is copied into the connector config unless the connector already defines its own `search_fields` or `fields`.

## Transformations and validation

The migration engine applies:

- issueType transformation rules from transformations.issueType
- status transformation rules from transformations.status
- validation rules from validation

Validation currently checks that required issue fields are present, including:
- summary
- issue type
- status

## Logging

The project uses a logger configured in utils/logger.py. Runtime logs are written to the logs directory and include:
- migration start and completion
- connector connection attempts
- loaded source issue counts
- migrated issue progress
- validation warnings

## Output files

- target/project.json: written by the JSON connector for the created project
- target/issues.json: written by the JSON connector for created issues
- target/mapping.json: stores source ID to target ID mappings

## Notes

- The shared project metadata now comes from config/migration.json through project_info.
- The environment config is used for environment-specific connector settings such as server, auth, and project key.
- For local JSON runs, source/project.json and source/issues.json are used as sample input data.
