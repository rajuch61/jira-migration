import importlib
from unittest.mock import patch

connector_module = importlib.import_module('connectors.jira_connector')
JiraConnector = connector_module.JiraConnector
connector = JiraConnector({'type':'jira','server':'https://example.atlassian.net','project':'ABC','verify_ssl':False})

issue = {'summary':'x','description':'hello','issueType':'Epic','epic_name':'Epic summary','fixVersions':[{'id':'12604'}]}

attempts = {'count':0}

def side_effect(method, path, payload=None):
    if method == 'POST' and path == '/issue':
        attempts['count'] += 1
        print('attempt', attempts['count'], 'fields', sorted((payload or {}).get('fields', {}).keys()))
        if attempts['count'] == 1:
            raise RuntimeError("Jira request failed (400): {\"errorMessages\":[],\"errors\":{\"customfield_11720\":\"Field \'customfield_11720\' cannot be set. It is not on the appropriate screen, or unknown.\"}}")
        if attempts['count'] == 2:
            if 'fixVersions' in payload['fields'] or 'customfield_11720' in payload['fields']:
                raise RuntimeError("Jira request failed (400): {\"errorMessages\":[],\"errors\":{\"description\":\"Operation value must be a string\",\"fixVersions\":\"Version id \'12604\' is not valid\"}}")
        return {'id':'456','key':'ABC-456'}
    return {}

with patch.object(connector, '_request', side_effect=side_effect) as request_mock:
    try:
        response = connector.create_issue(issue)
        print('response', response)
    except Exception as exc:
        print('raised', type(exc).__name__, exc)

print('call count', request_mock.call_count)
for i, call in enumerate(request_mock.call_args_list):
    print(i+1, call.args[2]['fields'])
