"""Per-client editorial memory with mem0, stored in the same Postgres through pgvector.

Soft preferences ("avoid the word 'leverage'", "UK spelling") belong here.
Hard facts such as what was published belong in publish_ledger, never here.
"""

from typing import Any

from mem0 import AsyncMemory

from content_pipeline.settings import PipelineSettings


def build_memory(settings: PipelineSettings) -> AsyncMemory:
    config: dict[str, Any] = {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "connection_string": str(settings.DATABASE_URL).replace("+psycopg", ""),
                "collection_name": settings.MEM0_COLLECTION,
                "embedding_model_dims": settings.EMBEDDING_DIMS,
                "hnsw": True,
            },
        },
        "llm": {"provider": "anthropic", "config": {"model": settings.MEM0_LLM_MODEL, "temperature": 0}},
        "embedder": {"provider": "openai", "config": {"model": settings.EMBEDDINGS_MODEL}},
    }
    return AsyncMemory.from_config(config)


async def record_editor_correction(
    memory: AsyncMemory,
    client_id: str,
    editor: str,
    post_slug: str,
    original: str,
    corrected: str,
    note: str = "",
) -> None:
    content = (
        f"Editor {editor} corrected a draft for this client.\n"
        f"Original: {original}\nCorrected: {corrected}\nEditor note: {note}\n"
        "Keep only durable style or tone preferences, not one-off fixes."
    )
    await memory.add(
        [{"role": "user", "content": content}],
        user_id=client_id,
        metadata={"source": "editor_correction", "editor": editor, "post_slug": post_slug},
    )


async def style_notes_for_brief(memory: AsyncMemory, client_id: str, brief_text: str, limit: int = 8) -> list[str]:
    hits = await memory.search(brief_text, top_k=limit, filters={"user_id": client_id})
    return [hit["memory"] for hit in hits["results"]]
