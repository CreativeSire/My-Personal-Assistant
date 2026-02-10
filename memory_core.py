import os
import sqlite3
import json
import datetime
import numpy as np
from google.genai import Client, types
from dotenv import load_dotenv

# Load API Key
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = Client(api_key=api_key)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "memory_store", "victor_vault.db")
VECTORS_DIR = os.path.join(BASE_DIR, "memory_store", "victor_brain_vectors")

# Ensure directories exist
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(VECTORS_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT,
            embedding BLOB,
            metadata TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_embedding(text):
    """Uses Gemini to get vector embeddings."""
    try:
        response = client.models.embed_content(
            model="models/gemini-embedding-001",
            contents=text
        )
        return response.embeddings[0].values
    except Exception as e:
        print(f"Embedding Error: {e}")
        return None

def save_long_term_memory(text: str, tags: str = "general"):
    """Saves to SQLite with Gemini Embeddings."""
    embedding = get_embedding(text)
    if embedding is None:
        return "❌ Failed to generate embedding."
    
    timestamp = datetime.datetime.now().isoformat()
    clean_tag = tags.replace(" ", "_").lower()
    doc_id = f"{clean_tag}_{int(datetime.datetime.now().timestamp())}"
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO memories (id, content, embedding, metadata, timestamp) VALUES (?, ?, ?, ?, ?)",
        (doc_id, text, np.array(embedding).tobytes(), json.dumps({"type": tags}), timestamp)
    )
    conn.commit()
    conn.close()
    
    # Touch the directory the user is looking for to signal success
    with open(os.path.join(VECTORS_DIR, "vault_status.txt"), "w") as f:
        f.write(f"Vault initialized at {timestamp}")
        
    return f"✅ Memory Committed to Vault: '{text[:50]}...'"

def recall_long_term_memory(query: str, n_results: int = 5):
    """Performs cosine similarity search manually using SQLite + NumPy."""
    query_embedding = get_embedding(query)
    if query_embedding is None:
        return "Memory Recall Error: Could not generate query embedding."
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, content, embedding FROM memories")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return "No relevant long-term memories found."
    
    results = []
    q_vec = np.array(query_embedding)
    
    for row_id, content, emb_blob in rows:
        m_vec = np.frombuffer(emb_blob, dtype=np.float32)
        # Handle potential dtype mismatches if necessary, but text-embedding-004 is float32
        if len(m_vec) != len(q_vec): continue 
        
        # Cosine Similarity
        similarity = np.dot(q_vec, m_vec) / (np.linalg.norm(q_vec) * np.linalg.norm(m_vec))
        results.append((similarity, content))
    
    # Sort by similarity DESC
    results.sort(key=lambda x: x[0], reverse=True)
    top_memories = results[:n_results]
    
    formatted_memory = "\n".join([f"- {m[1]}" for m in top_memories if m[0] > 0.4]) # threshold
    if not formatted_memory:
        return "No relevant long-term memories found."
        
    return f"🔍 VAULT SEARCH RESULTS:\n{formatted_memory}"

if __name__ == "__main__":
    print(save_long_term_memory("Victor OS Initialized with SQLite Vector Vault.", "system"))
    print(recall_long_term_memory("How is memory stored?"))
