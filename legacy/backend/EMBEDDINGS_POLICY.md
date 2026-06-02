# Graph Embeddings Policy

Embeddings must not be calculated on every user request.

## When to generate embeddings

Generate embeddings:

1. after a new DataGalaxy/catalog export is loaded
2. after rebuilding `catalog_path_index`
3. on a scheduled nightly job
4. manually from admin endpoint or script

## Where to store them

Store in PostgreSQL table:

```text
catalog_node_embeddings
```

Each row stores:

- catalog_path_index_id
- node_id
- entity_level
- embedding_text
- embedding_vector
- model_name
- generated_at

## How to use them

At request time:

1. Build a small query text from the DQC event.
2. Generate only the query embedding.
3. Load precomputed catalog vectors for the same app_code / target level.
4. Compute cosine similarity.
5. Return top candidates as embedding fallback.

## Why cosine similarity

Cosine similarity compares vector direction and works well for semantic similarity retrieval. In this project it is only a fallback after path exact, token, and fuzzy matching.
