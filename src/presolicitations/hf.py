"""Hugging Face I/O. The dataset is the only home of the data; git never holds it.

Reading is anonymous (the dataset is public). Writing needs HF_TOKEN in the environment and never
deletes anything on the Hub: the dataset is append-only.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import config

PULL_PATTERNS = ["data/notices/*", "data/attachments/*", "data/status_history.parquet"]
PUSH_PATTERNS = ["data/**", "README.md"]


def _root(data_dir: str | Path) -> Path:
    d = Path(data_dir).resolve()
    if d.name != "data":
        raise ValueError("the data directory must be named 'data' (it mirrors data/ in the dataset)")
    return d.parent


def pull(data_dir: str | Path, repo: str = config.HF_REPO) -> bool:
    """Download the small tables (not the texts). False if the dataset does not exist yet."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import RepositoryNotFoundError

    try:
        snapshot_download(repo_id=repo, repo_type="dataset", allow_patterns=PULL_PATTERNS,
                          local_dir=str(_root(data_dir)))
    except RepositoryNotFoundError:
        return False
    return True


def push(data_dir: str | Path, repo: str = config.HF_REPO, message: str = "Daily update") -> None:
    from huggingface_hub import HfApi

    root = _root(data_dir)
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN is not set; refusing to publish")
    HfApi(token=token).upload_folder(
        folder_path=str(root), repo_id=repo, repo_type="dataset",
        allow_patterns=PUSH_PATTERNS, commit_message=message,
    )
