"""
ObligationDiffService — obligation-level structural + LLM semantic diff (REQ-017)

Provides structural and semantic diffing for compliance obligations.
- Structural diff: compares obligation fields (rule_text, thresholds, etc.)
- Semantic diff: uses LLM to compare meaning and detect substantive changes

Used by sync API and compliance rule validators.
"""
from typing import Dict, Any, List, Tuple
import difflib

try:
    from anthropic import Anthropic
    from src.utils.config import settings
    _HAS_LLM = True
except ImportError:
    _HAS_LLM = False

class ObligationDiffService:
    def __init__(self):
        self.llm_enabled = _HAS_LLM and not getattr(settings, "LLM_MOCK_MODE", False)
        if self.llm_enabled:
            self.llm = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def structural_diff(self, old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
        """Return field-level diffs between two obligation dicts."""
        diff = {}
        for k in set(old) | set(new):
            if old.get(k) != new.get(k):
                diff[k] = {"old": old.get(k), "new": new.get(k)}
        return diff

    def semantic_diff(self, old_text: str, new_text: str) -> Dict[str, Any]:
        """Return semantic diff using LLM if enabled, else fallback to difflib."""
        if self.llm_enabled:
            prompt = f"Compare the following compliance obligations. Summarize substantive changes.\n\nOLD:\n{old_text}\n\nNEW:\n{new_text}\n\nList all meaningful differences."
            resp = self.llm.messages.create(
                model=settings.P1_OBLIGATION_EXTRACTION_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            return {"semantic_diff": resp.content[0].text.strip()}
        else:
            # Fallback: show unified diff
            diff_lines = list(difflib.unified_diff(
                old_text.splitlines(), new_text.splitlines(), lineterm=""
            ))
            return {"semantic_diff": "\n".join(diff_lines)}

    def diff_obligations(self, old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
        """Return both structural and semantic diffs."""
        struct = self.structural_diff(old, new)
        sem = self.semantic_diff(old.get("rule_text", ""), new.get("rule_text", ""))
        return {"structural": struct, "semantic": sem}
