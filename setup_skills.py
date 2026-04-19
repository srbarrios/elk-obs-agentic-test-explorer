"""
Standalone setup script to download the latest Elastic Agent Skills release
from https://github.com/elastic/agent-skills/releases and install it locally
under ./agent-skills so that the runtime skill tools can consume it.

Usage:
    python setup_skills.py
    python setup_skills.py --tag v0.2.3
    python setup_skills.py --force
"""

import argparse
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

GITHUB_REPO = "elastic/agent-skills"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASE_BY_TAG_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{{tag}}"
TARGET_DIR = "./agent-skills"


def _http_get(url: str, accept: str = "application/json") -> bytes:
    headers = {"Accept": accept, "User-Agent": "elastic-obs-agentic-test-explorer"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def resolve_release(tag: str | None) -> dict:
    url = RELEASE_BY_TAG_URL.format(tag=tag) if tag else LATEST_RELEASE_URL
    print(f"🔎 Resolving release metadata from {url}")
    return json.loads(_http_get(url).decode("utf-8"))


def pick_zip_url(release: dict) -> str:
    # Prefer an uploaded zip asset if present; fall back to the source zipball.
    for asset in release.get("assets") or []:
        name = (asset.get("name") or "").lower()
        if name.endswith(".zip"):
            return asset.get("browser_download_url") or asset.get("url")
    zipball = release.get("zipball_url")
    if not zipball:
        raise RuntimeError("Release has no zip asset nor zipball_url.")
    return zipball


def download_and_extract(zip_url: str, target_dir: str) -> None:
    print(f"⬇️  Downloading {zip_url}")
    data = _http_get(zip_url, accept="application/zip,application/octet-stream")
    print(f"📦 Downloaded {len(data):,} bytes. Extracting…")

    if os.path.isdir(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = zf.namelist()
        if not members:
            raise RuntimeError("Zip archive is empty.")
        # GitHub zipballs wrap everything in a single top-level folder.
        top_levels = {m.split("/", 1)[0] for m in members if m.strip()}
        strip_prefix = None
        if len(top_levels) == 1:
            strip_prefix = next(iter(top_levels)) + "/"

        for member in members:
            if member.endswith("/"):
                continue
            rel_path = member[len(strip_prefix):] if strip_prefix and member.startswith(strip_prefix) else member
            if not rel_path:
                continue
            out_path = os.path.join(target_dir, rel_path)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with zf.open(member) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            # Preserve unix exec bits so scripts/ entries remain runnable.
            info = zf.getinfo(member)
            mode = (info.external_attr >> 16) & 0o7777
            if mode:
                try:
                    os.chmod(out_path, mode)
                except OSError:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Elastic Agent Skills locally.")
    parser.add_argument("--tag", help="Specific release tag (e.g. v0.2.3). Defaults to latest.")
    parser.add_argument("--target", default=TARGET_DIR, help=f"Destination directory (default: {TARGET_DIR})")
    parser.add_argument("--force", action="store_true", help="Reinstall even if destination already has SKILL.md files.")
    args = parser.parse_args()

    already_installed = os.path.isdir(args.target) and any(
        "SKILL.md" in files for _, _, files in os.walk(args.target)
    )
    if already_installed and not args.force:
        print(f"✅ Skills already present at {args.target}. Use --force to reinstall.")
        return 0

    release = resolve_release(args.tag)
    print(f"🎯 Release: {release.get('name') or release.get('tag_name')}")
    zip_url = pick_zip_url(release)
    download_and_extract(zip_url, args.target)

    skill_count = sum(
        1 for _, _, files in os.walk(args.target) if "SKILL.md" in files
    )
    print(f"✨ Installed {skill_count} skill(s) into {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

