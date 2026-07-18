#!/usr/bin/env python3
"""Test different JQL formats to find working syntax."""

import json
import urllib.request
import urllib.error
import ssl

# Disable SSL verification
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

bearer_token = "Test"
server = "https://usazrapnjiira02.sncorp.smith-nephew.com:8443"
api_path = "/rest/api/2"

# Test different JQL formats
test_cases = [
    ("Format 1: project = CSTEST (spaces)", {"jql": "project = CSTEST", "startAt": 0, "maxResults": 50}),
    ("Format 2: project in (CSTEST)", {"jql": "project in (CSTEST)", "startAt": 0, "maxResults": 50}),
    ("Format 3: project = 'CSTEST' (quoted)", {"jql": "project = 'CSTEST'", "startAt": 0, "maxResults": 50}),
    ("Format 4: project = \"CSTEST\" (double quoted)", {"jql": 'project = "CSTEST"', "startAt": 0, "maxResults": 50}),
    ("Format 5: key >= CSTEST-0 (range)", {"jql": "key >= CSTEST-0", "startAt": 0, "maxResults": 50}),
    ("Format 6: No JQL, all fields", {"startAt": 0, "maxResults": 50}),
]

for test_name, payload in test_cases:
    print(f"\n{'='*70}")
    print(f"Testing: {test_name}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    print(f"{'='*70}")
    
    url = f"{server}{api_path}/search"
    
    try:
        # Encode payload
        data = json.dumps(payload).encode('utf-8')
        
        # Create request
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                'Authorization': f'Bearer {bearer_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            method='POST'
        )
        
        # Make request
        with urllib.request.urlopen(req, context=ssl_context) as response:
            response_data = json.loads(response.read().decode('utf-8'))
            print(f"✅ SUCCESS")
            print(f"   Total Issues: {response_data.get('total', 'N/A')}")
            print(f"   Max Results: {response_data.get('maxResults', 'N/A')}")
            print(f"   Issues Returned: {len(response_data.get('issues', []))}")
            
            # Show first issue if any
            if response_data.get('issues'):
                first_issue = response_data['issues'][0]
                print(f"   First Issue: {first_issue.get('key', 'N/A')} - {first_issue.get('fields', {}).get('summary', 'N/A')[:50]}")
    
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP Error: {e.code}")
        error_body = e.read().decode('utf-8')
        try:
            error_json = json.loads(error_body)
            print(f"   Error: {error_json.get('errorMessages', error_json)}")
        except:
            print(f"   Response: {error_body[:200]}")
    
    except Exception as e:
        print(f"❌ Error: {e}")

print(f"\n{'='*70}")
print("Test complete!")
