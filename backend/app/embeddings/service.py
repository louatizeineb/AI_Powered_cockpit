from __future__ import annotations
from sqlalchemy import text
from app.db import SessionLocal
from app.catalog.path_parser import build_embedding_text
from app.embeddings.provider import embed_text
from app.embeddings.repository import save_embeddings_batch
from app.config import get_settings

settings = get_settings()


def _count_pending(model_name: str, replace_existing: bool) -> int:
    if replace_existing:
        query = "SELECT count(*) FROM catalog_path_index"
        params = {}
    else:
        query = """
            SELECT count(*)
            FROM catalog_path_index c
            LEFT JOIN catalog_node_embeddings e
              ON e.catalog_path_index_id = c.id
             AND e.model_name = :model_name
            WHERE e.id IS NULL
        """
        params = {"model_name": model_name}

    with SessionLocal() as db:
        return int(db.execute(text(query), params).scalar() or 0)


def _fetch_batch(last_id: int, batch_size: int, model_name: str, replace_existing: bool) -> list[dict]:
    params = {
        "last_id": last_id,
        "batch_size": batch_size,
        "model_name": model_name,
    }
    missing_join = ""
    missing_where = ""
    if not replace_existing:
        missing_join = """
            LEFT JOIN catalog_node_embeddings e
              ON e.catalog_path_index_id = c.id
             AND e.model_name = :model_name
        """
        missing_where = "AND e.id IS NULL"

    with SessionLocal() as db:
        rows = db.execute(text(f"""
            SELECT c.id, c.node_id, c.entity_level, c.app_code_from_path, c.normalized_path,
                   c.leaf_name, c.parent_name, c.path_tokens
            FROM catalog_path_index c
            {missing_join}
            WHERE c.id > :last_id
            {missing_where}
            ORDER BY c.id
            LIMIT :batch_size
        """), params).mappings().all()
    return [dict(row) for row in rows]


def generate_catalog_embeddings(
    limit: int | None = None,
    batch_size: int | None = None,
    replace_existing: bool = False,
    progress: bool = False,
) -> dict:
    model_name = settings.embedding_provider
    batch_size = batch_size or settings.embedding_batch_size
    pending = _count_pending(model_name, replace_existing=replace_existing)
    target = min(pending, limit) if limit else pending
    count = 0
    last_id = 0

    if progress:
        mode = "replacing all rows" if replace_existing else "skipping existing rows"
        print(f"Generating up to {target} embeddings with provider={model_name}, batch_size={batch_size}, {mode}", flush=True)

    while count < target:
        remaining = target - count
        rows = _fetch_batch(
            last_id=last_id,
            batch_size=min(batch_size, remaining),
            model_name=model_name,
            replace_existing=replace_existing,
        )
        if not rows:
            break

        output_rows = []
        for payload in rows:
            last_id = payload["id"]
            embedding_text = build_embedding_text(payload)
            vector = embed_text(embedding_text)
            output_rows.append({
                "catalog_path_index_id": payload["id"],
                "node_id": payload["node_id"],
                "entity_level": payload["entity_level"],
                "embedding_text": embedding_text,
                "embedding_vector": vector,
            })

        save_embeddings_batch(output_rows, model_name=model_name)
        count += len(output_rows)

        if progress:
            print(f"  generated {count}/{target} embeddings (last catalog_path_index.id={last_id})", flush=True)

    return {
        "generated": count,
        "provider": model_name,
        "batch_size": batch_size,
        "replace_existing": replace_existing,
        "pending_before_run": pending,
    }
