CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['source', 'container', 'structure', 'field'] LOOP
        IF to_regclass('public.' || tbl) IS NOT NULL THEN
            EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%I_node_id ON %I (node_id)', tbl, tbl);

            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = tbl
                  AND column_name = 'name_label'
            ) THEN
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS idx_%I_name_label_trgm ON %I USING GIN (LOWER(name_label) gin_trgm_ops)',
                    tbl,
                    tbl
                );
            END IF;

            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = tbl
                  AND column_name = 'name_tech'
            ) THEN
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS idx_%I_name_tech_trgm ON %I USING GIN (LOWER(name_tech) gin_trgm_ops)',
                    tbl,
                    tbl
                );
            END IF;

            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = tbl
                  AND column_name = 'path_full'
            ) THEN
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS idx_%I_path_full_trgm ON %I USING GIN (LOWER(path_full) gin_trgm_ops)',
                    tbl,
                    tbl
                );
            END IF;
        END IF;
    END LOOP;

    IF to_regclass('public.usage') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_usage_uuid ON usage (usage_uuid);

        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'usage'
              AND column_name = 'usage_name'
        ) THEN
            CREATE INDEX IF NOT EXISTS idx_usage_name_trgm ON usage USING GIN (LOWER(usage_name) gin_trgm_ops);
        END IF;

        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'usage'
              AND column_name = 'usage_tech_name'
        ) THEN
            CREATE INDEX IF NOT EXISTS idx_usage_tech_name_trgm ON usage USING GIN (LOWER(usage_tech_name) gin_trgm_ops);
        END IF;

        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'usage'
              AND column_name = 'usage_path'
        ) THEN
            CREATE INDEX IF NOT EXISTS idx_usage_path_trgm ON usage USING GIN (LOWER(usage_path) gin_trgm_ops);
        END IF;
    END IF;
END $$;
