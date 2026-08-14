"""Post-migration helper to recreate valid parent tasks and re-create subtasks.

Usage:
  - Set environment variables: `JIRA_BASE`, `JIRA_USER`, `JIRA_TOKEN` (API token)
  - From repo root run: `python scripts/fix_subtasks.py --target target/issues.json`

The script finds `Sub-task` entries in the target export whose parent is missing
or whose parent is an Epic (Epic cannot be a parent of subtasks in Cloud). For
each such parent reference it creates a replacement `Task` in the same project
and then recreates the sub-task under that new parent using the Jira REST API.

This script is idempotent for created parent placeholders: it records the
created parent mapping in a local JSON file `target/created_parent_mapping.json`
so you can re-run safely.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

import requests


def load_issues(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"Expected a list of issues in {path}")
    return data


def jira_get(session: requests.Session, base: str, path: str) -> requests.Response:
    url = f"{base.rstrip('/')}" + path
    return session.get(url)


def jira_post(session: requests.Session, base: str, path: str, payload: dict) -> requests.Response:
    url = f"{base.rstrip('/')}" + path
    return session.post(url, json=payload)


def ensure_parent_tasks(session: requests.Session, base: str, subtasks: List[Dict[str, Any]], mapping_file: Path) -> Dict[str, str]:
    """Create replacement parent Tasks for invalid parents and return mapping
    {original_parent_key: new_parent_key}
    """
    mapping: Dict[str, str] = {}
    if mapping_file.exists():
        mapping = json.loads(mapping_file.read_text(encoding="utf-8"))

    # Unique parent keys referenced by subtasks
    parents = sorted({str(item.get("parent")) for item in subtasks if item.get("parent")})

    for parent_key in parents:
        if not parent_key or parent_key in mapping:
            continue

        # Check existing parent on target
        resp = jira_get(session, base, f"/rest/api/2/issue/{parent_key}?fields=issuetype")
        if resp.status_code == 200:
            try:
                payload = resp.json()
                issuetype = payload.get("fields", {}).get("issuetype", {})
                # If parent is Epic, we'll create a replacement; otherwise keep existing
                if str(issuetype.get("name", "")).lower() != "epic":
                    print(f"Parent {parent_key} exists and is not Epic; no replacement needed")
                    mapping[parent_key] = parent_key
                    continue
            except Exception:
                pass

        # Parent missing or is an Epic: create a Task placeholder
        project_key = parent_key.split("-")[0] if "-" in parent_key else None
        if not project_key:
            print(f"Cannot infer project from parent key {parent_key}; skipping")
            continue

        create_payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": f"Migrated parent placeholder for {parent_key}",
                "description": "Auto-created by post-migration fix script as a valid parent for subtasks.",
                "issuetype": {"name": "Task"},
            }
        }
        print(f"Creating replacement parent Task for {parent_key} in project {project_key}...", end=" ")
        create_resp = jira_post(session, base, "/rest/api/2/issue", create_payload)
        if create_resp.status_code in (200, 201):
            created = create_resp.json()
            new_key = created.get("key")
            print(f"done -> {new_key}")
            mapping[parent_key] = new_key
            mapping_file.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
        else:
            print(f"failed ({create_resp.status_code}): {create_resp.text}")

    return mapping


def recreate_subtasks(session: requests.Session, base: str, subtasks: List[Dict[str, Any]], mapping: Dict[str, str]) -> None:
    for st in subtasks:
        original_parent = str(st.get("parent") or "")
        if not original_parent:
            continue
        parent_key = mapping.get(original_parent) or original_parent

        project_key = original_parent.split("-")[0] if "-" in original_parent else None
        if not project_key:
            print(f"Skipping subtask {st.get('key')}: cannot infer project")
            continue

        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": st.get("summary") or "Migrated Sub-task",
                "description": st.get("description") or "",
                "issuetype": {"name": "Sub-task"},
                "parent": {"key": parent_key},
            }
        }

        print(f"Creating sub-task for source {st.get('key')} under parent {parent_key}...", end=" ")
        resp = jira_post(session, base, "/rest/api/2/issue", payload)
        if resp.status_code in (200, 201):
            created = resp.json()
            print(f"done -> {created.get('key')}")
        else:
            print(f"failed ({resp.status_code}): {resp.text}")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="target/issues.json", help="Path to exported target issues.json")
    parser.add_argument("--mapping-file", default="target/created_parent_mapping.json", help="File to store created parent mapping")
    parser.add_argument("--dry-run", action="store_true", help="Do not perform POSTs, only print actions")
    args = parser.parse_args(argv)

    # Allow inspecting locally with --dry-run without requiring credentials.
    jira_base = os.getenv("JIRA_BASE")
    jira_user = os.getenv("JIRA_USER")
    jira_token = os.getenv("JIRA_TOKEN")
    if not args.dry_run and (not jira_base or not jira_user or not jira_token):
        print("Please set JIRA_BASE, JIRA_USER and JIRA_TOKEN environment variables")
        return 2

    target_path = Path(args.target)
    if not target_path.exists():
        print(f"Target issues file not found: {target_path}")
        return 2

    issues = load_issues(target_path)
    subtasks = [i for i in issues if str(i.get("issueType") or "").lower() == "sub-task" or str(i.get("issueType") or "").lower() == "subtask"]
    if not subtasks:
        print("No subtasks found in target export; nothing to do.")
        return 0

    session = requests.Session()
    if not args.dry_run:
        session.auth = (jira_user, jira_token)
    session.headers.update({"Accept": "application/json"})

    mapping_file = Path(args.mapping_file)

    if args.dry_run:
        print(f"Dry-run mode: would create parents for {len(subtasks)} subtasks")
        return 0

    mapping = ensure_parent_tasks(session, jira_base, subtasks, mapping_file)
    recreate_subtasks(session, jira_base, subtasks, mapping)
    print("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
