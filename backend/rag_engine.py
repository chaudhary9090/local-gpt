import os
import json
import uuid
import datetime
import urllib.request
import urllib.error
from typing import List, Dict, Any, Generator, Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from langchain_text_splitters import RecursiveCharacterTextSplitter
import pypdf
import docx

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DOCS_DIR = os.path.join(DATA_DIR, "docs")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")

os.makedirs(DOCS_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)

DEFAULT_LLM_MODEL = "qwen2.5:1.5b"
OLLAMA_BASE_URL = "http://localhost:11434"

class LocalRAGEngine:
    def __init__(self, persist_directory: str = CHROMA_DIR):
        self.persist_directory = persist_directory
        self.chunk_size = 1000
        self.chunk_overlap = 150
        self.llm_model = DEFAULT_LLM_MODEL
        self.client = chromadb.PersistentClient(path=self.persist_directory)
        
        # Use ONNX default embedding function (all-MiniLM-L6-v2) for local fast embeddings
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name="personal_documents",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )

    def set_config(self, llm_model: Optional[str] = None, chunk_size: Optional[int] = None, chunk_overlap: Optional[int] = None):
        if llm_model:
            self.llm_model = llm_model
        if chunk_size and chunk_size > 50:
            self.chunk_size = chunk_size
        if chunk_overlap and chunk_overlap >= 0:
            self.chunk_overlap = chunk_overlap

    def extract_text_from_file(self, file_path: str) -> List[Dict[str, Any]]:
        """Extract text and metadata from PDF, DOCX, TXT, MD, etc."""
        ext = os.path.splitext(file_path)[1].lower()
        filename = os.path.basename(file_path)
        pages_content = []

        if ext == ".pdf":
            reader = pypdf.PdfReader(file_path)
            for page_idx, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages_content.append({
                        "text": text,
                        "page": page_idx + 1,
                        "source": filename
                    })
        elif ext == ".docx":
            doc = docx.Document(file_path)
            full_text = []
            for p in doc.paragraphs:
                if p.text.strip():
                    full_text.append(p.text)
            text_content = "\n\n".join(full_text)
            if text_content.strip():
                pages_content.append({
                    "text": text_content,
                    "page": 1,
                    "source": filename
                })
        else: # txt, md, json, csv, etc.
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    text_content = f.read()
                if text_content.strip():
                    pages_content.append({
                        "text": text_content,
                        "page": 1,
                        "source": filename
                    })
            except Exception as e:
                print(f"Error reading {file_path}: {e}")

        return pages_content

    def ingest_file(self, file_path: str) -> int:
        """Parse, split, and embed file into ChromaDB."""
        filename = os.path.basename(file_path)
        # Delete existing entries for this file first
        self.delete_document(filename, remove_file=False)

        pages = self.extract_text_from_file(file_path)
        if not pages:
            return 0

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", " ", ""]
        )

        all_docs = []
        all_metas = []
        all_ids = []

        chunk_counter = 0
        timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for page_data in pages:
            raw_text = page_data["text"]
            chunks = splitter.split_text(raw_text)
            for idx, chunk in enumerate(chunks):
                if chunk.strip():
                    chunk_counter += 1
                    doc_id = f"{filename}_chunk_{chunk_counter}_{uuid.uuid4().hex[:6]}"
                    all_ids.append(doc_id)
                    all_docs.append(chunk)
                    all_metas.append({
                        "source": filename,
                        "page": int(page_data.get("page", 1)),
                        "chunk_index": chunk_counter,
                        "timestamp": timestamp_str
                    })

        if all_docs:
            # Batch add to ChromaDB (Chroma handles up to 5000 comfortably per call)
            batch_size = 500
            for i in range(0, len(all_docs), batch_size):
                self.collection.add(
                    documents=all_docs[i:i+batch_size],
                    metadatas=all_metas[i:i+batch_size],
                    ids=all_ids[i:i+batch_size]
                )

        return len(all_docs)

    def query_context(self, question: str, k: int = 4) -> List[Dict[str, Any]]:
        """Retrieve most relevant chunks for a question."""
        count = self.collection.count()
        if count == 0:
            return []

        actual_k = min(k, count)
        results = self.collection.query(
            query_texts=[question],
            n_results=actual_k,
            include=["documents", "metadatas", "distances"]
        )

        retrieved = []
        if results and "documents" in results and results["documents"]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if "metadatas" in results else [{}] * len(docs)
            distances = results["distances"][0] if "distances" in results else [0.0] * len(docs)

            for d, m, dist in zip(docs, metas, distances):
                retrieved.append({
                    "content": d,
                    "source": m.get("source", "Unknown"),
                    "page": m.get("page", 1),
                    "chunk_index": m.get("chunk_index", 1),
                    "distance": round(float(dist), 4),
                    "relevance": round(1.0 - float(dist), 4)
                })

        return retrieved

    def generate_answer(self, question: str, k: int = 4, stream: bool = False):
        """Perform RAG Q&A with strict anti-hallucination prompt as in Aman Kharwal's guide."""
        retrieved_chunks = self.query_context(question, k=k)

        if not retrieved_chunks:
            fallback = "I couldn't find that information in the document because no documents are indexed yet. Please upload your documents first."
            if stream:
                def _fallback_gen():
                    yield {"type": "meta", "sources": []}
                    yield {"type": "token", "text": fallback}
                    yield {"type": "done"}
                return _fallback_gen()
            return {"answer": fallback, "sources": []}

        context_str = "\n\n---\n\n".join([
            f"[Source: {chunk['source']} | Page: {chunk['page']}]\n{chunk['content']}"
            for chunk in retrieved_chunks
        ])

        system_instruction = (
            "You are a helpful AI assistant for personal documents.\n"
            "- If the user provides a greeting (e.g. 'hello', 'hi'), greet them warmly and let them know you are ready to answer questions about their uploaded documents.\n"
            "- Answer questions accurately and thoroughly using ONLY the provided Context.\n"
            "- If the question asks for a summary, overview, or key takeaways, provide a clear, well-structured summary based on the context.\n"
            "- If the answer to a factual question is NOT present in the Context, respond with: 'I couldn't find that information in the document.'\n"
        )

        prompt = (
            f"{system_instruction}\n\n"
            f"Context:\n{context_str}\n\n"
            f"User Question:\n{question}\n\n"
            f"Assistant Answer:\n"
        )

        sources = [{
            "source": c["source"],
            "page": c["page"],
            "chunk_index": c["chunk_index"],
            "relevance": c["relevance"],
            "snippet": c["content"][:200] + ("..." if len(c["content"]) > 200 else "")
        } for c in retrieved_chunks]

        if stream:
            return self._stream_ollama(prompt, sources)
        else:
            ans = self._call_ollama(prompt)
            return {"answer": ans, "sources": sources}

    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama generate endpoint."""
        url = f"{OLLAMA_BASE_URL}/api/generate"
        payload = json.dumps({
            "model": self.llm_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1, # Keep low to prevent hallucination
                "top_p": 0.9
            }
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("response", "").strip()
        except Exception as e:
            return f"Error contacting local SLM/LLM ({self.llm_model}): {str(e)}. Please check if Ollama is running."

    def _stream_ollama(self, prompt: str, sources: List[Dict[str, Any]]) -> Generator[Dict[str, Any], None, None]:
        """Stream Ollama tokens to caller."""
        yield {"type": "meta", "sources": sources}
        url = f"{OLLAMA_BASE_URL}/api/generate"
        payload = json.dumps({
            "model": self.llm_model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9
            }
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                for line in resp:
                    if line:
                        chunk_data = json.loads(line.decode("utf-8"))
                        token = chunk_data.get("response", "")
                        if token:
                            yield {"type": "token", "text": token}
                        if chunk_data.get("done", False):
                            break
        except Exception as e:
            yield {"type": "token", "text": f"\n\n[Error from Ollama]: {str(e)}"}
        yield {"type": "done"}

    def list_documents(self) -> List[Dict[str, Any]]:
        """List distinct documents with metadata from Chroma collection."""
        count = self.collection.count()
        if count == 0:
            return []

        all_data = self.collection.get(include=["metadatas"])
        docs_map = {}
        for meta in all_data.get("metadatas", []):
            if not meta:
                continue
            src = meta.get("source", "Unknown")
            if src not in docs_map:
                file_path = os.path.join(DOCS_DIR, src)
                file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                docs_map[src] = {
                    "filename": src,
                    "chunks": 0,
                    "pages": set(),
                    "size": file_size,
                    "timestamp": meta.get("timestamp", "N/A")
                }
            docs_map[src]["chunks"] += 1
            docs_map[src]["pages"].add(meta.get("page", 1))

        result = []
        for src, item in docs_map.items():
            result.append({
                "filename": item["filename"],
                "chunks": item["chunks"],
                "pages": len(item["pages"]),
                "size_kb": round(item["size"] / 1024, 2),
                "timestamp": item["timestamp"]
            })
        result.sort(key=lambda x: x["timestamp"], reverse=True)
        return result

    def delete_document(self, filename: str, remove_file: bool = True):
        """Remove a document's chunks from ChromaDB and optionally delete file from disk."""
        try:
            self.collection.delete(where={"source": filename})
        except Exception as e:
            print(f"Error removing {filename} from Chroma: {e}")

        if remove_file:
            file_path = os.path.join(DOCS_DIR, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    print(f"Error removing file {file_path}: {e}")

    def reindex_all(self) -> Dict[str, Any]:
        """Re-scan docs directory and re-chunk everything."""
        # Clear collection
        try:
            self.client.delete_collection("personal_documents")
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name="personal_documents",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )

        files = [f for f in os.listdir(DOCS_DIR) if os.path.isfile(os.path.join(DOCS_DIR, f))]
        total_chunks = 0
        for f in files:
            path = os.path.join(DOCS_DIR, f)
            total_chunks += self.ingest_file(path)

        return {
            "files_processed": len(files),
            "total_chunks": total_chunks
        }

    def get_stats(self) -> Dict[str, Any]:
        total_chunks = self.collection.count()
        docs = self.list_documents()
        return {
            "total_documents": len(docs),
            "total_chunks": total_chunks,
            "llm_model": self.llm_model,
            "embedding_model": "all-MiniLM-L6-v2 (ONNX local)",
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap
        }

# Global singleton
rag_engine = LocalRAGEngine()
