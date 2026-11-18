"""Storage contract tests: every backend must behave the same. No network calls."""

import pytest
from google.api_core.exceptions import NotFound

from buildsocial.harness import storage as storage_module
from buildsocial.harness.storage import GCSStorage, LocalStorage, MemoryStorage


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def download_as_text(self, encoding="utf-8"):
        if self.name not in self.bucket.data:
            raise NotFound(self.name)
        return self.bucket.data[self.name]

    def upload_from_string(self, text, content_type=None):
        self.bucket.data[self.name] = text

    def exists(self):
        return self.name in self.bucket.data

    def delete(self):
        if self.name not in self.bucket.data:
            raise NotFound(self.name)
        del self.bucket.data[self.name]


class FakeBucket:
    def __init__(self, client):
        self.client, self.data = client, {}

    def blob(self, name):
        return FakeBlob(self, name)


class FakeGCSClient:
    def __init__(self):
        self.b = FakeBucket(self)

    def bucket(self, name):
        return self.b

    def list_blobs(self, bucket, prefix=""):
        return [FakeBlob(bucket, n) for n in bucket.data if n.startswith(prefix)]


@pytest.fixture(params=["local", "memory", "gcs"])
def store(request, tmp_path):
    if request.param == "local":
        return LocalStorage(tmp_path)
    if request.param == "memory":
        return MemoryStorage()
    return GCSStorage("buildsocial", client=FakeGCSClient())


def test_write_read_exists_delete(store):
    assert not store.exists("traces/a.jsonl")
    store.write_text("traces/a.jsonl", "line ☕ बारिश\n")
    assert store.exists("traces/a.jsonl")
    assert store.read_text("traces/a.jsonl") == "line ☕ बारिश\n"
    store.delete("traces/a.jsonl")
    assert not store.exists("traces/a.jsonl")
    store.delete("traces/a.jsonl")  # deleting twice is fine


def test_missing_key_raises_file_not_found(store):
    with pytest.raises(FileNotFoundError):
        store.read_text("output/nope.md")


def test_list_by_prefix_sorted(store):
    for key in ["traces/b.jsonl", "traces/a.jsonl", "output/x/1.md", "research_cache/market/m.json"]:
        store.write_text(key, "x")
    assert store.list("traces/") == ["traces/a.jsonl", "traces/b.jsonl"]
    assert store.list("research_cache/") == ["research_cache/market/m.json"]
    assert store.list("evals/") == []


@pytest.mark.parametrize("bad", ["", "/etc/passwd", "../secrets", "a//b", "a/../b"])
def test_rejects_unsafe_keys(store, bad):
    with pytest.raises(ValueError, match="invalid storage key"):
        store.write_text(bad, "x")


def test_local_storage_writes_real_files(tmp_path):
    LocalStorage(tmp_path).write_text("output/brand/post.md", "hi")
    assert (tmp_path / "output" / "brand" / "post.md").read_text() == "hi"


def test_get_storage_from_env(monkeypatch, tmp_path):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)  # ignore the real .env
    monkeypatch.setenv("BUILDSOCIAL_STORAGE", "local")
    monkeypatch.setenv("BUILDSOCIAL_STORAGE_DIR", str(tmp_path))
    s = storage_module.get_storage()
    assert isinstance(s, LocalStorage) and s.root == tmp_path

    monkeypatch.setenv("BUILDSOCIAL_STORAGE", "gcs")
    monkeypatch.delenv("BUILDSOCIAL_GCS_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="needs BUILDSOCIAL_GCS_BUCKET"):
        storage_module.get_storage()

    monkeypatch.setenv("BUILDSOCIAL_STORAGE", "dropbox")
    with pytest.raises(RuntimeError, match="unknown BUILDSOCIAL_STORAGE"):
        storage_module.get_storage()
