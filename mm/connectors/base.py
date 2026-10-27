from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Any


@dataclass
class ConnectorPage:
    id: str
    domain: str
    type: str
    title: str
    summary: str
    detail_sections: dict[str, str]  # heading -> body text
    source: str
    source_ref: str
    connector: str
    confidence: str = 'medium'
    tags: list[str] = field(default_factory=list)
    staleness_threshold_days: int = 30
    source_updated_at: str | None = None  # when the source last changed (e.g. file mtime, UTC ISO)


@dataclass
class Chunk:
    id: str  # e.g. 'page-id::summary' or 'page-id::detail::heading'
    text: str
    metadata: dict[str, Any]
    chunk_type: str  # 'summary' or 'detail'


class BaseConnector(ABC):
    connector_id: str
    display_name: str
    description: str

    def __init__(self, config: dict[str, Any], user_config: Any):
        self.config = config
        self.user_config = user_config

    @abstractmethod
    def validate(self) -> tuple[bool, str]: ...

    @abstractmethod
    def schema(self) -> dict[str, Any]: ...

    @abstractmethod
    def ingest(self, progress_cb=None) -> list[ConnectorPage]: ...
