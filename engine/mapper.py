import csv
import json
from pathlib import Path


class Mapper:
    def __init__(self, output_path: str | None = None):
        self.output_path = Path(output_path) if output_path else None
        self.mapping: dict[str, str] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.records: dict[str, dict[str, str]] = {}

    def add_mapping(self, source_key: str, target_key: str) -> None:
        self.mapping[source_key] = target_key

    def add_metadata(self, source_key: str, target_key: str, *, issue_type: str | None = None, status: str | None = None) -> None:
        self.metadata[source_key] = {
            "target_key": target_key,
            "issue_type": issue_type or "",
            "status": status or "",
        }

    def add_issue_mapping(
        self,
        source_id: str,
        source_key: str | None,
        target_key: str,
        target_id: str | None = None,
        *,
        issue_type: str | None = None,
        status: str | None = None,
    ) -> None:
        if source_id:
            self.add_mapping(source_id, target_key)
            self.add_metadata(source_id, target_key, issue_type=issue_type, status=status)

        if source_key:
            self.add_mapping(source_key, target_key)
            self.add_metadata(source_key, target_key, issue_type=issue_type, status=status)

        self.records[source_id] = {
            "source_id": source_id,
            "source_key": source_key or "",
            "target_id": target_id or "",
            "target_key": target_key,
            "issue_type": issue_type or "",
            "status": status or "",
        }

    def save(self) -> None:
        if not self.output_path:
            return
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(self.mapping, indent=2), encoding="utf-8")

        metadata_path = self.output_path.with_name("mapping_metadata.json")
        metadata_path.write_text(json.dumps(self.metadata, indent=2), encoding="utf-8")

        records_path = self.output_path.with_name("mapping_records.json")
        records_path.write_text(json.dumps(list(self.records.values()), indent=2), encoding="utf-8")

        csv_path = self.output_path.with_name("mapping_records.csv")
        with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=["SourceId", "SourceKey", "TargetId", "TargetKey", "IssueType", "Status"],
            )
            writer.writeheader()
            for record in self.records.values():
                writer.writerow(
                    {
                        "SourceId": record["source_id"],
                        "SourceKey": record["source_key"],
                        "TargetId": record["target_id"],
                        "TargetKey": record["target_key"],
                        "IssueType": record["issue_type"],
                        "Status": record["status"],
                    }
                )
