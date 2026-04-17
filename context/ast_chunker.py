"""DARA - AST chunker using tree-sitter 0.25"""
from __future__ import annotations
import logging, textwrap
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
MIN_LINES = 3
MAX_CHUNK_LINES = 300

@dataclass
class CodeChunk:
    chunk_id: str
    file_path: str
    language: str
    content: str
    function_name: str | None
    class_name: str | None
    line_start: int
    line_end: int
    service: str | None = None
    relevance_score: float = 1.0

    @property
    def display_name(self) -> str:
        if self.class_name and self.function_name:
            return f"{self.class_name}.{self.function_name}"
        return self.function_name or f"<module:{self.line_start}-{self.line_end}>"

    @property
    def line_count(self) -> int:
        return self.line_end - self.line_start + 1

class ASTChunker:
    def __init__(self):
        self._parser = self._build_parser()

    def _build_parser(self):
        try:
            import tree_sitter_python as tsp
            from tree_sitter import Language, Parser
            p = Parser(Language(tsp.language()))
            logger.info("tree-sitter ready")
            return p
        except Exception as e:
            logger.error("tree-sitter failed: %s", e)
            return None

    def chunk_file(self, file_path: str, source: str | None = None, service: str | None = None) -> list[CodeChunk]:
        if source is None:
            try:
                source = Path(file_path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning("Cannot read %s: %s", file_path, e)
                return []
        if not source.strip():
            return []
        lang = self._lang(file_path)
        if lang != "python" or not self._parser:
            return self._fallback(file_path, source, lang, service)
        try:
            chunks = self._ast_chunk(file_path, source, service)
            return chunks or self._file_chunk(file_path, source, service)
        except Exception as e:
            logger.error("AST error %s: %s", file_path, e)
            return self._fallback(file_path, source, lang, service)

    def _ast_chunk(self, fp, source, service):
        sb = source.encode()
        tree = self._parser.parse(sb)
        lines = source.splitlines()
        chunks = []
        self._walk(tree.root_node, lines, sb, fp, service, chunks, None)
        return chunks

    def _walk(self, node, lines, sb, fp, svc, chunks, cls):
        if node.type == "class_definition":
            name = self._name(node, sb)
            for c in node.children:
                self._walk(c, lines, sb, fp, svc, chunks, name)
            return
        if node.type == "function_definition":
            fn = self._name(node, sb)
            s, e = node.start_point[0], node.end_point[0]
            if (e - s + 1) < MIN_LINES:
                return
            content = textwrap.dedent("\n".join(lines[s:e+1]))
            if (e - s + 1) > MAX_CHUNK_LINES:
                chunks.extend(self._split(content, fn, cls, fp, svc, s))
            else:
                chunks.append(CodeChunk(
                    chunk_id=f"{fp}::{cls or ''}::{fn}::{s}", file_path=fp,
                    language="python", content=content, function_name=fn,
                    class_name=cls, line_start=s+1, line_end=e+1, service=svc))
            return
        for c in node.children:
            self._walk(c, lines, sb, fp, svc, chunks, cls)

    def _name(self, node, sb):
        for c in node.children:
            if c.type == "identifier":
                return sb[c.start_byte:c.end_byte].decode()
        return "<anonymous>"

    def _lang(self, fp):
        return {".py":"python",".go":"go",".java":"java"}.get(Path(fp).suffix.lower(),"unknown")

    def _file_chunk(self, fp, source, svc):
        return [CodeChunk(chunk_id=f"{fp}::__module__::0", file_path=fp,
                          language=self._lang(fp), content=source[:8000],
                          function_name=None, class_name=None,
                          line_start=1, line_end=len(source.splitlines()), service=svc)]

    def _fallback(self, fp, source, lang, svc):
        lines, chunks, win, start = source.splitlines(), [], [], 0
        for i, line in enumerate(lines):
            win.append(line)
            if (not line.strip() and len(win)>=40) or len(win)>=60:
                chunks.append(CodeChunk(chunk_id=f"{fp}::chunk::{start}", file_path=fp,
                    language=lang, content="\n".join(win), function_name=None, class_name=None,
                    line_start=start+1, line_end=i+1, service=svc))
                start, win = i+1, []
        if win:
            chunks.append(CodeChunk(chunk_id=f"{fp}::chunk::{start}", file_path=fp,
                language=lang, content="\n".join(win), function_name=None, class_name=None,
                line_start=start+1, line_end=len(lines), service=svc))
        return chunks or self._file_chunk(fp, source, svc)

    def _split(self, content, fn, cls, fp, svc, base, win=150, overlap=30):
        lines, chunks, step = content.splitlines(), [], win-overlap
        for i, s in enumerate(range(0, len(lines), step)):
            e = min(s+win, len(lines))
            chunks.append(CodeChunk(
                chunk_id=f"{fp}::{cls or ''}::{fn}::part{i}::{base+s}",
                file_path=fp, language="python", content="\n".join(lines[s:e]),
                function_name=fn, class_name=cls,
                line_start=base+s+1, line_end=base+e, service=svc))
            if e==len(lines): break
        return chunks
