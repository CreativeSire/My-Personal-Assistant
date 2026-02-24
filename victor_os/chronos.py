"""
Project Chronos
Semantic Visual Search Engine for Victor OS.
"""

import os
import time
import json
import base64
from pathlib import Path
from rich.console import Console
from google import genai
from google.genai import types
from victor_os.config import get_config

console = Console()
cfg = get_config()

DB_PATH = Path("memory_store/chronos_db.json")

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def index_vision_log():
    """
    Scans the vision_log folder for unindexed images and generates descriptions using Gemini Flash.
    """
    vision_dir = Path("memory_store/vision_log")
    if not vision_dir.exists():
        console.print("[yellow]No vision log found. Run 'manage.py watch' first.[/yellow]")
        return

    # Load existing index
    if DB_PATH.exists():
        with open(DB_PATH, "r") as f:
            index = json.load(f)
    else:
        index = {}

    client = genai.Client(api_key=cfg.gemini_api_key)
    
    # Find new images
    all_images = list(vision_dir.glob("*.png"))
    new_images = [img for img in all_images if img.name not in index]

    if not new_images:
        console.print("[dim]Chronos: No new images to index.[/dim]")
        return

    console.print(f"[bold cyan]Chronos: Indexing {len(new_images)} new screenshots...[/bold cyan]")

    for img_path in new_images:
        try:
            # Prepare image for Gemini
            with open(img_path, "rb") as f:
                img_data = f.read()
            
            prompt = "Describe this screen in detail. What application is open? What text is visible? What is the user doing?"
            
            response = client.models.generate_content(
                model="gemini-2.0-flash-001",
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(text=prompt),
                            types.Part(
                                inline_data=types.Blob(
                                    mime_type="image/png",
                                    data=img_data
                                )
                            )
                        ]
                    )
                ]
            )
            
            description = response.text.strip()
            
            # Save to index
            index[img_path.name] = {
                "path": str(img_path),
                "timestamp": time.ctime(os.path.getctime(img_path)),
                "description": description
            }
            
            console.print(f"[green]Indexed: {img_path.name}[/green]")
            
            # Rate limit handling (simple)
            time.sleep(1) 

        except Exception as e:
            console.print(f"[red]Failed to index {img_path.name}: {e}[/red]")

    # Save updated index
    with open(DB_PATH, "w") as f:
        json.dump(index, f, indent=2)

def search_chronos(query: str):
    """
    Semantic search over the indexed visual memory.
    """
    if not DB_PATH.exists():
        console.print("[red]Chronos index empty.[/red]")
        return

    with open(DB_PATH, "r") as f:
        index = json.load(f)

    console.print(f"[bold]Searching Chronos for: '{query}'...[/bold]")
    
    hits = []
    for filename, entry in index.items():
        # Simple keyword search for now (can be upgraded to vector search later)
        if query.lower() in entry["description"].lower():
            hits.append(entry)

    if not hits:
        console.print("[yellow]No matches found.[/yellow]")
        return

    # Sort by recent first (simple approximation via filename/timestamp if parsed, here just raw list)
    for hit in hits[-5:]: # Show last 5
        console.print(f"
[cyan]Found in {hit['path']}[/cyan] ({hit['timestamp']})")
        console.print(f"[dim]{hit['description'][:200]}...[/dim]")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        search_chronos(sys.argv[2])
    else:
        index_vision_log()
