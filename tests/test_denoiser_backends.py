"""Unit tests for the luma_denoise.denoisers backend package."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Import 'denoisers' as a top-level package; luma_denoise/__init__.py pulls in
# AYON modules that are not installed locally, so we must not go through it.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client" / "luma_denoise"))

from denoisers import base  # noqa: E402


def make_instance(**data):
    """Minimal stand-in for a pyblish instance (only .data is used)."""
    defaults = {
        "files": ["/renders/shot/main/shot_main.1001.exr"],
        "frameStartHandle": 1001,
        "frameEndHandle": 1100,
    }
    defaults.update(data)
    return SimpleNamespace(data=defaults)


def test_quote_adds_quotes_only_when_needed():
    assert base.quote("/plain/path.py") == "/plain/path.py"
    assert base.quote("/has space/p.py") == '"/has space/p.py"'
    assert base.quote('"/already quoted/p.py"') == '"/already quoted/p.py"'


def test_base_get_executable_uses_python_executable_setting():
    backend = base.DenoiserBackend()
    assert backend.get_executable({"python_executable": "/py/bin/python3"}) == "/py/bin/python3"
    assert backend.get_executable({}) == "python"


def test_base_resolve_wrapper_path_substitutes_version():
    backend = base.DenoiserBackend()
    backend.name = "renderman"
    settings = {"renderman": {
        "wrapper_script_path": "L:/scripts/{version}/renderman_denoise.py"}}
    resolved = backend._resolve_wrapper_path(settings)
    assert "{version}" not in resolved
    assert resolved.endswith("/renderman_denoise.py")


def test_base_resolve_wrapper_path_empty_raises_actionable_error():
    backend = base.DenoiserBackend()
    backend.name = "renderman"
    with pytest.raises(RuntimeError, match="wrapper_script_path"):
        backend._resolve_wrapper_path({"renderman": {"wrapper_script_path": ""}})
