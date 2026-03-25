import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BROWSERS_DIR = ROOT / ".playwright-browsers"

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS_DIR))

PLATFORM_FILES = {
    "x": ROOT / "social" / "sessions" / "x-session.json",
    "instagram": ROOT / "social" / "sessions" / "instagram-session.json",
    "linkedin": ROOT / "social" / "sessions" / "linkedin-session.json",
}
