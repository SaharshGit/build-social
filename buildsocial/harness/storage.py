"""Storage for runtime data (traces, research cache, saved posts, eval results).

Every read and write of runtime data goes through a Storage, so where it lives is one
setting, not a code change (same adapter pattern as the LLM layer):

    LocalStorage   files under a folder (default: the project folder)
    GCSStorage     a Google Cloud Storage bucket
    MemoryStorage  a dict, for tests

Keys look like paths: "traces/2026-11-18_run.jsonl", "research_cache/market/x.json".
Project files (prompts, skills, models.yaml, examples, eval cases) are NOT runtime
data: they ship with the code and are read as normal files.

Configuration (.env):
    BUILDSOCIAL_STORAGE=local|gcs          default: local
    BUILDSOCIAL_STORAGE_DIR=.              local root folder
    BUILDSOCIAL_GCS_BUCKET=buildsocial     bucket name (gcs)
    GCS_CREDENTIALS_JSON={...}             service account key contents, one line (gcs; secret)
    (or GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json, or a gcloud login)
"""

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path


class Storage(ABC):
    @abstractmethod
    def read_text(self, key: str) -> str:
        """Raises FileNotFoundError if the key doesn't exist."""

    @abstractmethod
    def write_text(self, key: str, text: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list(self, prefix: str) -> list[str]:
        """Keys starting with prefix, sorted."""

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def describe(self) -> str:
        """Where data goes, for messages (a folder or gs:// URL)."""

    def location(self, key: str) -> str:
        """A human-readable location for one key."""
        base = self.describe()
        return f"{base}{key}" if base.endswith("/") else f"{base}/{key}"


def _check_key(key: str) -> str:
    parts = key.split("/")
    if not key or key.startswith("/") or ".." in parts or "" in parts:
        raise ValueError(f"invalid storage key: {key!r}")
    return key


class LocalStorage(Storage):
    def __init__(self, root: Path | str = "."):
        self.root = Path(root)

    def path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def read_text(self, key: str) -> str:
        return self.path(key).read_text(encoding="utf-8")

    def write_text(self, key: str, text: str) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix.rstrip("/") if prefix else self.root
        folder = base if base.is_dir() else base.parent
        if not folder.exists():
            return []
        keys = (str(p.relative_to(self.root)).replace(os.sep, "/") for p in folder.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix))

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def describe(self) -> str:
        return str(self.root)


class MemoryStorage(Storage):
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def read_text(self, key: str) -> str:
        if key not in self.files:
            raise FileNotFoundError(key)
        return self.files[key]

    def write_text(self, key: str, text: str) -> None:
        self.files[_check_key(key)] = text

    def exists(self, key: str) -> bool:
        return key in self.files

    def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self.files if k.startswith(prefix))

    def delete(self, key: str) -> None:
        self.files.pop(key, None)

    def describe(self) -> str:
        return "memory://"


class GCSStorage(Storage):
    """A Google Cloud Storage bucket. Objects can't be appended to, so callers write whole files."""

    def __init__(self, bucket: str, credentials_json: str | None = None, client=None):
        if client is None:
            from google.cloud import storage as gcs  # imported here so local mode doesn't need it

            if credentials_json:
                from google.oauth2 import service_account

                info = json.loads(credentials_json)
                creds = service_account.Credentials.from_service_account_info(info)
                client = gcs.Client(project=info.get("project_id"), credentials=creds)
            else:
                client = gcs.Client()  # GOOGLE_APPLICATION_CREDENTIALS or a gcloud login
        self.bucket_name = bucket
        self._bucket = client.bucket(bucket)

    def read_text(self, key: str) -> str:
        from google.api_core.exceptions import NotFound

        try:
            return self._bucket.blob(_check_key(key)).download_as_text(encoding="utf-8")
        except NotFound as e:
            raise FileNotFoundError(f"gs://{self.bucket_name}/{key}") from e

    def write_text(self, key: str, text: str) -> None:
        self._bucket.blob(_check_key(key)).upload_from_string(text, content_type="application/json; charset=utf-8")

    def exists(self, key: str) -> bool:
        return self._bucket.blob(_check_key(key)).exists()

    def list(self, prefix: str) -> list[str]:
        # Skip "folder" placeholders (names ending in "/"), which the Cloud console
        # creates for "Create folder": they aren't files.
        blobs = self._bucket.client.list_blobs(self._bucket, prefix=prefix)
        return sorted(b.name for b in blobs if not b.name.endswith("/"))

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket.blob(_check_key(key)).delete()
        except NotFound:
            pass

    def describe(self) -> str:
        return f"gs://{self.bucket_name}"


def get_storage() -> Storage:
    """The configured storage (reads .env)."""
    from dotenv import load_dotenv

    load_dotenv()
    kind = os.environ.get("BUILDSOCIAL_STORAGE", "local").strip().lower()
    if kind == "local":
        return LocalStorage(os.environ.get("BUILDSOCIAL_STORAGE_DIR", "."))
    if kind == "gcs":
        bucket = os.environ.get("BUILDSOCIAL_GCS_BUCKET")
        if not bucket:
            raise RuntimeError("BUILDSOCIAL_STORAGE=gcs needs BUILDSOCIAL_GCS_BUCKET in .env")
        return GCSStorage(bucket, credentials_json=os.environ.get("GCS_CREDENTIALS_JSON") or None)
    raise RuntimeError(f"unknown BUILDSOCIAL_STORAGE '{kind}' (use local or gcs)")
