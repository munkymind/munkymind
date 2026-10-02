"""User configuration dataclass with YAML load/save."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DomainConfig:
    id: str
    label: str
    staleness_threshold_days: int = 30


@dataclass
class LLMConfig:
    provider: str = field(default_factory=lambda: default_llm()[0])
    model: str = field(default_factory=lambda: default_llm()[1])


def _has_key(name: str) -> bool:
    # Docker Compose turns an empty `KEY=   # comment` line into the value "# comment".
    value = (os.environ.get(name) or "").strip()
    return bool(value) and not value.startswith("#")


def default_llm() -> tuple[str, str]:
    """Answer model for a new user: the provider they have a key for, so one OpenAI key
    works without the wizard (e.g. `munkymind user create`). Anthropic if both are set."""
    if _has_key("ANTHROPIC_API_KEY") or not _has_key("OPENAI_API_KEY"):
        return "anthropic", "claude-haiku-4-5"
    return "openai", "gpt-4o-mini"


@dataclass
class EmbedConfig:
    provider: str = 'openai'
    model: str = 'text-embedding-3-small'


@dataclass
class UserConfig:
    user_id: str
    llm: LLMConfig
    embedding: EmbedConfig
    domains: list[DomainConfig]
    connectors: list[dict]

    DEFAULT_DOMAINS = [
        DomainConfig('health', 'Health', 30),
        DomainConfig('professional', 'Professional', 14),
        DomainConfig('personal', 'Personal', 60),
        DomainConfig('strategic', 'Strategic', 7),
        DomainConfig('temporal', 'Temporal', 3),
        DomainConfig('projects', 'Projects', 7),
    ]

    @classmethod
    def default(cls, user_id: str) -> 'UserConfig':
        return cls(
            user_id=user_id,
            llm=LLMConfig(),
            embedding=EmbedConfig(),
            domains=list(cls.DEFAULT_DOMAINS),
            connectors=[],
        )

    @classmethod
    def load(cls, path: Path) -> 'UserConfig':
        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f)
        return cls(
            user_id=data['user_id'],
            llm=LLMConfig(**data.get('llm', {})),
            embedding=EmbedConfig(**data.get('embedding', {})),
            domains=[DomainConfig(**d) for d in data.get('domains', [])],
            connectors=data.get('connectors', []),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'user_id': self.user_id,
            'llm': asdict(self.llm),
            'embedding': asdict(self.embedding),
            'domains': [asdict(d) for d in self.domains],
            'connectors': self.connectors,
        }
        with open(path, 'w') as f:
            yaml.safe_dump(data, f, default_flow_style=False)
