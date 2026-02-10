def _section(name: str, items: list[str], default_text: str) -> str:
    if items:
        return f"{name}\n" + "\n".join(f"- {i}" for i in items)
    return f"{name}\n- {default_text}"


def build_executive_sitrep(
    mission: list[str],
    projects: list[str],
    decisions: list[str],
    blockers: list[str],
    deadlines: list[str],
    next_actions: list[str],
) -> str:
    missing = []
    if not mission:
        missing.append("Mission Focus")
    if not projects:
        missing.append("Active Projects")
    if not decisions:
        missing.append("Key Decisions")
    if not blockers:
        missing.append("Open Blockers/Risks")
    if not deadlines:
        missing.append("Upcoming Deadlines")
    if not next_actions:
        missing.append("Recommended Next Actions")

    report = []
    report.append("Executive Sitrep")
    report.append(_section("1. Mission Focus", mission, "Data gap: no mission constraints/facts recorded yet."))
    report.append(_section("2. Active Projects", projects, "Data gap: no active project entries found."))
    report.append(_section("3. Key Decisions", decisions, "Data gap: no decision logs found."))
    report.append(_section("4. Open Blockers/Risks", blockers, "Data gap: no blocker/risk records found."))
    report.append(_section("5. Upcoming Deadlines", deadlines, "Data gap: no deadlines parsed from memory."))
    report.append(_section("6. Recommended Next Actions", next_actions, "Data gap: no actionable tasks found."))
    if missing:
        report.append("Data Gap Summary")
        report.append("- Missing categories: " + ", ".join(missing))
    return "\n\n".join(report)
