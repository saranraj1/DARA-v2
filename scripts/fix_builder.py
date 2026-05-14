import pathlib
import textwrap

content = textwrap.dedent('''
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from config.settings import get_settings
from context.ast_chunker import ASTChunker, CodeChunk
from context.git_analyzer import GitAnalyzer
from context.retriever import ContextRetriever, count_tokens

logger = logging.getLogger(__name__)


@dataclass
class ContextBundle:
    error_id: str
    erroring_file: str | None
    erroring_function: str | None
    erroring_code: str | None
    related_functions: list[dict] = field(default_factory=list)
    recent_commits: list[dict] = field(default_factory=list)
    similar_past_bugs: list[dict] = field(default_factory=list)
    callers: list[dict] = field(default_factory=list)
    blame_info: dict | None = None
    total_tokens: int = 0
    trace_id: str | None = None

    def summary(self) -> str:
        return (
            f"ContextBundle[id={self.error_id[:8]}, file={self.erroring_file}, "
            f"chunks={len(self.related_functions)}, commits={len(self.recent_commits)}, "
            f"tokens={self.total_tokens}]"
        )


class ContextBuilder:
    def __init__(self, retriever: ContextRetriever, repo_path: str):
        self._retriever = retriever
        self._chunker = ASTChunker()
        self._git = GitAnalyzer(repo_path)
        self._settings = get_settings()

    async def build(self, error: dict) -> ContextBundle:
        eid = error.get("id", "")
        fp = error.get("file_path")
        ln = error.get("line_number")
        svc = error.get("service")
        ec = error.get("error_class", "")
        msg = error.get("message", "")
        budget = self._settings.max_context_tokens
        used = 0
        b = ContextBundle(error_id=eid, erroring_file=fp, erroring_function=None,
                          erroring_code=None, trace_id=error.get("trace_id"))
        if fp and Path(fp).exists():
            chunks = self._chunker.chunk_file(fp, service=svc)
            ec_chunk = self._find_chunk(chunks, ln)
            if ec_chunk:
                b.erroring_function = ec_chunk.display_name
                b.erroring_code = ec_chunk.content
                used += count_tokens(ec_chunk.content)
        query = f"{ec}: {msg}"
        code_budget = min(budget - used, 3000)
        if code_budget > 200:
            related = await self._retriever.retrieve_relevant_code(
                query=query, error_file=fp, service=svc, token_budget=code_budget, limit=8)
            b.related_functions = related
            used += sum(count_tokens(c.get("content", "")) for c in related)
        bug_budget = min(budget - used, 1500)
        if bug_budget > 200:
            b.similar_past_bugs = await self._retriever.retrieve_similar_errors(
                error_summary=query, error_class=ec, limit=5)
        if (budget - used) > 100:
            b.recent_commits = self._git.get_recent_commits(
                file_path=fp, days=self._settings.git_commit_history_days, max_commits=5)
        if fp and ln:
            b.blame_info = self._git.get_blame_info(fp, ln)
        b.total_tokens = used
        logger.info(b.summary())
        return b

    def _find_chunk(self, chunks: list[CodeChunk], ln: int | None) -> CodeChunk | None:
        if not chunks:
            return None
        if ln is None:
            return chunks[0]
        for c in chunks:
            if c.line_start <= ln <= c.line_end:
                return c
        return min(chunks, key=lambda c: abs(c.line_start - ln))
''').lstrip()

pathlib.Path(r'context\builder.py').write_text(content, encoding='utf-8')
print('builder.py written OK')
