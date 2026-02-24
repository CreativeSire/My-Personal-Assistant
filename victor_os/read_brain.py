import sqlite3
import os

# Connect to the brain
# db_path = "victor_memory.db" # OLD
db_path = "victor_os/memory_store/victor_memory.db" # NEW: Correct relative path from root


if not os.path.exists(db_path):
    print("❌ NO BRAIN FOUND at " + db_path)
else:
    print("🧠 CONNECTING TO BRAIN...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Read the last 10 thoughts
    try:
        # Note: The user's snippet blindly tries SELECT * FROM sessions.
        # It's safer to check tables first, which the script does in the except/fallback block in the user's mind,
        # but let's just stick to the user's provided code structure or slightly improved to be robust.
        # User's code:
        # cursor.execute("SELECT * FROM sessions") 
        # tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        
        # I'll use a slightly more robust version that doesn't crash if 'sessions' doesn't exist immediately
        tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        print(f"📂 Found Memory Clusters: {tables}")
        
        for table in tables:
            t_name = table[0]
            print(f"\n--- READING {t_name} ---")
            try:
                # Limit to last 10 rows to avoid spamming
                rows = cursor.execute(f"SELECT * FROM {t_name} ORDER BY rowid DESC LIMIT 10").fetchall()
                for row in rows:
                    print(row)
                    if "Omega Protocol" in str(row):
                        print("✅ FOUND TARGET MEMORY: 'Omega Protocol'")
            except Exception as e:
                print(f"Error reading table {t_name}: {e}")

    except Exception as e:
        print(f"⚠️ Memory Read Error: {e}")

    conn.close()
    print("\nDone.")
