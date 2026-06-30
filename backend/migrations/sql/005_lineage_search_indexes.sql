CREATE INDEX IF NOT EXISTS idx_lineage_search_node_id
    ON lineage_search_document(node_id);
CREATE INDEX IF NOT EXISTS idx_lineage_search_level
    ON lineage_search_document(entity_level);
CREATE INDEX IF NOT EXISTS idx_lineage_search_label_prefix
    ON lineage_search_document(normalized_label text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_lineage_search_technical_prefix
    ON lineage_search_document(normalized_technical_name text_pattern_ops);
CREATE INDEX IF NOT EXISTS idx_lineage_search_label_trgm
    ON lineage_search_document USING gin(normalized_label gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_lineage_search_technical_trgm
    ON lineage_search_document USING gin(normalized_technical_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_lineage_search_path_trgm
    ON lineage_search_document USING gin(normalized_path gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_lineage_search_text_trgm
    ON lineage_search_document USING gin(search_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_lineage_search_tsv
    ON lineage_search_document USING gin(search_tsv);
