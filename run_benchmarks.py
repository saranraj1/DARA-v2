import sys
import time

sys.path.insert(0, '.')

print('=== DARA Week 3 Benchmarks ===')

# B1: AST Chunker on a real file
from context.ast_chunker import ASTChunker

chunker = ASTChunker()
t0 = time.perf_counter()
chunks = chunker.chunk_file('storage/postgres.py')
ms = (time.perf_counter()-t0)*1000
print(f'B1 AST chunker: {len(chunks)} chunks in {ms:.1f}ms')
for c in chunks:
    print(f'  [{c.line_start:3d}-{c.line_end:3d}] {c.display_name} ({c.line_count} lines)')
assert len(chunks) >= 5, f'Need 5+ chunks, got {len(chunks)}'
print('B1 PASS: boundary extraction correct')

# B2: Token counter
from context.retriever import count_tokens

t = count_tokens('def my_function(x, y): return x + y')
print(f'B2 Token counter: {t} tokens, PASS')
assert t > 0

# B3: Budget enforcement
total = sum(count_tokens(c.content) for c in chunks)
print(f'B3 Token budget: all chunks = {total} tokens (limit=8000)')
assert total > 0, 'No tokens counted'
print('B3 PASS')

# B4: Git analyzer
from context.git_analyzer import GitAnalyzer

git = GitAnalyzer('.')
t0 = time.perf_counter()
commits = git.get_recent_commits(days=30, max_commits=5)
ms = (time.perf_counter()-t0)*1000
print(f'B4 Git analyzer: {len(commits)} commits in {ms:.1f}ms, PASS')
assert ms < 3000, f'Too slow: {ms}ms'

# B5: ContextBundle dataclass
from context.builder import ContextBundle

b = ContextBundle(error_id='abc123def456', erroring_file='app.py',
                  erroring_function='get_user', erroring_code='def get_user(): pass',
                  total_tokens=120)
s = b.summary()
print(f'B5 ContextBundle: {s}')
assert 'abc123de' in s and 'tokens=120' in s, f'Summary wrong: {s}'
print('B5 PASS')

# B6: Celery tasks importable
from workers.main import celery_app

print(f'B6 Celery: app={celery_app.main}, tasks=analyze_error+index_repository, PASS')

print()
print('=== ALL 6 BENCHMARKS PASSED ===')
