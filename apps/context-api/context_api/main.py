from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import Event, Thread
import time
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status

from .config import Settings
from .db import Database, Principal
from .embedding import EmbeddingError, embed, node_text, session_text, space_text
from .models import (
    ApiKeyCreate,
    ApiKeyResponse,
    ClientRegistration,
    ContextPackRequest,
    ContextPackResponse,
    EdgeInput,
    EdgeRelation,
    FeedbackInput,
    MemoryCreate,
    MemoryResponse,
    SearchRequest,
    SearchResponse,
    SessionIngest,
    SessionClassificationUpdate,
    SessionResponse,
    SpaceCreate,
    SpaceResponse,
)


settings: Settings | None = None
database: Database | None = None
classifier_stop = Event()
classifier_thread: Thread | None = None


def classifier_loop(repository: Database, config: Settings, stop: Event) -> None:
    next_cleanup = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_cleanup:
            repository.cleanup_expired_sessions()
            next_cleanup = time.monotonic() + 3600
        item = repository.claim_session_for_classification()
        if not item:
            stop.wait(config.classifier_poll_seconds)
            continue
        try:
            vector = embed(session_text(item["transcript"]), config)
            repository.classify_session(
                item["tenant_id"],
                item["allowed_space_ids"],
                item["id"],
                item["content_hash"],
                vector,
            )
        except Exception as exc:
            repository.fail_session_classification(
                item["id"], item["content_hash"], str(exc)
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global settings, database, classifier_thread
    settings = Settings.from_env()
    database = Database(settings)
    database.open()
    classifier_stop.clear()
    classifier_thread = Thread(
        target=classifier_loop,
        args=(database, settings, classifier_stop),
        name="context-classifier",
        daemon=True,
    )
    classifier_thread.start()
    try:
        yield
    finally:
        classifier_stop.set()
        classifier_thread.join(timeout=5)
        database.close()


app = FastAPI(
    title="Context Hub",
    version="1.0.0",
    description="Project-scoped shared memory for AI agents.",
    lifespan=lifespan,
)


def db() -> Database:
    if database is None:
        raise HTTPException(status_code=503, detail="database is not initialized")
    return database


def auth(
    authorization: Annotated[str | None, Header()] = None,
    repository: Database = Depends(db),
) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    principal = repository.authenticate(authorization.removeprefix("Bearer ").strip())
    if not principal:
        raise HTTPException(status_code=401, detail="invalid API key")
    return principal


def require(principal: Principal, project_id: str, scope: str) -> None:
    if not principal.permits(project_id, scope):
        raise HTTPException(status_code=403, detail=f"{scope} access denied for project")


def make_embedding(data: MemoryCreate) -> list[float]:
    assert settings is not None
    try:
        return embed(
            node_text(
                data.kind.value,
                data.subject,
                data.title,
                data.content,
                data.tags,
                data.performed_by,
            ),
            settings,
        )
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def make_query_embedding(query: str) -> list[float]:
    assert settings is not None
    try:
        return embed(query, settings)
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def embed_text(text: str) -> list[float]:
    assert settings is not None
    return embed(text, settings)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def ready(repository: Database = Depends(db)) -> dict[str, str]:
    repository.ping()
    make_query_embedding("context hub readiness check")
    if classifier_thread is None or not classifier_thread.is_alive():
        raise HTTPException(status_code=503, detail="classification worker is not running")
    return {"status": "ready"}


@app.post("/v1/register", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
def register_client(
    body: ClientRegistration, repository: Database = Depends(db)
) -> dict:
    assert settings is not None
    client = Principal(
        settings.bootstrap_tenant, body.principal, ("*",), ("read", "write")
    )
    return repository.create_api_key(
        client, body.principal, ["*"], ["read", "write"]
    )


@app.post("/v1/api-keys", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(
    body: ApiKeyCreate,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict:
    if "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="admin access denied")
    if "*" not in principal.project_ids and not set(body.project_ids) <= set(principal.project_ids):
        raise HTTPException(status_code=403, detail="cannot grant access to another project")
    return repository.create_api_key(
        principal, body.principal, body.project_ids, body.scopes
    )


@app.delete("/v1/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: UUID,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> Response:
    if "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="admin access denied")
    if not repository.revoke_api_key(principal, key_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.put("/v1/spaces/{space_id}", response_model=SpaceResponse)
def put_space(
    space_id: str,
    body: SpaceCreate,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict:
    if "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="admin access denied")
    if space_id != body.space_id:
        raise HTTPException(status_code=400, detail="path and body space_id must match")
    try:
        vector = embed_text(space_text(body.display_name, body.description, body.keywords))
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return repository.create_space(principal, body, vector)


@app.get("/v1/spaces", response_model=list[SpaceResponse])
def list_spaces(
    principal: Principal = Depends(auth), repository: Database = Depends(db)
) -> list[dict]:
    if "read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="read access denied")
    return repository.list_spaces(principal)


@app.delete("/v1/spaces/{space_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_space(
    space_id: str,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> Response:
    if "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="admin access denied")
    if not repository.delete_space(principal, space_id):
        raise HTTPException(status_code=404, detail="space not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/v1/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_session(
    body: SessionIngest,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict:
    if "write" not in principal.scopes:
        raise HTTPException(status_code=403, detail="write access denied")
    return repository.ingest_session(principal, body)


@app.get("/v1/sessions", response_model=list[SessionResponse])
def list_sessions(
    classification_status: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> list[dict]:
    if "read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="read access denied")
    allowed = {"pending", "processing", "classified", "unclassified", "failed"}
    if classification_status is not None and classification_status not in allowed:
        raise HTTPException(status_code=400, detail="invalid classification_status")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    return repository.list_sessions(principal, classification_status, limit)


@app.get("/v1/sessions/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: UUID,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict:
    if "read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="read access denied")
    session = repository.get_session(principal, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.delete("/v1/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def purge_session(
    session_id: UUID,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> Response:
    if "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="admin access denied")
    if not repository.purge_session(principal, session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/v1/sessions/{session_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_session_classification(
    session_id: UUID,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict[str, str]:
    if "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="admin access denied")
    if not repository.retry_session_classification(principal, session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"status": "pending"}


@app.put(
    "/v1/sessions/{session_id}/classification", response_model=SessionResponse
)
def correct_session_classification(
    session_id: UUID,
    body: SessionClassificationUpdate,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict:
    if "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="admin access denied")
    try:
        session = repository.correct_session_classification(
            principal, session_id, body.space_ids
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.get("/v1/admin/stats")
def admin_stats(
    principal: Principal = Depends(auth), repository: Database = Depends(db)
) -> dict:
    if "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="admin access denied")
    return repository.session_stats(principal)


@app.post(
    "/v1/memories",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_memory(
    body: MemoryCreate,
    response: Response,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    require(principal, body.project_id, "write")
    if idempotency_key and len(idempotency_key) > 200:
        raise HTTPException(status_code=400, detail="Idempotency-Key is too long")
    try:
        memory, created = repository.create_memory(
            principal, body, make_embedding(body), idempotency_key
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return memory


@app.get("/v1/memories/{memory_id}", response_model=MemoryResponse)
def get_memory(
    memory_id: UUID,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict:
    if "read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="read access denied")
    memory = repository.get_memory(principal, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="memory not found")
    return memory


@app.post("/v1/memories/search", response_model=SearchResponse)
def search_memories(
    body: SearchRequest,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict:
    require(principal, body.project_id, "read")
    items = repository.search(principal, body, make_query_embedding(body.query))
    return {"items": items, "count": len(items)}


@app.post("/v1/context-packs", response_model=ContextPackResponse)
def context_pack(
    body: ContextPackRequest,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict:
    require(principal, body.project_id, "read")
    search_request = SearchRequest(
        **body.model_dump(exclude={"token_budget", "limit"}), limit=50
    )
    candidates = repository.search(
        principal, search_request, make_query_embedding(body.query)
    )
    priority = {"decision": 0, "handoff": 1, "failure": 2, "fact": 3, "artifact": 4}
    candidates.sort(key=lambda item: (priority[item["kind"]], -item["score"]))

    items: list[dict] = []
    estimated_tokens = 0
    for item in candidates:
        item_tokens = max(1, len(item["content"]) // 3)
        if estimated_tokens + item_tokens <= body.token_budget:
            items.append(item)
            estimated_tokens += item_tokens

    decisions: dict[str, list[UUID]] = {}
    for item in items:
        if item["kind"] == "decision":
            decisions.setdefault(item["subject"], []).append(item["id"])
    conflicts = [
        {"subject": subject, "memory_ids": ids}
        for subject, ids in decisions.items()
        if len(ids) > 1
    ]
    return {
        "query": body.query,
        "project_id": body.project_id,
        "generated_at": datetime.now(timezone.utc),
        "token_budget": body.token_budget,
        "estimated_tokens": estimated_tokens,
        "items": items,
        "conflicts": conflicts,
        "omitted_count": len(candidates) - len(items),
    }


@app.post("/v1/memories/{memory_id}/supersede", response_model=MemoryResponse)
def supersede_memory(
    memory_id: UUID,
    body: MemoryCreate,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    require(principal, body.project_id, "write")
    old = repository.get_memory(principal, memory_id)
    if not old:
        raise HTTPException(status_code=404, detail="memory not found")
    if old["project_id"] != body.project_id:
        raise HTTPException(status_code=400, detail="replacement must use the same project")
    if not any(
        edge.target_id == memory_id and edge.relation == EdgeRelation.supersedes
        for edge in body.edges
    ):
        body.edges.append(
            EdgeInput(target_id=memory_id, relation=EdgeRelation.supersedes)
        )
    try:
        memory, _ = repository.create_memory(
            principal, body, make_embedding(body), idempotency_key
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return memory


@app.post("/v1/memories/{memory_id}/expire", response_model=MemoryResponse)
def expire_memory(
    memory_id: UUID,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> dict:
    if "write" not in principal.scopes:
        raise HTTPException(status_code=403, detail="write access denied")
    memory = repository.expire(principal, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="memory not found")
    return memory


@app.post("/v1/memories/{memory_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
def record_feedback(
    memory_id: UUID,
    body: FeedbackInput,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> Response:
    if "read" not in principal.scopes:
        raise HTTPException(status_code=403, detail="read access denied")
    if not repository.feedback(principal, memory_id, body.useful, body.note):
        raise HTTPException(status_code=404, detail="memory not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/v1/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def purge_memory(
    memory_id: UUID,
    principal: Principal = Depends(auth),
    repository: Database = Depends(db),
) -> Response:
    if "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="admin access denied")
    if not repository.purge(principal, memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
