from __future__ import annotations

from pathlib import Path
import argparse
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.config import get_settings
from app.embeddings.service import generate_catalog_embeddings


parser = argparse.ArgumentParser()
parser.add_argument(
    "--limit",
    type=int,
    default=None,
    help="Maximum number of embeddings to generate this run",
)
parser.add_argument(
    "--batch-size",
    type=int,
    default=None,
    help="Rows to fetch and commit per batch",
)
parser.add_argument(
    "--replace-existing",
    action="store_true",
    help="Regenerate embeddings that already exist",
)

args = parser.parse_args()
settings = get_settings()

print("=" * 70)
print("CATALOG EMBEDDING GENERATION")
print("=" * 70)
print(f"Embedding provider chosen : {settings.embedding_provider}")

if settings.embedding_provider == "azure_openai":
    print(f"Azure OpenAI endpoint     : {settings.azure_openai_endpoint}")
    print(f"Azure embedding deployment: {settings.azure_openai_embedding_deployment}")
else:
    print("Embedding mode            : local hash embedding")

print(f"Embedding dimension       : {settings.embedding_dim}")
print(f"Limit                     : {args.limit}")
print(f"Batch size                : {args.batch_size}")
print(f"Replace existing          : {args.replace_existing}")
print("=" * 70)

try:
    result = generate_catalog_embeddings(
        limit=args.limit,
        batch_size=args.batch_size,
        replace_existing=args.replace_existing,
        progress=True,
    )
except RuntimeError as exc:
    raise SystemExit(f"Embedding generation failed: {exc}") from exc

print(result)
