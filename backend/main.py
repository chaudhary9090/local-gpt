import os
import shutil
import json
import urllib.request
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .rag_engine import rag_engine, DOCS_DIR, OLLAMA_BASE_URL

app = FastAPI(title="Local Document GPT")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# Mount static assets
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Frontend not found</h1>")

def check_ollama_status():
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["name"] for m in data.get("models", [])]
            return True, models
    except Exception:
        return False, []

@app.get("/api/status")
async def get_system_status():
    is_live, models = check_ollama_status()
    stats = rag_engine.get_stats()
    return {
        "slm_live": is_live,
        "slm_status": "LIVE" if is_live else "OFFLINE",
        "available_models": models,
        "active_model": rag_engine.llm_model,
        "total_documents": stats["total_documents"],
        "total_chunks": stats["total_chunks"],
        "embedding_model": stats["embedding_model"],
        "chunk_size": stats["chunk_size"],
        "chunk_overlap": stats["chunk_overlap"]
    }

@app.get("/api/documents")
async def list_documents():
    docs = rag_engine.list_documents()
    return {"documents": docs, "count": len(docs)}

@app.delete("/api/documents/{filename}")
async def delete_document(filename: str):
    rag_engine.delete_document(filename, remove_file=True)
    return {"status": "success", "message": f"Document {filename} deleted"}

@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    results = []
    total_new_chunks = 0
    for file in files:
        safe_filename = os.path.basename(file.filename)
        dest_path = os.path.join(DOCS_DIR, safe_filename)
        
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        chunks_count = rag_engine.ingest_file(dest_path)
        total_new_chunks += chunks_count
        results.append({
            "filename": safe_filename,
            "chunks": chunks_count,
            "status": "indexed" if chunks_count > 0 else "empty_or_failed"
        })

    return {
        "status": "success",
        "total_files": len(files),
        "total_new_chunks": total_new_chunks,
        "files": results
    }

class ChatRequest(BaseModel):
    question: str
    stream: bool = True
    k: int = 4

@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest):
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    if payload.stream:
        generator = rag_engine.generate_answer(payload.question, k=payload.k, stream=True)
        def event_stream():
            for item in generator:
                yield f"data: {json.dumps(item)}\n\n"
        return StreamingResponse(event_stream(), media_type="text/event-stream")
    else:
        res = rag_engine.generate_answer(payload.question, k=payload.k, stream=False)
        return JSONResponse(content=res)

class SettingsRequest(BaseModel):
    llm_model: Optional[str] = None
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None

@app.post("/api/settings")
async def update_settings(payload: SettingsRequest):
    rag_engine.set_config(
        llm_model=payload.llm_model,
        chunk_size=payload.chunk_size,
        chunk_overlap=payload.chunk_overlap
    )
    return {"status": "success", "config": rag_engine.get_stats()}

@app.post("/api/retrain")
async def retrain_vector_db():
    res = rag_engine.reindex_all()
    return {"status": "success", "details": res}
