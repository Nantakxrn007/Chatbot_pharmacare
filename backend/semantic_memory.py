"""
Semantic Memory — จัดการความจำระยะยาวของ Chatbot ผ่าน Qdrant (Vector DB)
ใช้ Qdrant client ตัวเดียวกับ rag_engine (singleton) เพื่อหลีกเลี่ยง lock conflict
"""
import uuid
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

from backend.rag_engine import embed_query, get_qdrant_client

COLLECTION_NAME = "chat_memory"

class SemanticMemory:
    def __init__(self):
        self._collection_ready = False

    def _ensure_collection(self):
        if self._collection_ready:
            return
        client = get_qdrant_client()
        collections = client.get_collections().collections
        exists = False
        for c in collections:
            if c.name == COLLECTION_NAME:
                exists = True
                break
        
        # We need to ensure the collection has size=3072
        if exists:
            # Let's drop it and recreate to avoid dimension mismatch since we changed from 768 to 3072
            # or if it fails, we ignore. For now we assume if it exists we just use it, 
            # but if it has wrong dim, it will crash. Let's just drop and recreate if needed.
            # Actually, the user just got the crash, so we should drop it once or try-catch the insert.
            pass
        
        if not exists:
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=3072, distance=Distance.COSINE)
            )
        self._collection_ready = True

    def add_to_memory(self, session_id: str, role: str, content: str, timestamp: str):
        if not content or not content.strip():
            return
            
        self._ensure_collection()

        try:
            vector = embed_query(content)

            point_id = str(uuid.uuid4())
            
            get_qdrant_client().upsert(
                collection_name=COLLECTION_NAME,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "session_id": session_id,
                            "role": role,
                            "content": content,
                            "timestamp": timestamp
                        }
                    )
                ]
            )
        except Exception as e:
            print(f"[SemanticMemory] Error adding to memory: {e}")
            if "broadcast" in str(e).lower() or "shape" in str(e).lower():
                print("[SemanticMemory] Dimension mismatch detected. Recreating collection...")
                get_qdrant_client().delete_collection(collection_name=COLLECTION_NAME)
                self._collection_ready = False
                self.add_to_memory(session_id, role, content, timestamp)

    def search_memory(self, session_id: str, query: str, top_k: int = 5) -> list[dict]:
        if not query or not query.strip():
            return []
            
        self._ensure_collection()

        try:
            vector = embed_query(query)

            search_result = get_qdrant_client().query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                query_filter=Filter(
                    must=[
                        FieldCondition(
                            key="session_id",
                            match=MatchValue(value=session_id)
                        )
                    ]
                ),
                limit=top_k
            ).points
            
            results = []
            for hit in search_result:
                results.append({
                    "role": hit.payload.get("role", ""),
                    "content": hit.payload.get("content", ""),
                    "timestamp": hit.payload.get("timestamp", ""),
                    "similarity": hit.score
                })
                
            results.sort(key=lambda x: x["timestamp"])
            return results
        except Exception as e:
            print(f"[SemanticMemory] Error searching memory: {e}")
            return []

semantic_memory = SemanticMemory()
