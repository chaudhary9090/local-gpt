import os
import sys
import uvicorn
from backend.rag_engine import rag_engine, DOCS_DIR

def initialize_initial_docs():
    """Ensure sample documents are indexed into ChromaDB if empty."""
    stats = rag_engine.get_stats()
    if stats["total_chunks"] == 0:
        print("[Startup] Indexing initial enterprise documents into ChromaDB...")
        if os.path.exists(DOCS_DIR):
            files = [f for f in os.listdir(DOCS_DIR) if os.path.isfile(os.path.join(DOCS_DIR, f))]
            for f in files:
                path = os.path.join(DOCS_DIR, f)
                n = rag_engine.ingest_file(path)
                print(f"  -> Ingested {f}: {n} chunks")
        print("[Startup] Initialization completed.")
    else:
        print(f"[Startup] Found {stats['total_chunks']} existing chunks across {stats['total_documents']} documents in ChromaDB.")

if __name__ == "__main__":
    initialize_initial_docs()
    port = 8145
    host = "127.0.0.1"
    print(f"\n==================================================================")
    print(f"  Local Document GPT & Policy Analysis Engine Running")
    print(f"  Web Interface: http://{host}:{port}")
    print(f"  Local SLM: http://localhost:11434 (Ollama)")
    print(f"==================================================================\n")
    uvicorn.run("backend.main:app", host=host, port=port, reload=False, log_level="info")
