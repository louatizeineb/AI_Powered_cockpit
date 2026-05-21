from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from app.routes import router as core_router

try:
    from app.eventing.routes import router as eventing_router
except Exception:
    eventing_router = None

try:
    from app.dqc.routes import router as old_dqc_router
except Exception:
    old_dqc_router = None

from app.catalog.routes import router as catalog_router
from app.dqc.resolution.routes import router as dqc_resolution_router
from app.embeddings.routes import router as embeddings_router
from app.graphrag.routes import router as graphrag_router
from app.agent.routes import router as agent_router
from app.observability.routes import router as observability_router

app = FastAPI(
    title="AI-Powered Data Quality Cockpit Backend",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core_router)

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