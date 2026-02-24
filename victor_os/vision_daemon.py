"""
Victor OS - Ghost Vision Daemon
Background service that periodically captures screen context to build a visual memory.
"""

import time
import os
import datetime
from pathlib import Path
from rich.console import Console

console = Console()

def start_watcher(interval: int = 300):
    """
    Captures screenshots every `interval` seconds.
    """
    import pyautogui  # Requires: pip install pyautogui
    
    save_dir = Path("memory_store/vision_log")
    save_dir.mkdir(parents=True, exist_ok=True)
    
    console.print(f"[green]Ghost Vision Active.[/green] Watching screen every {interval}s.")
    console.print(f"Saving to: {save_dir.absolute()}")
    
    try:
        while True:
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = save_dir / f"screen_{ts}.png"
            
            try:
                screenshot = pyautogui.screenshot()
                screenshot.save(filename)
                console.print(f"[dim]Captured: {filename.name}[/dim]")
            except Exception as e:
                console.print(f"[red]Capture Failed: {e}[/red]")
            
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("
[yellow]Ghost Vision Deactivated.[/yellow]")

if __name__ == "__main__":
    start_watcher()
