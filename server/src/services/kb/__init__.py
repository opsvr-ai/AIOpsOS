"""Knowledge-base service package.

Holds the wiki compile pipeline and related helpers split out from the
legacy ``kb_summarizer`` / ``kb_monitor`` modules in Phase F of the
Agent Runtime Optimization & Evolution spec.
"""

from src.services.kb.compile_logic import (
    CompileResult,
    compile_wiki_async,
)

__all__ = ["CompileResult", "compile_wiki_async"]
