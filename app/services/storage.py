import os


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def list_files_recursive(folder: str):
    out = []
    for root, _, files in os.walk(folder):
        for name in files:
            out.append(os.path.join(root, name))
    return out
