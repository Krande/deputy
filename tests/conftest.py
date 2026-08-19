import pathlib
import sys

# Make the package importable without an install, so a bare `pytest` from the
# repo root works before `pip install -e .`.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
