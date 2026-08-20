"""
One-off migration: local embedded Qdrant (rag/qdrant_db) -> Qdrant Cloud.
Run once from a machine that still has the local qdrant_db populated:

    python -m backend.migrate_to_qdrant_cloud
"""

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from backend.config import QDRANT_DIR, QDRANT_URL, QDRANT_API_KEY, COLLECTION_NAME
from backend.semantic_memory import COLLECTION_NAME as MEMORY_COLLECTION_NAME

BATCH_SIZE = 200

# Both collections that live in the local embedded Qdrant store need migrating:
# COLLECTION_NAME        -> RAG guideline documents (backend/config.py)
# MEMORY_COLLECTION_NAME -> long-term chat memory (backend/semantic_memory.py)
COLLECTIONS_TO_MIGRATE = [COLLECTION_NAME, MEMORY_COLLECTION_NAME]


def migrate_collection(local: QdrantClient, cloud: QdrantClient, name: str):
    if not local.collection_exists(name):
        print(f"[migrate] skip '{name}' — not found in local store")
        return

    info = local.get_collection(name)
    if not cloud.collection_exists(name):
        cloud.create_collection(
            collection_name=name,
            vectors_config=info.config.params.vectors,
        )
        print(f"[migrate] created collection '{name}' on cloud")
    else:
        print(f"[migrate] collection '{name}' already exists on cloud, upserting into it")

    offset = None
    total = 0
    while True:
        points, offset = local.scroll(
            name,
            limit=BATCH_SIZE,
            offset=offset,
            with_vectors=True,
            with_payload=True,
        )
        if not points:
            break
        # scroll() returns Record objects; upsert() needs PointStruct
        upsert_points = [
            PointStruct(id=p.id, vector=p.vector, payload=p.payload)
            for p in points
        ]
        cloud.upsert(collection_name=name, points=upsert_points)
        total += len(points)
        print(f"[migrate] '{name}': migrated {total} points")
        if offset is None:
            break

    cloud_count = cloud.count(collection_name=name).count
    print(f"[migrate] '{name}' done — cloud collection now has {cloud_count} points")


# field -> collection that needs a keyword index for filtering
# (local embedded mode creates these automatically; Qdrant Cloud does not,
# and migrate_collection() only copies points, not index definitions)
PAYLOAD_INDEXES = {
    COLLECTION_NAME: ["patient_group", "source"],
    MEMORY_COLLECTION_NAME: ["session_id"],
}


def ensure_payload_indexes(cloud: QdrantClient):
    from qdrant_client.models import PayloadSchemaType

    for collection, fields in PAYLOAD_INDEXES.items():
        for field in fields:
            cloud.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
            print(f"[migrate] index ready: '{collection}'.{field} (keyword)")


def migrate():
    if not QDRANT_URL or not QDRANT_API_KEY:
        raise SystemExit("ไม่พบ QDRANT_URL / QDRANT_API_KEY ใน .env")

    local = QdrantClient(path=str(QDRANT_DIR))
    cloud = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

    for name in COLLECTIONS_TO_MIGRATE:
        migrate_collection(local, cloud, name)

    ensure_payload_indexes(cloud)


if __name__ == "__main__":
    migrate()
