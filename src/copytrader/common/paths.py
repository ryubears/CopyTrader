"""
Where the project keeps its files.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"     # The database. Not committed.
