"""
The Forge
Autonomous Self-Healing System for Victor OS.
"""

import subprocess
import os
from pathlib import Path
from rich.console import Console
from google import genai
from google.genai import types
from victor_os.config import get_config

console = Console()
cfg = get_config()

def run_forge_repair():
    console.print("[bold red]Forge: Running System Diagnostics...[/bold red]")
    
    # 1. Run Tests
    result = subprocess.run(
        ["pytest", "victor_os/"], 
        capture_output=True, 
        text=True
    )
    
    if result.returncode == 0:
        console.print("[green]All Systems Operational. No repairs needed.[/green]")
        return

    console.print("[yellow]Failures Detected. Initiating Repair Protocol...[/yellow]")
    
    # 2. Extract Error
    error_log = result.stdout + result.stderr
    # Simple heuristic to find the failing file (would be more robust in prod)
    # Looking for "FAILED victor_os/test_..."
    
    console.print(f"[dim]Error Log Sample:\n{error_log[-500:]}[/dim]")

    # 3. Consult Gemini
    client = genai.Client(api_key=cfg.gemini_api_key)
    
    prompt = f"""
    You are the Victor OS Repair Agent. The test suite failed.
    
    ERROR LOG:
    {error_log}
    
    Please identify the file causing the error and provide the COMPLETE corrected python code for that file.
    Only return the code, no markdown, no explanation.
    Start the file with a comment: # Patched by The Forge
    """
    
    try:
        response = client.models.generate_content(
            model=cfg.model_name,
            contents=prompt
        )
        
        # 4. Apply Patch (Simulated for safety)
        patch_code = response.text.strip()
        if patch_code.startswith("```python"):
            patch_code = patch_code.split("```python")[1].split("```")[0]
            
        console.print("[cyan]Generated Patch. Saving to 'forge_patch.py' for review...[/cyan]")
        
        with open("forge_patch.py", "w") as f:
            f.write(patch_code)
            
        console.print("[bold green]Patch Ready. Run 'python forge_patch.py' to apply manual fix.[/bold green]")
        
    except Exception as e:
        console.print(f"[red]Forge Repair Failed: {e}[/red]")

if __name__ == "__main__":
    run_forge_repair()
