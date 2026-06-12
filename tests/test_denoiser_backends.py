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


from denoisers.renderman import RendermanDenoiser  # noqa: E402


RM_SETTINGS = {
    "python_executable": "/usr/bin/python3",
    "renderman": {
        "rmantree_path": "/opt/pixar/RenderManProServer-26.3",
        "denoise_exe": "denoise_batch",
        "pixar_license": "9010@192.168.35.28",
        "tiled_denoise_threshold": 2048,
        "wrapper_script_path": "L:/scripts/{version}/renderman_denoise.py",
    },
}


def _rm_backend(monkeypatch, large_image=False):
    backend = RendermanDenoiser()
    monkeypatch.setattr(
        backend, "detect_large_image", lambda instance, rm_settings: large_image)
    return backend


def test_renderman_name_and_combine_flag():
    backend = RendermanDenoiser()
    assert backend.name == "renderman"
    assert backend.requires_combine is True
    assert backend.wrapper_filename == "renderman_denoise.py"


def test_renderman_arguments_basic(monkeypatch):
    backend = _rm_backend(monkeypatch)
    instance = make_instance()
    args = backend.get_arguments(instance, RM_SETTINGS)
    assert args.startswith("L:/scripts/")
    assert "--denoise-exe /opt/pixar/RenderManProServer-26.3/bin/denoise_batch" in args
    assert "--input /renders/shot/main/shot_main.1001.exr" in args
    assert "--output-dir /renders/shot/main/denoised" in args
    assert "--frame-start 1001" in args
    assert "--frame-end 1100" in args
    # 100 frames >= 8 -> cross-frame on; not a large image -> no tiles
    assert "--cross-frame" in args
    assert "--tiles" not in args


def test_renderman_arguments_short_range_no_cross_frame(monkeypatch):
    backend = _rm_backend(monkeypatch)
    instance = make_instance(frameStartHandle=1001, frameEndHandle=1004)
    args = backend.get_arguments(instance, RM_SETTINGS)
    assert "--cross-frame" not in args


def test_renderman_arguments_custom_frames_drive_cross_frame(monkeypatch):
    backend = _rm_backend(monkeypatch)
    # Range says 1 frame, custom frames say 10 -> cross-frame on.
    instance = make_instance(
        frameStartHandle=1001, frameEndHandle=1001,
        publish_attributes={"CollectJobInfo": {
            "use_custom_frames": "custom_only",
            "frames": "1001-1010",
        }})
    args = backend.get_arguments(instance, RM_SETTINGS)
    assert "--cross-frame" in args


def test_renderman_arguments_large_image_enables_tiles(monkeypatch):
    backend = _rm_backend(monkeypatch, large_image=True)
    args = backend.get_arguments(make_instance(), RM_SETTINGS)
    assert "--tiles 2 2" in args


def test_renderman_environment():
    backend = RendermanDenoiser()
    env = backend.get_environment(RM_SETTINGS)
    assert env["RMANTREE"] == "/opt/pixar/RenderManProServer-26.3"
    assert env["PIXAR_LICENSE_FILE"] == "9010@192.168.35.28"
    assert env["PATH"] == "/opt/pixar/RenderManProServer-26.3/bin"


def test_renderman_validate_requires_wrapper_path():
    backend = RendermanDenoiser()
    bad = {"renderman": {"wrapper_script_path": ""}}
    with pytest.raises(RuntimeError, match="renderman.wrapper_script_path"):
        backend.validate(make_instance(), bad)


def test_count_custom_frames():
    count = RendermanDenoiser._count_custom_frames
    assert count("1001,1003-1006,1010") == 6
    assert count("1001") == 1
    assert count("") == 1


from denoisers.oidn import OidnDenoiser  # noqa: E402


OIDN_SETTINGS = {
    "python_executable": "/usr/bin/python3",
    "oiio_root_path": "/opt/oiio",
    "oiio_exe": "oiiotool",
    "oidn": {
        "oidn_root_path": "/opt/oidn",
        "denoise_exe": "oidnDenoise",
        "wrapper_script_path": "L:/scripts/{version}/oidn_denoise.py",
        "beauty_channel": "beauty",
        "albedo_channel": "albedo",
        "normal_channel": "N",
    },
}


def test_oidn_name_and_combine_flag():
    backend = OidnDenoiser()
    assert backend.name == "oidn"
    assert backend.requires_combine is True
    assert backend.wrapper_filename == "oidn_denoise.py"


def test_oidn_arguments():
    backend = OidnDenoiser()
    args = backend.get_arguments(make_instance(), OIDN_SETTINGS)
    assert args.startswith("L:/scripts/")
    assert "--oidn-exe /opt/oidn/bin/oidnDenoise" in args
    assert "--oiiotool /opt/oiio/bin/oiiotool" in args
    assert "--input /renders/shot/main/shot_main.1001.exr" in args
    assert "--output-dir /renders/shot/main/denoised" in args
    assert "--frame-start 1001" in args
    assert "--frame-end 1100" in args
    assert "--beauty-channel beauty" in args
    assert "--albedo-channel albedo" in args
    assert "--normal-channel N" in args


def test_oidn_environment_prepends_bin():
    backend = OidnDenoiser()
    env = backend.get_environment(OIDN_SETTINGS)
    assert env == {"PATH": "/opt/oidn/bin"}


def test_oidn_validate_requires_wrapper_path():
    backend = OidnDenoiser()
    bad = {"oidn": dict(OIDN_SETTINGS["oidn"], wrapper_script_path="")}
    with pytest.raises(RuntimeError, match="oidn.wrapper_script_path"):
        backend.validate(make_instance(), bad)


def test_oidn_validate_requires_guide_channels():
    backend = OidnDenoiser()
    bad = {"oidn": dict(OIDN_SETTINGS["oidn"], albedo_channel="")}
    with pytest.raises(RuntimeError, match="albedo_channel"):
        backend.validate(make_instance(), bad)
