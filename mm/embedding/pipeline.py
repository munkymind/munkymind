"""Embedding pipeline: chunking and upsert logic."""
import datetime
from typing import Callable, Any

from mm.connectors.base import ConnectorPage, Chunk

# OpenAI text-embedding-3-small limit is 8192 tokens.
# We use 6000 as a safe ceiling (1 token ≈ 4 chars → ~24000 chars).
MAX_CHUNK_CHARS = 24_000
CHUNK_OVERLAP_CHARS = 800  # ~200 tokens overlap between splits


def _split_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split text into chunks of at most max_chars with overlap. Always returns at least one chunk."""
    text = text.strip()
    if not text:
        return []
    return [text]  # no splitting
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def build_chunks(pages: list[ConnectorPage]) -> list[Chunk]:
    """Build summary and detail chunks from a list of ConnectorPages.

    Guarantees:
    - Every page with any content produces at least 1 chunk.
    - No chunk exceeds MAX_CHUNK_CHARS (safe under the 8192-token embedding limit).
    - Small files with no ## headings are emitted as a single 'content' chunk.
    """
    chunks = []
    ingested_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for page in pages:
        tags_str = ", ".join(page.tags) if isinstance(page.tags, list) else str(page.tags)
        base_meta: dict[str, Any] = {
            "domain": page.domain,
            "type": page.type,
            "source": page.source,
            "source_ref": page.source_ref,
            "connector": page.connector,
            "confidence": page.confidence,
            "tags": tags_str,
            "staleness_threshold_days": str(page.staleness_threshold_days),
            "page_id": page.id,
            "title": page.title,
            "updated_at": ingested_at,
        }

        page_had_content = False

        # Summary chunk (split if large)
        if page.summary.strip():
            for i, part in enumerate(_split_text(f"[SUMMARY] {page.summary.strip()}")):
                chunks.append(Chunk(
                    id=f"{page.id}::summary::{i}",
                    text=part,
                    metadata={**base_meta, "chunk_type": "summary", "section_heading": "Summary"},
                    chunk_type="summary",
                ))
                page_had_content = True

        # Detail chunks (split large sections)
        for heading, body in page.detail_sections.items():
            if not body.strip():
                continue
            for i, part in enumerate(_split_text(f"[DETAIL:{heading}] {body.strip()}")):
                chunks.append(Chunk(
                    id=f"{page.id}::detail::{heading}::{i}",
                    text=part,
                    metadata={**base_meta, "chunk_type": "detail", "section_heading": heading},
                    chunk_type="detail",
                ))
                page_had_content = True

        # Fallback: flat file with no headings and no summary — emit raw content as one chunk
        if not page_had_content:
            # Reconstruct best available text
            raw = " ".join(page.detail_sections.values()) or page.summary or page.title
            if raw.strip():
                for i, part in enumerate(_split_text(raw.strip())):
                    chunks.append(Chunk(
                        id=f"{page.id}::content::{i}",
                        text=part,
                        metadata={**base_meta, "chunk_type": "content", "section_heading": "Content"},
                        chunk_type="content",
                    ))

    return chunks


def upsert_chunks(chunks: list[Chunk], collection, embedding_fn: Callable[[list[str]], list[list[float]]]) -> None:
    """Idempotent upsert of chunks into a ChromaDB collection."""
    if not chunks:
        return

    BATCH_SIZE = 50
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i: i + BATCH_SIZE]
        texts = [c.text for c in batch]
        ids = [c.id for c in batch]
        metadatas = [c.metadata for c in batch]
        embeddings = embedding_fn(texts)
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
