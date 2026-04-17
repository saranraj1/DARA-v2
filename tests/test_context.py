"""Unit tests: context builder pipeline"""
import pytest, sys
sys.path.insert(0,".")

from context.ast_chunker import ASTChunker, CodeChunk
from context.git_analyzer import GitAnalyzer
from context.retriever import count_tokens

class TestASTChunker:
    @pytest.fixture
    def chunker(self): return ASTChunker()

    def test_chunks_real_file(self, chunker):
        chunks = chunker.chunk_file("storage/postgres.py")
        assert len(chunks) >= 5
        for c in chunks:
            assert c.line_start > 0
            assert c.line_end >= c.line_start
            assert len(c.content) > 0

    def test_handles_empty_file(self, chunker):
        chunks = chunker.chunk_file("__nonexistent__.py", source="")
        assert chunks == []

    def test_handles_no_functions(self, chunker):
        source = "X = 1\nY = 2\nZ = X + Y\n"
        chunks = chunker.chunk_file("test.py", source=source)
        assert len(chunks) >= 1  # FILE-level chunk
        assert chunks[0].function_name is None

    def test_class_method_context(self, chunker):
        source = (
            "class MyClass:\n"
            "    def my_method(self, x):\n"
            "        return x * 2\n"
            "    def another(self):\n"
            "        pass\n"
            "        pass\n"
            "        pass\n"
        )
        chunks = chunker.chunk_file("test.py", source=source)
        funcs = [c for c in chunks if c.function_name is not None]
        if funcs:
            # At least one should have class context
            assert any(c.class_name == "MyClass" for c in funcs)

    def test_fallback_for_non_python(self, chunker):
        source = "package main\n\nfunc main() {\n    fmt.Println(\"hello\")\n}\n"
        chunks = chunker.chunk_file("main.go", source=source)
        assert len(chunks) >= 1

    def test_line_boundaries_correct(self, chunker):
        chunks = chunker.chunk_file("storage/postgres.py")
        lines = open("storage/postgres.py").readlines()
        for c in chunks:
            assert 1 <= c.line_start <= len(lines)
            assert c.line_end <= len(lines)

class TestTokenCounter:
    def test_empty_string(self): assert count_tokens("") == 0
    def test_short_string(self): assert count_tokens("def foo(): pass") > 0
    def test_longer_is_more_tokens(self):
        t1 = count_tokens("x = 1")
        t2 = count_tokens("x = 1\n" * 50)
        assert t2 > t1

class TestGitAnalyzer:
    @pytest.fixture
    def git(self): return GitAnalyzer(".")

    def test_returns_list(self, git):
        commits = git.get_recent_commits(days=30, max_commits=5)
        assert isinstance(commits, list)

    def test_commit_structure(self, git):
        commits = git.get_recent_commits(days=30, max_commits=5)
        for c in commits:
            assert "sha" in c
            assert "message" in c
            assert "author" in c

    def test_invalid_repo_returns_empty(self):
        git = GitAnalyzer("C:/nonexistent_path_xyz")
        commits = git.get_recent_commits()
        assert commits == []
