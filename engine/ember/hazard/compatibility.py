"""Fail visibly when shared contracts evolve; never silently migrate model checkpoints."""

from __future__ import annotations
import ast
from pathlib import Path
from ember.contracts import SCHEMA_VERSION, QUANTILES
from .api import ModelConfig
from .bridge import REPO, MODEL_VERSION, PROTOCOL_VERSION


def check_contracts(repo: Path = REPO):
    specification = (repo / "spec/00_contracts.md").read_text()
    start = specification.index(
        "from dataclasses import", specification.index("## 6. Physics boundary")
    )
    source = specification[start : specification.index("```", start)]

    def classes(text):
        return {
            node.name: ast.dump(node, include_attributes=False)
            for node in ast.parse(text).body
            if isinstance(node, ast.ClassDef)
        }

    expected = classes(source)
    actual = classes((repo / "engine/ember/hazard/api.py").read_text())
    if actual != expected:
        raise RuntimeError(
            "HazardModel api.py differs from the shared specification; reconcile the adapter explicitly"
        )
    if SCHEMA_VERSION != 2 or QUANTILES != ModelConfig().quantiles:
        raise RuntimeError(
            "wire schema or quantiles changed; update publisher and frontend tests together"
        )
    return {
        "hazard_api": "matches shared specification",
        "wire_schema": SCHEMA_VERSION,
        "native_protocol": PROTOCOL_VERSION,
        "model_version": MODEL_VERSION,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(check_contracts(), indent=2))
