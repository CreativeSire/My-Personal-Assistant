from __future__ import annotations

from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent
GIT_HOOKS_DIR = REPO_DIR / ".git" / "hooks"
HOOK_PATH = GIT_HOOKS_DIR / "post-commit"


def main() -> None:
    if not GIT_HOOKS_DIR.exists():
        raise SystemExit(f"Not a git repository hooks path: {GIT_HOOKS_DIR}")

    script_path = (REPO_DIR / "update_build_ledger.py").as_posix()
    hook_body = "\n".join(
        [
            "#!/bin/sh",
            f'python "{script_path}" >/dev/null 2>&1 || true',
            "",
        ]
    )
    HOOK_PATH.write_text(hook_body, encoding="utf-8")
    try:
        HOOK_PATH.chmod(0o755)
    except Exception:
        pass
    print(f"Installed git hook: {HOOK_PATH}")


if __name__ == "__main__":
    main()

