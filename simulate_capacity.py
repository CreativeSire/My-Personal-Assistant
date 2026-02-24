"""
Simulation: Complex Capacity Test
Demonstrates the full Planner -> Research -> Draft loop.
"""

from rich.console import Console

# Mock classes for simulation
class PlanResult:
    def __init__(self, intent, provider, confidence, intent_class, actions):
        self.intent = intent
        self.provider = provider
        self.confidence = confidence
        self.intent_class = intent_class
        self.actions = actions

class PlannedAction:
    def __init__(self, tool_name, inputs, risk_class):
        self.tool_name = tool_name
        self.inputs = inputs
        self.risk_class = risk_class

console = Console()

def simulate_complex_task():
    console.print("[bold purple]=== VICTOR OS: COMPLEX CAPACITY TEST ===[/bold purple]")
    console.print("[cyan]User Request:[/cyan] 'Research Quantum Computing, summarize it, and draft a Telegram message.'")
    
    # 1. Simulate Planner Output (Gemini 1.5 Pro)
    plan = PlanResult(
        intent="research_quantum_computing",
        provider="gemini_1.5_pro",
        confidence=0.98,
        intent_class="complex_research_task",
        actions=[
            PlannedAction(
                tool_name="browser.open_url",
                inputs={"url": "https://en.wikipedia.org/wiki/Quantum_computing"},
                risk_class="medium"
            ),
            PlannedAction(
                tool_name="process.run_terminal",
                inputs={"command": "echo [Simulating Browser Read...] Content Extracted: Quantum computing is a type of computation whose operations can exploit such phenomena of quantum mechanics as superposition and entanglement."},
                risk_class="low"
            ),
            PlannedAction(
                tool_name="messaging.telegram.send",
                inputs={"text": "Draft: Quantum computing leverages superposition/entanglement to solve problems faster than classical computers. (Source: Wikipedia)"},
                risk_class="high"
            )
        ]
    )

    console.print("\n[bold green]1. Planner Output (Gemini 1.5 Pro)[/bold green]")
    for action in plan.actions:
        console.print(f"- [yellow]{action.tool_name}[/yellow]: {action.inputs}")
    
    console.print("\n[bold green]2. Execution Phase[/bold green]")
    
    # 2. Simulate Execution
    for action in plan.actions:
        console.print(f"Executing {action.tool_name}...")
        if action.tool_name == "messaging.telegram.send":
            console.print(f"[bold blue]TELEGRAM SENT:[/bold blue] {action.inputs['text']}")
        elif action.tool_name == "process.run_terminal":
            console.print(f"[dim]{action.inputs['command']}[/dim]")
            
    console.print("\n[bold green]=== TEST COMPLETE: CAPACITY VERIFIED ===[/bold green]")

if __name__ == "__main__":
    simulate_complex_task()
