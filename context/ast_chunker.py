"""DARA - Polyglot AST chunker using tree-sitter 0.25

Supported languages (with full AST chunking):
  python     — tree-sitter-python (always available per pyproject.toml)
  go         — tree-sitter-go     (optional, graceful fallback)
  typescript — tree-sitter-typescript (optional, graceful fallback)
  javascript — tree-sitter-javascript (optional, graceful fallback)

Unsupported languages use a sliding-window line chunker (60-line windows).
All language libs are loaded once at construction and never crash the pipeline.
"""
from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
MIN_LINES = 3
MAX_CHUNK_LINES = 300

# Extension → canonical language name
_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".java": "java",
    ".rb": "ruby",
    ".rs": "rust",
}


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
    """
    Polyglot AST-based code chunker.

    Each language has a dedicated parser stored in self._parsers dict.
    Languages whose tree-sitter library is not installed fall back to the
    line-window _fallback() chunker — the pipeline never crashes.
    """

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}
        self._build_parsers()

    # ──────────────────────────────────────────────────────────────────────────
    # Parser initialisation
    # ──────────────────────────────────────────────────────────────────────────

    def _build_parsers(self) -> None:
        """Attempt to load each language parser. Failures are non-fatal."""
        self._parsers["python"] = self._load_python()
        self._parsers["go"] = self._load_go()
        self._parsers["typescript"] = self._load_typescript()
        self._parsers["javascript"] = self._load_javascript()
        ready = [lang for lang, p in self._parsers.items() if p is not None]
        logger.info("ASTChunker: parsers ready: %s", ", ".join(ready) or "none (fallback only)")

    def _load_python(self) -> Any | None:
        try:
            import tree_sitter_python as tsp
            from tree_sitter import Language, Parser
            return Parser(Language(tsp.language()))
        except Exception as exc:
            logger.warning("ASTChunker: python parser unavailable: %s", exc)
            return None

    def _load_go(self) -> Any | None:
        try:
            import tree_sitter_go as tsg
            from tree_sitter import Language, Parser
            return Parser(Language(tsg.language()))
        except Exception as exc:
            logger.debug("ASTChunker: go parser unavailable (optional): %s", exc)
            return None

    def _load_typescript(self) -> Any | None:
        try:
            import tree_sitter_typescript as tsts
            from tree_sitter import Language, Parser
            # tree-sitter-typescript exposes both tsx and typescript variants
            lang = tsts.language_typescript() if hasattr(tsts, "language_typescript") else tsts.language()
            return Parser(Language(lang))
        except Exception as exc:
            logger.debug("ASTChunker: typescript parser unavailable (optional): %s", exc)
            return None

    def _load_javascript(self) -> Any | None:
        try:
            import tree_sitter_javascript as tsjs
            from tree_sitter import Language, Parser
            return Parser(Language(tsjs.language()))
        except Exception as exc:
            logger.debug("ASTChunker: javascript parser unavailable (optional): %s", exc)
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    def chunk_file(
        self,
        file_path: str,
        source: str | None = None,
        service: str | None = None,
    ) -> list[CodeChunk]:
        if source is None:
            try:
                source = Path(file_path).read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.warning("ASTChunker: cannot read %s: %s", file_path, exc)
                return []
        if not source.strip():
            return []

        lang = self._lang(file_path)
        parser = self._parsers.get(lang)

        if parser is None:
            return self._fallback(file_path, source, lang, service)

        try:
            dispatch = {
                "python":     self._ast_chunk_python,
                "go":         self._ast_chunk_go,
                "typescript": self._ast_chunk_typescript,
                "javascript": self._ast_chunk_typescript,  # same node types
            }
            fn = dispatch.get(lang)
            if fn is None:
                return self._fallback(file_path, source, lang, service)
            chunks = fn(file_path, source, parser, service)
            return chunks or self._file_chunk(file_path, source, lang, service)
        except Exception as exc:
            logger.error("ASTChunker: AST error %s: %s", file_path, exc)
            return self._fallback(file_path, source, lang, service)

    # ──────────────────────────────────────────────────────────────────────────
    # Python AST walker (original logic, unchanged)
    # ──────────────────────────────────────────────────────────────────────────

    def _ast_chunk_python(
        self, fp: str, source: str, parser: Any, service: str | None
    ) -> list[CodeChunk]:
        sb = source.encode()
        tree = parser.parse(sb)
        lines = source.splitlines()
        chunks: list[CodeChunk] = []
        self._walk_python(tree.root_node, lines, sb, fp, service, chunks, None)
        return chunks

    def _walk_python(self, node: Any, lines: list, sb: bytes, fp: str,
                     svc: str | None, chunks: list, cls: str | None) -> None:
        if node.type == "class_definition":
            name = self._node_name(node, sb)
            for c in node.children:
                self._walk_python(c, lines, sb, fp, svc, chunks, name)
            return
        if node.type == "function_definition":
            fn = self._node_name(node, sb)
            s, e = node.start_point[0], node.end_point[0]
            if (e - s + 1) >= MIN_LINES:
                content = textwrap.dedent("\n".join(lines[s:e + 1]))
                if (e - s + 1) > MAX_CHUNK_LINES:
                    chunks.extend(self._split(content, fn, cls, fp, svc, s, lang="python"))
                else:
                    chunks.append(CodeChunk(
                        chunk_id=f"{fp}::{cls or ''}::{fn}::{s}", file_path=fp,
                        language="python", content=content, function_name=fn,
                        class_name=cls, line_start=s + 1, line_end=e + 1, service=svc,
                    ))
            # Still recurse into function body (nested defs, decorated async defs)
            for c in node.children:
                self._walk_python(c, lines, sb, fp, svc, chunks, cls)
            return
        for c in node.children:
            self._walk_python(c, lines, sb, fp, svc, chunks, cls)

    # ──────────────────────────────────────────────────────────────────────────
    # Go AST walker
    # ──────────────────────────────────────────────────────────────────────────

    def _ast_chunk_go(
        self, fp: str, source: str, parser: Any, service: str | None
    ) -> list[CodeChunk]:
        """
        Walk Go AST for function_declaration and method_declaration nodes.
        Go methods carry a receiver type which we treat as the "class_name".
        """
        sb = source.encode()
        tree = parser.parse(sb)
        lines = source.splitlines()
        chunks: list[CodeChunk] = []
        self._walk_go(tree.root_node, lines, sb, fp, service, chunks)
        return chunks

    def _walk_go(self, node: Any, lines: list, sb: bytes, fp: str,
                 svc: str | None, chunks: list) -> None:
        if node.type == "function_declaration":
            fn = self._node_name(node, sb)
            s, e = node.start_point[0], node.end_point[0]
            if (e - s + 1) >= MIN_LINES:
                content = "\n".join(lines[s:e + 1])
                chunks.append(CodeChunk(
                    chunk_id=f"{fp}:::::{fn}::{s}", file_path=fp,
                    language="go", content=content, function_name=fn,
                    class_name=None, line_start=s + 1, line_end=e + 1, service=svc,
                ))
        elif node.type == "method_declaration":
            # receiver → class name; function name from the function field
            receiver = self._go_receiver_type(node, sb)
            fn = self._node_name(node, sb)
            s, e = node.start_point[0], node.end_point[0]
            if (e - s + 1) >= MIN_LINES:
                content = "\n".join(lines[s:e + 1])
                chunks.append(CodeChunk(
                    chunk_id=f"{fp}::{receiver}::{fn}::{s}", file_path=fp,
                    language="go", content=content, function_name=fn,
                    class_name=receiver, line_start=s + 1, line_end=e + 1, service=svc,
                ))
        for c in node.children:
            self._walk_go(c, lines, sb, fp, svc, chunks)

    def _go_receiver_type(self, node: Any, sb: bytes) -> str | None:
        """Extract receiver type from a Go method_declaration node."""
        for child in node.children:
            if child.type == "parameter_list":
                for param in child.children:
                    if hasattr(param, "type") and "type" in param.type:
                        return sb[param.start_byte:param.end_byte].decode().strip("*()")
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # TypeScript / JavaScript AST walker
    # ──────────────────────────────────────────────────────────────────────────

    def _ast_chunk_typescript(
        self, fp: str, source: str, parser: Any, service: str | None
    ) -> list[CodeChunk]:
        """
        Walk TypeScript/JavaScript AST for:
          - function_declaration
          - method_definition (inside class_body)
          - arrow_function assigned to a const/let/var (lexical_declaration)
        """
        sb = source.encode()
        tree = parser.parse(sb)
        lines = source.splitlines()
        lang = "typescript" if fp.endswith((".ts", ".tsx")) else "javascript"
        chunks: list[CodeChunk] = []
        self._walk_ts(tree.root_node, lines, sb, fp, lang, service, chunks, None)
        return chunks

    def _walk_ts(self, node: Any, lines: list, sb: bytes, fp: str,
                 lang: str, svc: str | None, chunks: list, cls: str | None) -> None:
        if node.type == "class_declaration":
            name = self._node_name(node, sb)
            for c in node.children:
                self._walk_ts(c, lines, sb, fp, lang, svc, chunks, name)
            return
        if node.type in ("function_declaration", "function_expression"):
            fn = self._node_name(node, sb) or "<anonymous>"
            self._append_ts_chunk(node, lines, fp, lang, svc, chunks, fn, cls)
        elif node.type == "method_definition":
            fn = self._node_name(node, sb) or "<method>"
            self._append_ts_chunk(node, lines, fp, lang, svc, chunks, fn, cls)
        elif node.type == "lexical_declaration":
            # const myFn = (...) => { ... }
            for declarator in node.children:
                if declarator.type == "variable_declarator":
                    fn = self._node_name(declarator, sb)
                    for child in declarator.children:
                        if child.type in ("arrow_function", "function_expression"):
                            self._append_ts_chunk(child, lines, fp, lang, svc, chunks, fn, cls)
        for c in node.children:
            if node.type not in ("class_declaration", "function_declaration",
                                 "function_expression", "method_definition",
                                 "lexical_declaration"):
                self._walk_ts(c, lines, sb, fp, lang, svc, chunks, cls)

    def _append_ts_chunk(self, node: Any, lines: list, fp: str, lang: str,
                         svc: str | None, chunks: list,
                         fn: str | None, cls: str | None) -> None:
        s, e = node.start_point[0], node.end_point[0]
        if (e - s + 1) < MIN_LINES:
            return
        content = "\n".join(lines[s:e + 1])
        chunks.append(CodeChunk(
            chunk_id=f"{fp}::{cls or ''}::{fn}::{s}", file_path=fp,
            language=lang, content=content, function_name=fn,
            class_name=cls, line_start=s + 1, line_end=e + 1, service=svc,
        ))

    # ──────────────────────────────────────────────────────────────────────────
    # Shared helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _node_name(self, node: Any, sb: bytes) -> str | None:
        for c in node.children:
            if c.type == "identifier" or c.type == "property_identifier":
                return sb[c.start_byte:c.end_byte].decode()
        return None

    def _lang(self, fp: str) -> str:
        return _EXT_LANG.get(Path(fp).suffix.lower(), "unknown")

    def _file_chunk(self, fp: str, source: str, lang: str, svc: str | None) -> list[CodeChunk]:
        return [CodeChunk(
            chunk_id=f"{fp}::__module__::0", file_path=fp,
            language=lang, content=source[:8000],
            function_name=None, class_name=None,
            line_start=1, line_end=len(source.splitlines()), service=svc,
        )]

    def _fallback(self, fp: str, source: str, lang: str, svc: str | None) -> list[CodeChunk]:
        """Sliding-window chunker for unsupported languages."""
        lines, chunks, win, start = source.splitlines(), [], [], 0
        for i, line in enumerate(lines):
            win.append(line)
            if (not line.strip() and len(win) >= 40) or len(win) >= 60:
                chunks.append(CodeChunk(
                    chunk_id=f"{fp}::chunk::{start}", file_path=fp,
                    language=lang, content="\n".join(win), function_name=None, class_name=None,
                    line_start=start + 1, line_end=i + 1, service=svc,
                ))
                start, win = i + 1, []
        if win:
            chunks.append(CodeChunk(
                chunk_id=f"{fp}::chunk::{start}", file_path=fp,
                language=lang, content="\n".join(win), function_name=None, class_name=None,
                line_start=start + 1, line_end=len(lines), service=svc,
            ))
        return chunks or self._file_chunk(fp, source, svc, lang)

    def _split(self, content: str, fn: str | None, cls: str | None,
               fp: str, svc: str | None, base: int,
               lang: str = "python", win: int = 150, overlap: int = 30) -> list[CodeChunk]:
        """Split oversized functions into overlapping window chunks."""
        lines, chunks, step = content.splitlines(), [], win - overlap
        for i, s in enumerate(range(0, len(lines), step)):
            e = min(s + win, len(lines))
            chunks.append(CodeChunk(
                chunk_id=f"{fp}::{cls or ''}::{fn}::part{i}::{base + s}",
                file_path=fp, language=lang, content="\n".join(lines[s:e]),
                function_name=fn, class_name=cls,
                line_start=base + s + 1, line_end=base + e, service=svc,
            ))
            if e == len(lines):
                break
        return chunks
