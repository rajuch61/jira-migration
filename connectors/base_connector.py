from abc import ABC, abstractmethod


class Connector(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_project(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def read_issues(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def create_project(self, project: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    def create_issue(self, issue: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    def update_issue(self, issue_id: str, issue: dict) -> dict:
        raise NotImplementedError

    def create_issue_links(self, issue_id, linked_issues: list) -> None:
        """Create issue links for a previously created issue. Default is a no-op;
        connectors that support issue linking (e.g. Jira) should override this."""
        return None

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
