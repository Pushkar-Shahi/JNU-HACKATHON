"""SHA-256 fingerprinting for monitored files and directories."""
import hashlib
import json
from pathlib import Path


def sha256_file(path, chunk_size=65536):
    """Return the hex SHA-256 of a file, streamed in chunks so file size doesn't matter."""
    digest = hashlib.sha256()
    with open(Path(path), "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_directory(dir_path):
    """Return a single hex SHA-256 representing the entire directory contents.

    Walks all files (sorted by relative path), hashes each one, then hashes
    the combined (path, hash) pairs.  Any file change, addition, or deletion
    produces a different result.
    """
    dir_path = Path(dir_path)
    if not dir_path.is_dir():
        return ""
    manifest = directory_manifest(dir_path)
    combined = hashlib.sha256()
    for rel, fhash in sorted(manifest.items()):
        combined.update(f"{rel}:{fhash}\n".encode())
    return combined.hexdigest()


def directory_manifest(dir_path):
    """Return {relative_posix_path: sha256_hex} for every file in the directory tree."""
    dir_path = Path(dir_path)
    manifest = {}
    for f in sorted(dir_path.rglob("*")):
        if f.is_file():
            rel = f.relative_to(dir_path).as_posix()
            manifest[rel] = sha256_file(f)
    return manifest


def diff_manifests(old_manifest, new_manifest):
    """Compare two manifests and return a list of (change_type, filepath) tuples.

    change_type is one of: "modified", "added", "deleted".
    """
    changes = []
    all_keys = sorted(set(old_manifest) | set(new_manifest))
    for key in all_keys:
        if key not in old_manifest:
            changes.append(("added", key))
        elif key not in new_manifest:
            changes.append(("deleted", key))
        elif old_manifest[key] != new_manifest[key]:
            changes.append(("modified", key))
    return changes


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "target_system/config.json"
    p = Path(target)
    if p.is_dir():
        print(sha256_directory(p), target)
        for rel, h in sorted(directory_manifest(p).items()):
            print(f"  {h[:12]}…  {rel}")
    else:
        print(sha256_file(p), target)
