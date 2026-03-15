"""
CrewAI Engine — plugged into Victor Platform.
Wraps the 3-agent crew (Claude + Gemini + GPT-4o) and streams output line by line.
"""
from __future__ import annotations

import os
import sys
import threading
import queue as _queue
from typing import Iterator


def run_crew_streaming(user_idea: str) -> Iterator[str]:
    """
    Runs the CrewAI crew for `user_idea` and yields output lines as they arrive.
    Uses a thread + queue so FastAPI can stream via SSE.
    """
    output_queue: _queue.Queue[str | None] = _queue.Queue()

    def _worker():
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'victor_os', '.env'))
            load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'MyAIComputer', '.env'))

            # Add MyAIComputer to path so image_tool imports work
            myai_path = os.path.join(os.path.dirname(__file__), '..', '..', 'MyAIComputer')
            if myai_path not in sys.path:
                sys.path.insert(0, myai_path)

            from crewai import Agent, Task, Crew, Process, LLM

            output_queue.put(f"data: 🚀 Crew initialising for: {user_idea}\n\n")

            claude_llm = LLM(
                model="anthropic/claude-sonnet-4-6",
                api_key=os.getenv("ANTHROPIC_API_KEY"),
            )
            gemini_llm = LLM(
                model="gemini/gemini-3.1-pro-preview",
                api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            )
            openai_llm = LLM(
                model="gpt-4o",
                api_key=os.getenv("OPENAI_API_KEY"),
            )

            architect = Agent(
                role="Lead Software Architect",
                goal="Break down user requests into clear, step-by-step technical blueprints.",
                backstory="Seasoned architect with 20 years across web, mobile, and data platforms. Always picks the simplest stack that meets the need.",
                llm=claude_llm, verbose=False,
            )
            visual_researcher = Agent(
                role="UX Designer and Researcher",
                goal="Surface the best free APIs and describe a UI design for the project.",
                backstory="Creative UX designer who always finds open APIs that save engineering time.",
                llm=gemini_llm, verbose=False,
            )
            developer = Agent(
                role="Senior Full-Stack Developer",
                goal="Write clean, production-ready code implementing the architect's blueprint.",
                backstory="Pragmatic senior developer who writes self-documenting, error-handled code.",
                llm=openai_llm, verbose=False,
            )

            task_plan = Task(
                description=f'Analyse: "{user_idea}". Produce a tech-stack blueprint with folder structure and key components.',
                expected_output="Markdown architecture document.",
                agent=architect,
            )
            task_design = Task(
                description=f'For "{user_idea}", describe the UI layout and list 3 free APIs that could power it.',
                expected_output="UI description + API list with base URLs.",
                agent=visual_researcher,
            )
            task_build = Task(
                description=f'Write production-ready code for "{user_idea}" using the blueprint above.',
                expected_output="Complete, commented code files with README.",
                agent=developer,
                context=[task_plan, task_design],
            )

            crew = Crew(
                agents=[architect, visual_researcher, developer],
                tasks=[task_plan, task_design, task_build],
                process=Process.sequential,
                verbose=False,
            )

            output_queue.put("data: ⚡ Claude is architecting the blueprint...\n\n")
            result = crew.kickoff()

            # Save output
            workspace = os.path.join(os.path.dirname(__file__), '..', 'victor_os', 'memory_store')
            os.makedirs(workspace, exist_ok=True)
            out_path = os.path.join(workspace, 'last_build.md')
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(f"# {user_idea}\n\n{result}")

            # Stream result in chunks
            full_text = str(result)
            chunk_size = 300
            for i in range(0, len(full_text), chunk_size):
                chunk = full_text[i:i+chunk_size].replace('\n', '\\n')
                output_queue.put(f"data: CHUNK:{chunk}\n\n")

            output_queue.put("data: DONE\n\n")

        except Exception as e:
            output_queue.put(f"data: ERROR:{str(e)}\n\n")
        finally:
            output_queue.put(None)  # sentinel

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    while True:
        item = output_queue.get()
        if item is None:
            break
        yield item
