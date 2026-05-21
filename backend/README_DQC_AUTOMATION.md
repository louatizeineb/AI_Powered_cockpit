# DQC automation package

This package adds a dedicated Data Quality Check event pipeline.

## Flow

1. Kafka receives DQC events on `dataquality.results.raw`.
2. `consumer.py` reads the event.
3. `validator.py` checks the JSON schema.
4. `normalizer.py` creates the internal event format.
5. `business_rules.py` checks required fields and `controlled_item_count = ok_count + ko_count`.
6. `resolver.py` tries to resolve the event against the catalog.
7. PostgreSQL stores event store, result, resolution, and DLQ rows.
8. Event KG stores traceability nodes.
9. DLQ logs are written to `logs/dqc/dqc_dlq.log`.
10. Logstash sends DLQ logs to Elasticsearch index `dqc-dlq-logs-*`.

## Run order

```bash
python scripts/create_dqc_tables.py
python scripts/create_dqc_topics.py
python scripts/init_dqc_event_kg.py

docker compose -f infra/docker-compose.observability.yml up -d

python scripts/run_dqc_consumer.py
python scripts/send_sample_dqc_events.py --count 20
python scripts/send_bad_dqc_events.py
```

## FastAPI

Include the router in your `backend/app/main.py`:

```python
from backend.app.dqc.routes import router as dqc_router
app.include_router(dqc_router)
```

Useful endpoints:

- `GET /dqc/recent`
- `GET /dqc/results`
- `GET /dqc/dlq`
- `GET /dqc/resolutions`
- `GET /dqc/summary`
- `GET /dqc/resolution-summary`
- `GET /dqc/dlq-summary`
- `POST /dqc/test/send-valid`
- `POST /dqc/test/send-bad`
```
