"""
Gestion des profils fournisseur (save/load JSON).
"""
from __future__ import annotations

import json
import os
from pathlib import Path


PROFILES_DIR = Path(__file__).parent.parent / "profils"


def list_profiles() -> list[str]:
    PROFILES_DIR.mkdir(exist_ok=True)
    return [p.stem for p in PROFILES_DIR.glob("*.json")]


def save_profile(name: str, data: dict) -> None:
    PROFILES_DIR.mkdir(exist_ok=True)
    path = PROFILES_DIR / f"{_safe(name)}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{_safe(name)}.json"
    if not path.exists():
        raise FileNotFoundError(f"Profil '{name}' introuvable.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
