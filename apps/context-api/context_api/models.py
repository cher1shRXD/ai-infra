from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


SLUG = r"^[a-z0-9](?:[a-z0-9._/-]{0,126}[a-z0-9])?$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryKind(str, Enum):
    fact = "fact"
    decision = "decision"
    artifact = "artifact"
    handoff = "handoff"
    failure = "failure"


class SourceType(str, Enum):
    agent_run = "agent_run"
    git = "git"
    issue = "issue"
    document = "document"
    human = "human"


class Visibility(str, Enum):
    project = "project"
    restricted = "restricted"


class EdgeRelation(str, Enum):
    supersedes = "supersedes"
    derived_from = "derived_from"
    supports = "supports"
    blocks = "blocks"
    related_to = "related_to"


class EdgeInput(StrictModel):
    target_id: UUID
    relation: EdgeRelation


class MemoryCreate(StrictModel):
    project_id: str = Field(min_length=1, max_length=128, pattern=SLUG)
    namespace: str = Field(min_length=1, max_length=128, pattern=SLUG)
    kind: MemoryKind
    subject: str = Field(min_length=3, max_length=200)
    title: str = Field(min_length=3, max_length=200)
    content: str = Field(min_length=20, max_length=8000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_type: SourceType
    source_uri: str = Field(min_length=1, max_length=1000)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    author_agent_id: str = Field(min_length=1, max_length=200)
    performed_by: list[str] = Field(min_length=1, max_length=20)
    run_id: str | None = Field(default=None, max_length=200)
    confidence: float = Field(default=0.8, ge=0, le=1)
    valid_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    visibility: Visibility = Visibility.project
    allowed_principals: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    edges: list[EdgeInput] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_memory(self) -> "MemoryCreate":
        self.tags = sorted({tag.strip().lower() for tag in self.tags if tag.strip()})
        self.performed_by = list(
            dict.fromkeys(item.strip() for item in self.performed_by if item.strip())
        )
        if not self.performed_by or any(len(item) > 200 for item in self.performed_by):
            raise ValueError("performed_by requires 1-20 names of at most 200 characters")
        self.allowed_principals = sorted(
            {item.strip() for item in self.allowed_principals if item.strip()}
        )
        if self.visibility == Visibility.restricted and not self.allowed_principals:
            raise ValueError("restricted memories require allowed_principals")
        if self.expires_at and self.expires_at <= self.valid_from:
            raise ValueError("expires_at must be later than valid_from")
        return self


class SearchRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128, pattern=SLUG)
    query: str = Field(min_length=2, max_length=2000)
    namespace: str | None = Field(default=None, max_length=128, pattern=SLUG)
    kinds: list[MemoryKind] = Field(default_factory=list, max_length=5)
    tags: list[str] = Field(default_factory=list, max_length=20)
    performed_by: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=10, ge=1, le=50)
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContextPackRequest(SearchRequest):
    token_budget: int = Field(default=3000, ge=200, le=30000)


class FeedbackInput(StrictModel):
    useful: bool
    note: str | None = Field(default=None, max_length=1000)


class ApiKeyCreate(StrictModel):
    principal: str = Field(min_length=1, max_length=200)
    project_ids: list[str] = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])

    @model_validator(mode="after")
    def validate_key(self) -> "ApiKeyCreate":
        self.project_ids = sorted({item.strip() for item in self.project_ids if item.strip()})
        self.scopes = sorted({item.strip() for item in self.scopes if item.strip()})
        if not self.project_ids:
            raise ValueError("at least one project_id is required")
        if not self.scopes or not set(self.scopes) <= {"read", "write"}:
            raise ValueError("issued keys may contain only read or write scopes")
        return self


class ClientRegistration(StrictModel):
    principal: str = Field(min_length=1, max_length=200)


class ApiKeyResponse(StrictModel):
    id: UUID
    api_key: str
    tenant_id: str
    principal: str
    project_ids: list[str]
    scopes: list[str]
    created_at: datetime


class SpaceCreate(StrictModel):
    space_id: str = Field(min_length=1, max_length=128, pattern=SLUG)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=10, max_length=4000)
    keywords: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def normalize_keywords(self) -> "SpaceCreate":
        self.keywords = sorted(
            {item.strip().lower() for item in self.keywords if item.strip()}
        )
        return self


class SpaceResponse(StrictModel):
    space_id: str
    display_name: str
    description: str
    keywords: list[str]
    created_at: datetime


class SessionProvider(str, Enum):
    codex = "codex"
    claude = "claude"


class SessionIngest(StrictModel):
    provider: SessionProvider
    provider_session_id: str = Field(min_length=1, max_length=300)
    model: str | None = Field(default=None, max_length=200)
    cwd: str | None = Field(default=None, max_length=2000)
    transcript: str = Field(min_length=1, max_length=20_000_000)
    transcript_format: str = Field(default="jsonl", max_length=50)
    transcript_truncated: bool = False


class SessionResponse(StrictModel):
    id: UUID
    provider: SessionProvider
    provider_session_id: str
    model: str | None
    cwd: str | None
    transcript_format: str
    transcript_truncated: bool
    classification_status: str
    classifications: list[dict[str, Any]]
    captured_at: datetime
    updated_at: datetime


class SessionClassificationUpdate(StrictModel):
    space_ids: list[str] = Field(max_length=3)

    @model_validator(mode="after")
    def normalize_space_ids(self) -> "SessionClassificationUpdate":
        self.space_ids = list(dict.fromkeys(self.space_ids))
        return self


class MemoryResponse(StrictModel):
    id: UUID
    tenant_id: str
    project_id: str
    namespace: str
    kind: MemoryKind
    subject: str
    title: str
    content: str
    tags: list[str]
    embedding_model: str
    embedding_dimensions: int
    source_type: SourceType
    source_uri: str
    source_status: str
    evidence: list[str]
    author_agent_id: str
    performed_by: list[str]
    run_id: str | None
    confidence: float
    status: str
    valid_from: datetime
    expires_at: datetime | None
    visibility: Visibility
    allowed_principals: list[str]
    metadata: dict[str, Any]
    edges: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class SearchHit(MemoryResponse):
    score: float
    vector_score: float
    text_score: float


class SearchResponse(StrictModel):
    items: list[SearchHit]
    count: int


class ContextPackResponse(StrictModel):
    query: str
    project_id: str
    generated_at: datetime
    token_budget: int
    estimated_tokens: int
    items: list[SearchHit]
    conflicts: list[dict[str, Any]]
    omitted_count: int
