from app.db import check_neo4j, check_postgres
from app.marquez_client import MarquezClient
from app.openlineage_mapper import map_links_to_openlineage_events
from app.repositories import LinkRepository, Neo4jRepository, SearchRepository


class HealthService:
    def __init__(self) -> None:
        self.marquez = MarquezClient()

    async def full_health(self) -> dict:
        return {
            "postgres": "ok" if check_postgres() else "error",
            "neo4j": "ok" if check_neo4j() else "error",
            "marquez": "ok" if await self.marquez.health() else "error",
        }


class SearchService:
    def __init__(self) -> None:
        self.repo = SearchRepository()

    def search(self, q: str, limit: int = 20) -> dict:
        results = self.repo.search_assets(q=q, limit=limit)

        return {
            "query": q,
            "count": len(results),
            "results": results,
        }


class BusinessLineageService:
    def __init__(self) -> None:
        self.repo = Neo4jRepository()

    def get_subgraph(self, node_id: str, depth: int = 2) -> dict:
        graph = self.repo.get_business_subgraph(node_id=node_id, depth=depth)

        return {
            "root": node_id,
            "nodes": graph["nodes"],
            "edges": graph["edges"],
            "stats": {
                "node_count": len(graph["nodes"]),
                "edge_count": len(graph["edges"]),
            },
        }


class OpenLineageBootstrapService:
    def __init__(self) -> None:
        self.link_repo = LinkRepository()
        self.marquez = MarquezClient()

    def sample_links(self, limit: int = 20) -> dict:
        rows = self.link_repo.fetch_sample_links(limit=limit)

        return {
            "count": len(rows),
            "items": rows,
        }

    async def bootstrap(
        self,
        limit: int | None = None,
        dry_run: bool = True,
        sample_size: int = 3,
    ) -> dict:
        rows = self.link_repo.fetch_lineage_links(limit=limit)
        events, stats = map_links_to_openlineage_events(rows)

        sent = 0
        failed = 0

        if not dry_run:
            for event in events:
                result = await self.marquez.emit_openlineage_event(event)

                if result["success"]:
                    sent += 1
                else:
                    failed += 1

        return {
            "dry_run": dry_run,
            "links_read": stats["links_read"],
            "jobs_detected": stats["jobs_detected"],
            "events_generated": stats["events_generated"],
            "events_sent": sent,
            "events_failed": failed,
            "skipped_jobs_without_inputs_or_outputs": stats[
                "skipped_jobs_without_inputs_or_outputs"
            ],
            "sample_events": events[:sample_size] if dry_run else [],
        }