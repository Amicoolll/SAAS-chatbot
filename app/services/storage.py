import os
import json
from typing import Any

def ensure_dirs(base_dir: str):
    os.makedirs(os.path.join(base_dir, "raw"), exist_ok=True)
    os.makedirs(os.path.join(base_dir, "chunks"), exist_ok=True)
    os.makedirs(os.path.join(base_dir, "index"), exist_ok=True)

def write_text(path: str, content: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def write_bytes(path: str, content: bytes):
    with open(path, "wb") as f:
        f.write(content)

def write_json(path: str, obj: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def list_files_recursive(folder: str):
    out = []
    for root, _, files in os.walk(folder):
        for name in files:
            out.append(os.path.join(root, name))
    return out