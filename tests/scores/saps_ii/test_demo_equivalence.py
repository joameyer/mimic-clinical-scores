from __future__ import annotations

import os
import importlib.util
from pathlib import Path

import pytest


@pytest.mark.demo
def test_official_demo_reference_equivalence(tmp_path, project_root) -> None:
    root_value = os.environ.get("MIMIC_DEMO_ROOT")
    if not root_value:
        pytest.skip("MIMIC_DEMO_ROOT is not configured")
    demo_root = Path(root_value).resolve()
    if not any("demo" in part.lower() for part in demo_root.parts):
        pytest.fail("MIMIC_DEMO_ROOT must visibly identify a demo directory")
    script = project_root / "scripts" / "compare_demo.py"
    spec = importlib.util.spec_from_file_location("compare_demo", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.run(demo_root, project_root, tmp_path / "demo_run")
    assert result["equivalent"] is True
