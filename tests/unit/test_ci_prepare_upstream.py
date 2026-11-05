import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import ci_prepare_upstream as prepare_mod

ROOT = Path(__file__).resolve().parents[2]

class TestCIPrepareUpstream(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp = Path(self.td.name)
        self.target_root = self.tmp / "workspace"
        self.target_root.mkdir()
        self.cache_dir = self.target_root / ".upstream" / ".hfc-source-cache"

    def tearDown(self):
        self.td.cleanup()

    def _create_remote_repo(self, count=5):
        remote = self.tmp / "remote.git"
        remote.mkdir()
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
        src = self.tmp / "src"
        src.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(src)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "Tester"], check=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "tester@example.com"], check=True)
        subprocess.run(["git", "-C", str(src), "remote", "add", "origin", str(remote)], check=True)
        commits = []
        for i in range(count):
            (src / f"file_{i}.txt").write_text(f"content {i}")
            subprocess.run(["git", "-C", str(src), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(src), "commit", "-m", f"commit {i}"], check=True, capture_output=True)
            sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            commits.append(sha)
        subprocess.run(["git", "-C", str(src), "push", "origin", "main"], check=True, capture_output=True)
        return str(remote), commits

    def test_validate_manifest_rejects_invalid_count_or_structure(self):
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([])
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "a", "ref": "bad", "path": ".upstream/a"}])

    def test_validate_manifest_rejects_unsafe_paths(self):
        valid_ref = "0" * 40
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "x", "ref": valid_ref, "path": "/etc/passwd"}])
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "x", "ref": valid_ref, "path": ".upstream/../outside"}])
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "x", "ref": valid_ref, "path": "other/sub"}])
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "x", "ref": valid_ref, "path": ".upstream/a/b"}])
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "x", "ref": valid_ref, "path": ".upstream/.git"}])
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "x", "ref": valid_ref, "path": ".upstream/hermes_cache.git"}])

    def test_validate_manifest_rejects_invalid_ref_format(self):
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "x", "ref": "29112bef", "path": ".upstream/a"}])
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "x", "ref": "not_hex_" + "0" * 32, "path": ".upstream/a"}])
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "x", "ref": "--upload-pack=evil", "path": ".upstream/a"}])

    def test_validate_manifest_rejects_duplicate_paths(self):
        valid_ref1 = "a" * 40
        valid_ref2 = "b" * 40
        entries = [{"name": "1", "ref": valid_ref1, "path": ".upstream/same"},
                   {"name": "2", "ref": valid_ref2, "path": ".upstream/same"}]
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest(entries)

    def test_preflight_refuses_preexisting_targets_or_uninspectable_metadata(self):
        valid_ref = "a" * 40
        entries = [{"name": "1", "ref": valid_ref, "path": ".upstream/exists"}]
        target = self.target_root / ".upstream/exists"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir()
        with self.assertRaises(ValueError):
            prepare_mod.preflight_check(entries, self.target_root, self.cache_dir)

    def test_preflight_refuses_symlink_or_unsafe_target_parent(self):
        valid_ref = "a" * 40
        entries = [{"name": "1", "ref": valid_ref, "path": ".upstream/target"}]
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.target_root / ".upstream").symlink_to(outside)
        with self.assertRaises(ValueError):
            prepare_mod.preflight_check(entries, self.target_root, self.cache_dir)

    def test_remote_url_safety_rejects_option_injection(self):
        with self.assertRaises(ValueError):
            prepare_mod.validate_remote_url("--upload-pack=calc")
        with self.assertRaises(ValueError):
            prepare_mod.validate_remote_url("-o")

    def test_preflight_preserves_unknown_existing_cache(self):
        self.cache_dir.mkdir(parents=True)
        sentinel = self.cache_dir / "sentinel"
        sentinel.write_bytes(b"preserve")
        entries = [{"name": "one", "ref": "a" * 40, "path": ".upstream/new"}]
        with self.assertRaises(ValueError):
            prepare_mod.preflight_check(entries, self.target_root, self.cache_dir)
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        self.assertFalse((self.target_root / ".upstream/new").exists())

    def test_manifest_rejects_reserved_and_control_character_paths(self):
        for path in (".upstream/fixed", ".upstream/.hfc-source-cache", ".upstream/new\n"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                prepare_mod.validate_manifest([{"name": "one", "ref": "a" * 40, "path": path}])

    def test_manifest_rejects_newline_ref(self):
        with self.assertRaises(ValueError):
            prepare_mod.validate_manifest([{"name": "one", "ref": "a" * 40 + "\n", "path": ".upstream/new"}])

    def test_preflight_metadata_error_precedes_mutation(self):
        original = Path.lstat

        def inspected(path, *args, **kwargs):
            if path == self.cache_dir:
                raise PermissionError("synthetic metadata refusal")
            return original(path, *args, **kwargs)

        entries = [{"name": "one", "ref": "a" * 40, "path": ".upstream/new"}]
        with mock.patch.object(Path, "lstat", inspected), self.assertRaises(ValueError):
            prepare_mod.preflight_check(entries, self.target_root, self.cache_dir)
        self.assertFalse((self.target_root / ".upstream").exists())

    def test_successful_fetch_and_detached_worktrees_with_duplicate_pins(self):
        remote, commits = self._create_remote_repo(count=3)
        entries = []
        for i in range(19):
            ref = commits[i % len(commits)]
            entries.append({"name": f"step_{i}", "ref": ref, "path": f".upstream/repo_{i:02d}"})
        with mock.patch.object(prepare_mod.subprocess, "run", wraps=subprocess.run) as calls:
            prepare_mod.prepare_upstream_worktrees(entries, self.target_root, self.cache_dir, remote_url=remote)
        fetches = [call.args[0] for call in calls.call_args_list if "fetch" in call.args[0]]
        self.assertEqual(len(fetches), 1)
        refs = fetches[0][fetches[0].index("--depth=1") + 2:]
        self.assertEqual(set(refs), set(commits))
        self.assertEqual(len(refs), len(commits))
        for entry in entries:
            p = self.target_root / entry["path"]
            self.assertTrue(p.exists())
            self.assertTrue(p.is_dir())
            head = subprocess.run(["git", "-C", str(p), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(head, entry["ref"])
            is_wt = subprocess.run(["git", "-C", str(p), "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(is_wt, "true")
            status = subprocess.run(["git", "-C", str(p), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(status, "")
            git_file = p / ".git"
            self.assertTrue(git_file.exists())
            self.assertTrue(git_file.is_file())

    def test_fetch_failure_fails_closed_without_retaining_dirty_worktrees(self):
        remote, _commits = self._create_remote_repo(count=1)
        missing_ref = "f" * 40
        entries = [{"name": "bad", "ref": missing_ref, "path": ".upstream/repo_bad"}]
        with self.assertRaises(RuntimeError):
            prepare_mod.prepare_upstream_worktrees(entries, self.target_root, self.cache_dir, remote_url=remote)
        self.assertFalse((self.target_root / ".upstream/repo_bad").exists())

    def test_official_manifest_file_matches_exact_20_pins(self):
        manifest_file = ROOT / "tools/upstream_fixtures.json"
        self.assertTrue(manifest_file.exists())
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 20)
        paths = [e["path"] for e in manifest]
        self.assertEqual(len(paths), len(set(paths)))
        prepare_mod.validate_manifest(manifest)

    def test_partial_failure_retains_evidence_and_unrelated_files(self):
        remote, commits = self._create_remote_repo(count=2)
        unrelated = self.target_root / ".upstream" / "user-data"
        unrelated.mkdir(parents=True)
        sentinel = unrelated / "sentinel"
        sentinel.write_bytes(b"preserve")
        entries = [{"name": str(index), "ref": ref, "path": f".upstream/repo_{index}"}
                   for index, ref in enumerate(commits)]
        original = prepare_mod._git
        worktrees = 0

        def execute(arguments, *, phase):
            nonlocal worktrees
            if phase == "worktree":
                worktrees += 1
                if worktrees == 2:
                    raise RuntimeError("synthetic second-stage failure")
            return original(arguments, phase=phase)

        with mock.patch.object(prepare_mod, "_git", execute), self.assertRaises(RuntimeError):
            prepare_mod.prepare_upstream_worktrees(entries, self.target_root, self.cache_dir, remote_url=remote)
        first = self.target_root / entries[0]["path"]
        self.assertEqual((first / "file_0.txt").read_text(), "content 0")
        self.assertTrue(self.cache_dir.is_dir())
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_cli_does_not_accept_remote_or_cache_overrides(self):
        for flag in ("--remote-url", "--cache-dir"):
            with self.subTest(flag=flag):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tools/ci_prepare_upstream.py"),
                     "--manifest", str(ROOT / "tools/upstream_fixtures.json"),
                     "--target-root", str(self.target_root), flag, "ignored"],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertFalse((self.target_root / ".upstream").exists())

if __name__ == "__main__":
    unittest.main()
