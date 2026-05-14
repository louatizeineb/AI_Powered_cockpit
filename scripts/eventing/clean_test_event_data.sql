-- Removes only test event-pipeline data from Postgres event tables.
-- It does NOT touch source/container/structure/field/link/usage.

DELETE FROM event_catalog_resolution WHERE environment = 'test';
DELETE FROM data_quality_check_result WHERE environment = 'test';
DELETE FROM pipeline_run WHERE environment = 'test';
DELETE FROM event_dlq WHERE environment = 'test';
DELETE FROM event_store WHERE environment = 'test';
