import importlib.util
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

try:
    from app.eventing.routes import router as eventing_router
except Exception:
    logger.exception("Optional eventing routes could not be loaded")
    eventing_router = None

try:
    from app.dqc.routes import router as old_dqc_router
except Exception:
    logger.exception("Optional legacy DQC routes could not be loaded")
    old_dqc_router = None

from app.catalog.routes import router as catalog_router
from app.dqc.resolution.routes import router as dqc_resolution_router
from app.embeddings.routes import router as embeddings_router
from app.graphrag.routes import router as graphrag_router
from app.agent.routes import router as agent_router
from app.observability.routes import router as observability_router


def _load_lineage_explorer_router():
    route_path = Path(__file__).resolve().parent / "routes" / "lineage_explorer.py"
    spec = importlib.util.spec_from_file_location("lineage_explorer_routes_runtime", route_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.router


lineage_explorer_router = _load_lineage_explorer_router()

app = FastAPI(
    title="Progressive Lineage + DQC Backend",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:5176",
        "http://127.0.0.1:5176",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if lineage_explorer_router:
    app.include_router(lineage_explorer_router)

if eventing_router:
    app.include_router(eventing_router)

if old_dqc_router:
    app.include_router(old_dqc_router)

app.include_router(catalog_router)
app.include_router(dqc_resolution_router)
app.include_router(embeddings_router)
app.include_router(graphrag_router)
app.include_router(agent_router)
app.include_router(observability_router)


@app.get("/health")
def health():
    return {"status": "ok"}
