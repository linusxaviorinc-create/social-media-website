import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRIMARY_WORKSPACE = Path.home() / "Documents" / ROOT.name


def choose_path(relative_path: str) -> Path:
    local_path = ROOT / relative_path
    if local_path.exists():
        return local_path

    if PRIMARY_WORKSPACE != ROOT:
        shared_path = PRIMARY_WORKSPACE / relative_path
        if shared_path.exists():
            return shared_path

    return local_path


BROWSERS_DIR = choose_path(".playwright-browsers")

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS_DIR))

PLATFORM_FILES = {
    "x": choose_path("social/sessions/x-session.json"),
    "instagram": choose_path("social/sessions/instagram-session.json"),
    "linkedin": choose_path("social/sessions/linkedin-session.json"),
}
