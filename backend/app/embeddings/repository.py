from __future__ import annotations
import json
from sqlalchemy import text
from app.db import SessionLocal


def save_embedding(catalog_path_index_id: int, node_id: str, entity_level: str, embedding_text: str, vector: list[float], model_name: str) -> None:
    with SessionLocal() as db:
        db.execute(text("""
            INSERT INTO catalog_node_embeddings(catalog_path_index_id, node_id, entity_level, embedding_text, embedding_vector, model_name)
            VALUES (:catalog_path_index_id, :node_id, :entity_level, :embedding_text, :embedding_vector, :model_name)
            ON CONFLICT (catalog_path_index_id, model_name)
            DO UPDATE SET embedding_text = EXCLUDED.embedding_text,
                          embedding_vector = EXCLUDED.embedding_vector,
                          generated_at = now()
        """), {
            "catalog_path_index_id": catalog_path_index_id,
            "node_id": node_id,
            "entity_level": entity_level,
            "embedding_text": embedding_text,
            "embedding_vector": vector,
            "model_name": model_name,
        })
        db.commit()


def save_embeddings_batch(rows: list[dict], model_name: str) -> None:
    if not rows:
        return

    with SessionLocal() as db:
        db.execute(text("""
            INSERT INTO catalog_node_embeddings(catalog_path_index_id, node_id, entity_level, embedding_text, embedding_vector, model_name)
            VALUES (:catalog_path_index_id, :node_id, :entity_level, :embedding_text, :embedding_vector, :model_name)
            ON CONFLICT (catalog_path_index_id, model_name)
            DO UPDATE SET embedding_text = EXCLUDED.embedding_text,
                          embedding_vector = EXCLUDED.embedding_vector,
                          generated_at = now()
        """), [
            {
                "catalog_path_index_id": row["catalog_path_index_id"],
                "node_id": row["node_id"],
                "entity_level": row["entity_level"],
                "embedding_text": row["embedding_text"],
                "embedding_vector": row["embedding_vector"],
                "model_name": model_name,
            }
            for row in rows
        ])
        db.commit()


def list_embeddings(app_code: str | None = None, target_level: str | None = None, limit: int = 5000) -> list[dict]:
    where = []
    params = {"limit": limit}
    if app_code:
        where.append("c.app_code_from_path = :app_code")
        params["app_code"] = app_code.upper()
    if target_level:
        where.append("e.entity_level = :target_level")
        params["target_level"] = target_level
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    with SessionLocal() as db:
        rows = db.execute(text(f"""
            SELECT e.catalog_path_index_id, e.node_id, e.entity_level, e.embedding_text,
                   e.embedding_vector, c.raw_path_full, c.normalized_path, c.app_code_from_path,
                   c.leaf_name, c.parent_name, c.path_tokens
            FROM catalog_node_embeddings e
            JOIN catalog_path_index c ON c.id = e.catalog_path_index_id
            {where_sql}
            LIMIT :limit
        """), params).mappings().all()
    return [dict(r) for r in rows]


def list_nearest_embeddings(
    vector: list[float],
    app_code: str | None = None,
    target_level: str | None = None,
    limit: int = 50,
) -> list[dict] | None:
    where = ["e.embedding_vector_ann IS NOT NULL"]
    params = {
        "vector": json.dumps(vector),
        "limit": limit,
    }
    if app_code:
        where.append("c.app_code_from_path = :app_code")
        params["app_code"] = app_code.upper()
    if target_level:
        where.append("e.entity_level = :target_level")
        params["target_level"] = target_level
    where_sql = " AND ".join(where)

    with SessionLocal() as db:
        ready = db.execute(text("""
            SELECT to_regtype('vector') IS NOT NULL
               AND EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'catalog_node_embeddings'
                      AND column_name = 'embedding_vector_ann'
               )
        """)).scalar()
        if not ready:
            return None
        rows = db.execute(text(f"""
            SELECT e.catalog_path_index_id, e.node_id, e.entity_level, e.embedding_text,
                   e.embedding_vector, c.raw_path_full, c.normalized_path, c.app_code_from_path,
                   c.leaf_name, c.parent_name, c.path_tokens,
                   1 - (e.embedding_vector_ann <=> CAST(:vector AS vector)) AS embedding_similarity
            FROM catalog_node_embeddings e
            JOIN catalog_path_index c ON c.id = e.catalog_path_index_id
            WHERE {where_sql}
            ORDER BY e.embedding_vector_ann <=> CAST(:vector AS vector)
            LIMIT :limit
        """), params).mappings().all()
    return [dict(row) for row in rows]
