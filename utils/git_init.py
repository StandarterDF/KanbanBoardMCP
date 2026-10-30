import os
import subprocess


def init_git_repo(base_dir: str) -> bool:
    """Initialize git repo if not present. Returns True if initialized now."""
    git_dir = os.path.join(base_dir, ".git")
    if os.path.exists(git_dir):
        return False
    try:
        subprocess.run(["git", "init"], cwd=base_dir, check=True, capture_output=True)
    except Exception:
        return False
    # .gitignore already exists; ensure
    gi = os.path.join(base_dir, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write(".venv/\ntracker.db*\n__pycache__/\ntmp/\n")
    try:
        subprocess.run(
            ["git", "add", "-A"], cwd=base_dir, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Initial commit: Kanban MCP board"],
            cwd=base_dir,
            check=True,
            capture_output=True,
        )
    except Exception:
        # config user may be missing; try set local identity
        try:
            subprocess.run(
                ["git", "config", "user.email", "kanban@local"],
                cwd=base_dir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Kanban Board"],
                cwd=base_dir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "add", "-A"], cwd=base_dir, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial commit: Kanban MCP board"],
                cwd=base_dir,
                check=True,
                capture_output=True,
            )
        except Exception:
            pass
    return True
