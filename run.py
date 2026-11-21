#!/usr/bin/env python3
"""Start from the reviewed source or a verified installed runtime snapshot."""
import hashlib
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent
manifest_file = ROOT / "runtime-manifest.json"
if manifest_file.exists():
    manifest = json.loads(manifest_file.read_text())
    expected = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if ROOT.name != expected:
        raise SystemExit("Pinned runtime identity mismatch")
    inventory = set()
    for candidate in ROOT.rglob("*"):
        relative = candidate.relative_to(ROOT)
        if candidate.is_symlink():
            raise SystemExit("Pinned runtime path mismatch")
        if candidate.is_file() and not ("__pycache__" in relative.parts and candidate.suffix == ".pyc"):
            inventory.add(str(relative))
    if inventory != set(manifest) | {"runtime-manifest.json"}:
        raise SystemExit("Pinned runtime file inventory mismatch")
    for name, checksum in manifest.items():
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or ROOT not in path.resolve().parents:
            raise SystemExit("Pinned runtime path mismatch")
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise SystemExit("Pinned runtime content mismatch")
elif ROOT.parent.name == "releases":
    raise SystemExit("Pinned runtime manifest is missing")

sys.path.insert(0, str(ROOT))
runpy.run_module("pr_review.cli", run_name="__main__")
