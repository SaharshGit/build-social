"""Skills: packaged know-how in Markdown files, loaded into a prompt only when needed.

A skill is one .md file under a skills directory, named by its relative path
without the extension, e.g. "platforms/instagram" -> <root>/platforms/instagram.md.

Code decides which skills to load (from the agent's input), so each LLM call
carries only the guidance it needs. Generic: knows nothing about social media.
"""

from functools import lru_cache
from pathlib import Path


class SkillNotFoundError(FileNotFoundError):
    pass


@lru_cache(maxsize=None)  # skills are static files: read each once per process
def load_skill(root: Path, name: str) -> str:
    path = (root / f"{name}.md").resolve()
    # Names can come from user input (platform, format): never read outside the skills folder.
    if not path.is_relative_to(root.resolve()):
        raise SkillNotFoundError(f"skill name '{name}' points outside {root}")
    if not path.exists():
        available = sorted(str(p.relative_to(root).with_suffix("")) for p in root.rglob("*.md"))
        raise SkillNotFoundError(f"skill '{name}' not found in {root}. Available: {available}")
    return path.read_text().strip()


def render_skills(root: Path, names: list[str]) -> str:
    """Load several skills and join them into one block of prompt text."""
    return "\n\n".join(load_skill(root, name) for name in names)
