import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings


class EmbeddingError(RuntimeError):
    pass


def node_text(
    kind: str,
    subject: str,
    title: str,
    content: str,
    tags: list[str],
    performed_by: list[str],
) -> str:
    return "\n".join(
        (
            f"kind: {kind}",
            f"subject: {subject}",
            f"title: {title}",
            f"content: {content}",
            f"tags: {', '.join(tags)}",
            f"performed by: {', '.join(performed_by)}",
        )
    )


def space_text(name: str, description: str, keywords: list[str]) -> str:
    return "\n".join(
        (f"work: {name}", f"description: {description}", f"keywords: {', '.join(keywords)}")
    )


def session_text(transcript: str) -> str:
    # ponytail: embedding a representative head/tail avoids adding a transcript
    # parser for two unstable JSONL formats. Add provider parsers if accuracy demands it.
    if len(transcript) <= 16_000:
        return transcript
    return transcript[:4_000] + "\n...[middle omitted for classification]...\n" + transcript[-12_000:]


def embed(text: str, settings: Settings) -> list[float]:
    body = json.dumps(
        {"model": settings.embedding_model, "input": text}, ensure_ascii=False
    ).encode()
    request = Request(
        f"{settings.ollama_url}/api/embed",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise EmbeddingError(f"local embedding service unavailable: {exc}") from exc

    vectors = payload.get("embeddings")
    if not vectors or not vectors[0]:
        raise EmbeddingError("local embedding service returned no vector")
    return [float(value) for value in vectors[0]]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"
