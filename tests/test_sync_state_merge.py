"""A sharded push must not delete the other shards' work."""
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_merge_restores_files_held_only_in_s3(tmp_path, monkeypatch):
    import sync_state as S
    work = tmp_path / "work"; (work / "listings").mkdir(parents=True)
    # what S3 holds: shard A's ticker
    (work / "listings" / "AAA.csv").write_text("a\n")
    remote = tmp_path / "remote.tar.gz"
    with tarfile.open(remote, "w:gz") as t:
        t.add(work / "listings" / "AAA.csv", arcname="listings/AAA.csv")
    (work / "listings" / "AAA.csv").unlink()
    # what this runner has: shard B's ticker only
    (work / "listings" / "BBB.csv").write_text("b\n")

    class FakeS3:
        def download_file(self, bucket, key, dest):
            Path(dest).write_bytes(remote.read_bytes())

    monkeypatch.chdir(work)
    monkeypatch.setattr(S, "BUCKET", "bucket")
    n = S._merge_from_s3(FakeS3(), "uk_announcements")
    assert n == 1
    assert (work / "listings" / "AAA.csv").exists(), "shard A's work was deleted"
    assert (work / "listings" / "BBB.csv").read_text() == "b\n"


def test_local_file_wins_over_the_remote_copy(tmp_path, monkeypatch):
    import sync_state as S
    work = tmp_path / "work"; work.mkdir()
    old = work / "x.csv"; old.write_text("old\n")
    remote = tmp_path / "remote.tar.gz"
    with tarfile.open(remote, "w:gz") as t:
        t.add(old, arcname="x.csv")
    old.write_text("new\n")

    class FakeS3:
        def download_file(self, bucket, key, dest):
            Path(dest).write_bytes(remote.read_bytes())

    monkeypatch.chdir(work)
    monkeypatch.setattr(S, "BUCKET", "bucket")
    S._merge_from_s3(FakeS3(), "g")
    assert old.read_text() == "new\n", "this run's work must not be reverted"
