import sqlite3
import pandas as pd
import os

# FIXED PATH: User provided "victor_memory.db" but we know it's in a subdir
db_path = "victor_os/memory_store/victor_memory.db"

if not os.path.exists(db_path):
    print(f"NO DATABASE FOUND at {db_path}")
else:
    print(f"Scanning Brain at: {db_path}...\n")
    conn = sqlite3.connect(db_path)
    try:
        # Get all table names
        tables = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table';", conn)
        print("Found Tables:", tables.values.flatten())
        
        # Dump all tables
        for table in tables.values.flatten():
            print(f"\n--- CONTENTS OF TABLE: {table} ---")
            try:
                df = pd.read_sql(f"SELECT * FROM {table}", conn)
                print(df.tail(10)) # Show last 10 rows
                
                # Check for our secret
                # Convert whole dataframe to string to search
                if "Omega Protocol" in str(df.values):
                    print(f"\nFOUND 'Omega Protocol' IN TABLE '{table}'! (He is lying!)")
                else:
                    print(f"\n'Omega Protocol' NOT found in table '{table}'.")
            except Exception as e:
                print(f"Error reading table {table}: {e}")
                
    except Exception as e:
        print(f"Error reading brain: {e}")
    finally:
        conn.close()

# Removed input() to avoid hanging automation
# input("\nPress Enter to exit...")
print("\nScan complete.")
