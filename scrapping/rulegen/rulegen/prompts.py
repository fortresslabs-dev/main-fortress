"""Load prompts from YAML so they can be versioned and A/B tested.

Hardcoding a prompt in Python means every change is a code change and no
change is measurable. Here a prompt is config: copy v1 to v2, edit, run
both against the golden set, keep the winner.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class PromptConfig:
    version: str
    name: str
    model: str
    temperature: float
    max_tokens: int
    units_per_call: int
    system: str
    user_template: str
    few_shot: list[dict]
    evidence_vocabulary: list[str]
    path: Optional[Path] = None

    # ── validation ────────────────────────────────────────────────────
    def validate(self) -> list[str]:
        """Problems that would silently degrade extraction."""
        issues = []
        if self.units_per_call != 1:
            issues.append(
                f"units_per_call={self.units_per_call}: batching units makes "
                f"the model merge adjacent duties into one rule")
        if self.temperature != 0:
            issues.append(
                f"temperature={self.temperature}: this is a verification "
                f"task, variance is a defect")
        names = [f.get("name") for f in self.few_shot]
        if "noun_phrase_duty" not in names:
            issues.append(
                "no noun_phrase_duty example: recall on sections like "
                "1020.210 will collapse to the regex result")
        if "agency_actor" not in names:
            issues.append(
                "no agency_actor example: the model will write rules for "
                "duties the regulator performs")
        for f in self.few_shot:
            ev = (f.get("assistant") or {}).get("evidence_source", "")
            if ev and ev not in self.evidence_vocabulary:
                issues.append(
                    f"few-shot {f.get('name')!r} uses evidence {ev!r} which is "
                    f"not in the vocabulary — teaches the model to go outside it")
        return issues

    # ── message construction ──────────────────────────────────────────
    def system_prompt(self) -> str:
        return self.system.replace("{vocabulary}",
                                   ", ".join(self.evidence_vocabulary))

    def _render_user(self, stem: str, parent: str, marker: str,
                     text: str) -> str:
        return (self.user_template
                .replace("{stem}", stem).replace("{parent}", parent)
                .replace("{marker}", marker).replace("{text}", text))

    def messages(self, unit, stem: str) -> list[dict]:
        msgs: list[dict] = []
        for shot in self.few_shot:
            u = shot["user"]
            msgs.append({"role": "user", "content": self._render_user(
                u.get("stem", ""), u.get("parent", ""),
                u.get("marker", ""), u.get("text", ""))})
            msgs.append({"role": "assistant",
                         "content": json.dumps(shot["assistant"])})
        msgs.append({"role": "user", "content": self._render_user(
            stem, unit.parent, unit.marker, unit.text)})
        return msgs


def load(path: str | Path) -> PromptConfig:
    path = Path(path)
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    cfg = PromptConfig(
        version=raw["version"], name=raw["name"], model=raw["model"],
        temperature=float(raw.get("temperature", 0)),
        max_tokens=int(raw.get("max_tokens", 900)),
        units_per_call=int(raw.get("units_per_call", 1)),
        system=raw["system"], user_template=raw["user_template"],
        few_shot=raw.get("few_shot", []),
        evidence_vocabulary=raw.get("evidence_vocabulary", []),
        path=path)
    return cfg


def classifier(cfg: PromptConfig, api_key: Optional[str] = None):
    """Build a callable(unit, stem) -> dict from a config."""
    import os
    import re
    import requests
    key = api_key or os.environ["ANTHROPIC_API_KEY"]

    def call(unit, stem: str) -> dict:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=90,
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"},
            json={"model": cfg.model, "max_tokens": cfg.max_tokens,
                  "temperature": cfg.temperature,
                  "system": cfg.system_prompt(),
                  "messages": cfg.messages(unit, stem)})
        r.raise_for_status()
        body = "".join(b.get("text", "") for b in r.json()["content"]
                       if b["type"] == "text")
        return json.loads(re.sub(r"^```(?:json)?|```$", "", body.strip(),
                                 flags=re.M).strip())
    return call
