-- Apply only after moving the search database to a PostgreSQL image that provides pgvector.
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE catalog_node_embeddings
    ADD COLUMN IF NOT EXISTS embedding_vector_ann vector(1536);

UPDATE catalog_node_embeddings
SET embedding_vector_ann = embedding_vector::text::vector
WHERE embedding_vector_ann IS NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_embeddings_ann_hnsw
    ON catalog_node_embeddings
    USING hnsw (embedding_vector_ann vector_cosine_ops);
