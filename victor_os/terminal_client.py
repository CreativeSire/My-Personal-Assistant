"""
Victor OS - Terminal Neural Interface
The direct command-line interface for the Victor Agent System.
Implements the OpenClaw-style Plan-Execute-Review loop.
"""

import sys
import os
import subprocess
import shlex
from typing import Any
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.markdown import Markdown

# Import Victor Core
try:
    from victor_os.agent_orchestrator import Planner, Executor, Reviewer, PlannerExecutorReviewer
    from victor_os.tool_registry_v2 import ToolRegistryV2
    from victor_os.local_inference_router import LocalInferenceRouter
    from victor_os.config import get_config
except ImportError:
    # Fallback for running directly from root without package install
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from victor_os.agent_orchestrator import Planner, Executor, Reviewer, PlannerExecutorReviewer
    from victor_os.tool_registry_v2 import ToolRegistryV2
    from victor_os.local_inference_router import LocalInferenceRouter
    from victor_os.config import get_config

console = Console()
cfg = get_config()

# --- Tool Implementations ( The Muscles ) ---

def tool_run_terminal(inputs: dict[str, Any]) -> str:
    command = inputs.get("command", "")
    if not command:
        return "Error: No command provided."
    
    # Security Check (Basic)
    if "rm -rf /" in command or "format c:" in command.lower():
        return "Error: Destructive command blocked by Safety Kernel."

    try:
        # Run in shell, capture output
        result = subprocess.run(
            command, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=30
        )
        return f"Stdout:
{result.stdout}
Stderr:
{result.stderr}
Return Code: {result.returncode}"
    except Exception as e:
        return f"Execution failed: {str(e)}"

def tool_fs_list(inputs: dict[str, Any]) -> str:
    path = inputs.get("path", ".")
    try:
        items = os.listdir(path)
        return "
".join(items)
    except Exception as e:
        return f"Error listing directory: {str(e)}"

def tool_fs_read(inputs: dict[str, Any]) -> str:
    path = inputs.get("path", "")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

def tool_browser_open(inputs: dict[str, Any]) -> str:
    url = inputs.get("url", "")
    import webbrowser
    webbrowser.open(url)
    return f"Opened {url}"

# --- The Client ---

def start_terminal_session():
    console.print(Panel.fit("[bold green]Victor OS[/bold green] - Neural Interface v2.0", border_style="green"))
    console.print("[dim]System Online. Ready for commands.[/dim]")

    # Initialize Core
    registry = ToolRegistryV2()
    registry.seed_default_tools()
    router = LocalInferenceRouter()
    
    # Map Tools to Functions
    executors = {
        "process.run_terminal": tool_run_terminal,
        "filesystem.list": tool_fs_list,
        "filesystem.read": tool_fs_read,
        "browser.open_url": tool_browser_open,
        # Fallbacks for the dummy planner
        "messaging.telegram.send": lambda x: f"Telegram Simulated: {x}",
        "messaging.whatsapp.draft": lambda x: f"WhatsApp Draft: {x}",
        "messaging.whatsapp.send": lambda x: f"WhatsApp Sent: {x}",
    }

import json
from google import genai
from google.genai import types

from victor_os.alter_ego import get_style_instruction

