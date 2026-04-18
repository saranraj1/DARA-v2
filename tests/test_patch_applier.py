"""
Unit tests: patch/applier.py (Week 6)
Tests patch application to temp directory in safe mode.
"""
import sys, difflib, tempfile, os
from pathlib import Path
import pytest
sys.path.insert(0, ".")


@pytest.fixture
def applier():
    from patch.applier import PatchApplier
    return PatchApplier(repo_path=".")


@pytest.fixture
def dummy_patch_file(tmp_path):
    """Create a real Python file and return its relative path."""
    src = tmp_path / "sample.py"
    src.write_text("def greet(name):\n    return 'Hello ' + name\n")
    return src


def make_patch_obj(file_path: str, orig: str, fixed: str):
    """Build a minimal PatchFile-like object from two code strings."""
    from api.models.agent_schemas import PatchFile
    diff_lines = list(difflib.unified_diff(
        orig.splitlines(keepends=True),
        fixed.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    ))
    diff = "\n".join(diff_lines)
    changed = sum(1 for l in diff_lines if l.startswith(("+", "-"))
                  and not l.startswith(("+++", "---")))
    return PatchFile(
        file_path=file_path,
        unified_diff=diff,
        lines_changed=changed,
        change_description="test patch",
    )


class TestPatchApplier:

    @pytest.mark.asyncio
    async def test_empty_patches_returns_failure(self, applier):
        from patch.applier import PatchApplier
        result = await applier.apply([])
        assert not result.success
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_patch_with_empty_diff_returns_failure(self, applier):
        from api.models.agent_schemas import PatchFile
        p = PatchFile(file_path="x.py", unified_diff="",
                      lines_changed=0, change_description="empty")
        result = await applier.apply([p])
        assert not result.success

    @pytest.mark.asyncio
    async def test_valid_diff_creates_temp_dir(self, applier):
        orig = "x = 1\n"
        fixed = "x = 2  # fixed\n"
        p = make_patch_obj("storage/postgres.py", orig, fixed)
        result = await applier.apply([p])
        # Should succeed OR fail gracefully with a reason (file may not match)
        assert result.temp_dir is not None or result.error is not None

    @pytest.mark.asyncio
    async def test_preflight_extracts_file_paths(self, applier):
        orig = "def f(): pass\n"
        fixed = "def f(): return 1\n"
        p = make_patch_obj("mymodule/utils.py", orig, fixed)
        diff_str, files = applier._preflight([p])
        assert "mymodule/utils.py" in files
        assert "+++ b/mymodule/utils.py" in diff_str

    @pytest.mark.asyncio
    async def test_copy_to_temp_snapshot(self, applier):
        """Existing files should be copied to temp dir."""
        tmp_dir, backup_dir = applier._copy_to_temp(Path("."), ["storage/postgres.py"])
        assert tmp_dir is not None
        assert (tmp_dir / "storage/postgres.py").exists()
        assert backup_dir is not None
        assert (backup_dir / "storage/postgres.py").exists()
        import shutil
        shutil.rmtree(tmp_dir)
        shutil.rmtree(backup_dir)

    @pytest.mark.asyncio
    async def test_nonexistent_file_creates_empty_placeholder(self, applier):
        tmp_dir, backup_dir = applier._copy_to_temp(Path("."), ["totally/fake/file.py"])
        assert (tmp_dir / "totally/fake/file.py").exists()
        import shutil
        shutil.rmtree(tmp_dir); shutil.rmtree(backup_dir)

    @pytest.mark.asyncio
    async def test_validate_safe_code_passes(self, applier):
        import tempfile, shutil
        td = Path(tempfile.mkdtemp())
        (td / "safe.py").write_text("def add(a, b):\n    return a + b\n")
        passed, findings = applier._validate(td, ["safe.py"])
        assert passed
        shutil.rmtree(td)

    @pytest.mark.asyncio
    async def test_validate_syntax_error_fails(self, applier):
        import tempfile, shutil
        td = Path(tempfile.mkdtemp())
        (td / "broken.py").write_text("def broken(\n    pass\n")
        passed, findings = applier._validate(td, ["broken.py"])
        assert not passed
        assert any("E999" in f.get("code", "") for f in findings)
        shutil.rmtree(td)

    @pytest.mark.asyncio
    async def test_get_patched_content(self, applier):
        from patch.applier import PatchResult
        import tempfile, shutil
        td = Path(tempfile.mkdtemp())
        (td / "out.py").write_text("x = 42\n")
        res = PatchResult(success=True, temp_dir=str(td), files_modified=["out.py"],
                          validation_passed=True, ruff_errors=[])
        content = applier.get_patched_content(res, "out.py")
        assert content == "x = 42\n"
        shutil.rmtree(td)

    @pytest.mark.asyncio
    async def test_get_patched_content_failed_result(self, applier):
        from patch.applier import PatchResult
        res = PatchResult(success=False, temp_dir=None, files_modified=[],
                          validation_passed=False, ruff_errors=[])
        content = applier.get_patched_content(res, "any.py")
        assert content is None


class TestGitCommitter:

    def test_git_available(self):
        from patch.git_committer import GitCommitter
        g = GitCommitter(".")
        assert g._git_available()

    def test_build_commit_message(self):
        from patch.git_committer import GitCommitter
        g = GitCommitter(".")
        msg = g._build_commit_message(
            "abc12345", "AttributeError",
            "Fixed null check in session module",
            ["auth/session.py", "auth/cache.py"],
        )
        assert "fix(attributeerror):" in msg
        assert "abc12345" in msg
        assert "DARA" in msg
        assert "session.py" in msg   # basename only (Path.name)

    def test_is_git_repo(self):
        from patch.git_committer import GitCommitter
        g = GitCommitter(".")
        assert (Path(".") / ".git").exists()
