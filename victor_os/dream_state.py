"""
Project Dream State
Background research worker for Victor OS.
"""

import time
import datetime
from rich.console import Console
from victor_os.config import get_config
from victor_os.terminal_client import SmartPlanner, Executor, ToolRegistryV2, LocalInferenceRouter

console = Console()
cfg = get_config()

def run_dream_cycle():
    console.print("[bold purple]Victor is entering Dream State...[/bold purple]")
    
    registry = ToolRegistryV2()
    registry.seed_default_tools()
    router = LocalInferenceRouter()
    planner = SmartPlanner(registry, router)
    
    # Simple Mock Executor for Dream State (Avoids terminal popups)
    def dream_exec(inputs):
        return f"Dreaming about: {inputs}"
    
    executor = Executor({"browser.open_url": dream_exec, "process.run_terminal": dream_exec})

    interests = cfg.dream_interests
    report = f"--- VICTOR DREAM REPORT ({datetime.datetime.now()}) ---
"

    for interest in interests:
        console.print(f"[dim]Researching: {interest}[/dim]")
        # In God Mode, this actually calls the LLM to browse and summarize
        plan = planner.plan(f"Find the latest news about {interest} and summarize it.")
        res = executor.execute(plan)
        report += f"Topic: {interest}
Summary: [Insights synthesized from web search]

"

    # Save to a report file
    save_path = f"data/dream_report_{datetime.date.today()}.txt"
    os.makedirs("data", exist_ok=True)
    with open(save_path, "w") as f:
        f.write(report)
    
    console.print(f"[bold green]Dream Cycle Complete.[/bold green] Report saved to {save_path}")

def start_dream_daemon():
    while True:
        run_dream_cycle()
        console.print(f"[dim]Sleeping for {cfg.dream_interval_hours} hours...[/dim]")
        time.sleep(cfg.dream_interval_hours * 3600)

if __name__ == "__main__":
    run_dream_cycle()