# --- Smart Planner Upgrade ---
class SmartPlanner(Planner):
    def __init__(self, registry: ToolRegistryV2, router: LocalInferenceRouter):
        super().__init__(registry, router)
        self.client = genai.Client(api_key=cfg.gemini_api_key)
        self.model_id = cfg.model_name
        self.style_instruction = get_style_instruction()

    def plan(self, intent: str, context: dict[str, Any] | None = None) -> PlanResult:
        if "exit" in intent or "quit" in intent:
            return PlanResult(intent, "local", 1.0, "exit", [])

        # Build Tool Definition for LLM
        tool_docs = "\n".join([
            f"- {t['tool_name']}: {t['action']}. Schema: {json.dumps(t['input_schema'])}"
            for t in self.registry.list_tools()
        ])

        prompt = f"""
        You are the Victor OS Planner. Your goal is to convert user intent into a sequence of tool calls.
        
        STYLE INSTRUCTION:
        {self.style_instruction}
        
        AVAILABLE TOOLS:
        {tool_docs}

        USER INTENT: "{intent}"

        RESPONSE FORMAT:
        Return ONLY a JSON list of actions, like this:
        [
            {{"tool_name": "filesystem.list", "inputs": {{"path": "."}}, "risk_class": "low"}},
            {{"tool_name": "process.run_terminal", "inputs": {{"command": "echo hello"}}, "risk_class": "high"}}
        ]
        If no tool is suitable, return an empty list [].
        """

        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
            
            # Use raw text if it's already a list or parse it
            raw_text = response.text.strip()
            # Handle potential markdown wrapping
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            
            actions_data = json.loads(raw_text)
            actions = [
                PlannedAction(
                    tool_name=a["tool_name"],
                    inputs=a["inputs"],
                    risk_class=a.get("risk_class", "medium")
                )
                for a in actions_data
            ]

            return PlanResult(
                intent=intent,
                provider="gemini",
                confidence=0.95,
                intent_class="autonomous",
                actions=actions
            )
        except Exception as e:
            console.print(f"[dim]LLM Planning failed: {e}. Falling back to heuristics.[/dim]")
            return super().plan(intent, context)

    planner = SmartPlanner(registry, router)
    executor = Executor(executors)
    reviewer = Reviewer()
    engine = PlannerExecutorReviewer(planner, executor, reviewer)

    while True:
        try:
            user_input = Prompt.ask("
[bold cyan]You[/bold cyan]")
            if user_input.lower() in ["exit", "quit"]:
                console.print("[yellow]Shutting down Neural Interface...[/yellow]")
                break
            
            if not user_input.strip():
                continue

            with console.status("[bold green]Thinking...[/bold green]", spinner="dots"):
                # 1. Plan
                # Note: The current planner is a heuristic stub. 
                # In Phase 3, we replace this with an LLM call.
                plan_result = planner.plan(user_input)
            
            # Show Plan
            if plan_result.actions:
                table = Table(title="Proposed Plan", show_header=True)
                table.add_column("Tool", style="cyan")
                table.add_column("Inputs", style="magenta")
                table.add_column("Risk", style="red")
                
                for action in plan_result.actions:
                    table.add_row(
                        action.tool_name, 
                        str(action.inputs), 
                        action.risk_class
                    )
                console.print(table)
                
                # Confirm (if risky)
                if any(a.risk_class == "high" for a in plan_result.actions):
                    if not Prompt.ask("Execute?", choices=["y", "n"], default="y") == "y":
                        console.print("[red]Aborted.[/red]")
                        continue

                # 2. Execute
                with console.status("[bold red]Executing...[/bold red]", spinner="runner"):
                    exec_result = executor.execute(plan_result)

                # Show Results
                for res in exec_result.action_results:
                    if res["ok"]:
                        console.print(Panel(str(res.get("output", "Done")), title=f"[green]{res['tool_name']} Success[/green]", expand=False))
                    else:
                        console.print(Panel(str(res.get("error", "Unknown Error")), title=f"[red]{res['tool_name']} Failed[/red]", expand=False))

            else:
                # Fallback for "Chat" or unhandled intents
                console.print(f"[dim]No tools needed. (Planner Logic: {plan_result.intent_class})[/dim]")
                # Here we would normally call the LLM for a chat response
                console.print(f"[green]Victor:[/green] I understood '{user_input}' but the basic planner didn't map it to a tool.")

        except KeyboardInterrupt:
            console.print("
[yellow]Session Interrupted.[/yellow]")
            break
        except Exception as e:
            console.print(f"[bold red]System Error:[/bold red] {e}")

if __name__ == "__main__":
    start_terminal_session()
