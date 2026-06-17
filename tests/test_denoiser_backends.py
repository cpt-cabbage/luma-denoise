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


def test_base_get_executable_returns_python_executable():
    backend = base.DenoiserBackend()
    assert backend.get_executable(
        {"shared": {"python_executable": "/usr/bin/python3"}}) == "/usr/bin/python3"
    assert backend.get_executable({}) == "python"


def test_base_resolve_wrapper_path_substitutes_version():
    backend = base.DenoiserBackend()
    backend.name = "renderman"
    backend.wrapper_filename = "renderman_denoise.py"
    out = backend._resolve_wrapper_path(
        {"denoise": {"renderman": {"wrapper_script_path": "L:/s/{version}/renderman_denoise.py"}}})
    assert "{version}" not in out and out.endswith("/renderman_denoise.py")


def test_base_resolve_wrapper_path_empty_raises():
    backend = base.DenoiserBackend()
    backend.name = "renderman"
    with pytest.raises(RuntimeError, match="wrapper_script_path"):
        backend._resolve_wrapper_path({"denoise": {"renderman": {"wrapper_script_path": ""}}})


def test_resolve_wrapper_path_joins_dir_and_filename():
    out = base.resolve_wrapper_path(
        {"shared": {"scripts_directory": "L:/s/{version}"}},
        "renderman_denoise.py")
    assert "{version}" not in out
    assert out.endswith("/renderman_denoise.py")


def test_resolve_wrapper_path_strips_trailing_slash():
    out = base.resolve_wrapper_path(
        {"shared": {"scripts_directory": "L:/s/"}}, "oiio_combine.py")
    assert out == "L:/s/oiio_combine.py"
    out_bs = base.resolve_wrapper_path(
        {"shared": {"scripts_directory": "L:/s\\"}}, "oiio_combine.py")
    assert out_bs == "L:/s/oiio_combine.py"


def test_resolve_wrapper_path_empty_raises():
    with pytest.raises(RuntimeError, match="scripts_directory"):
        base.resolve_wrapper_path({"shared": {"scripts_directory": ""}},
                                  "renderman_denoise.py")


def test_resolve_platform_value_dict_and_passthrough():
    assert base.resolve_platform_value(
        {"windows": "a.exe", "linux": "a", "darwin": ""}, "windows") == "a.exe"
    assert base.resolve_platform_value(
        {"windows": "a.exe", "linux": "a", "darwin": ""}, "darwin") == ""
    assert base.resolve_platform_value("plain", "linux") == "plain"
    assert base.resolve_platform_value(None, "linux") == ""


def test_platform_triplet_args_from_dict_omits_empty():
    out = base.DenoiserBackend.platform_triplet_args(
        "rmantree", {"windows": "C:/RMP", "linux": "/opt/rmp", "darwin": ""})
    # Empty darwin value is omitted entirely (no empty token emitted).
    assert out == ["--rmantree-windows", "C:/RMP",
                   "--rmantree-linux", "/opt/rmp"]


def test_platform_triplet_args_from_plain_string():
    out = base.DenoiserBackend.platform_triplet_args("oidn-root", "/opt/oidn")
    assert out == ["--oidn-root-windows", "/opt/oidn",
                   "--oidn-root-linux", "/opt/oidn",
                   "--oidn-root-darwin", "/opt/oidn"]


from denoisers.renderman import RendermanDenoiser  # noqa: E402


RM_SETTINGS = {
    "shared": {
        "python_executable": "/usr/bin/python3",
        "oiio_root_path": {"windows": "C:/oiio", "linux": "/opt/oiio", "darwin": ""},
        "oiio_exe": "oiiotool",
    },
    "denoise": {
        "denoiser": "renderman",
        "renderman": {
            "rmantree_path": {"windows": "C:/Pixar/RMP", "linux": "/opt/pixar/RenderManProServer-26.3", "darwin": ""},
            "denoise_exe": "denoise_batch",
            "pixar_license": "9010@x",
            "tiled_denoise_threshold": 2048,
            "wrapper_script_path": "L:/scripts/{version}/renderman_denoise.py",
            "beauty_rename_map": [
                {"source": "Ci.r", "target": "R"},
                {"source": "a.Z", "target": "A"},
            ],
        },
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
    assert "--rmantree-linux /opt/pixar/RenderManProServer-26.3" in args
    assert "--denoise-exe-name denoise_batch" in args
    assert "--pixar-license 9010@x" in args
    assert "--denoise-exe " not in args
    assert "--input /renders/shot/main/shot_main.1001.exr" in args
    assert "--output-dir /renders/shot/main/denoised" in args
    assert "--frame-start 1001" in args
    assert "--frame-end 1100" in args
    # 100 frames >= 8 -> cross-frame on; not a large image -> no tiles
    assert "--cross-frame" in args
    assert "--tiles" not in args
    assert "--rename Ci.r=R" in args
    assert "--rename a.Z=A" in args


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
    assert env == {}


def test_renderman_validate_requires_wrapper_path():
    backend = RendermanDenoiser()
    bad = {"denoise": {"renderman": {"wrapper_script_path": ""}}}
    with pytest.raises(RuntimeError, match="denoise.renderman.wrapper_script_path"):
        backend.validate(make_instance(), bad)


def test_count_custom_frames():
    count = RendermanDenoiser._count_custom_frames
    assert count("1001,1003-1006,1010") == 6
    assert count("1001") == 1
    assert count("") == 1


from denoisers.oidn import OidnDenoiser  # noqa: E402


OIDN_SETTINGS = {
    "shared": {
        "python_executable": "/usr/bin/python3",
        "oiio_root_path": {"windows": "C:/oiio", "linux": "/opt/oiio", "darwin": ""},
        "oiio_exe": "oiiotool",
    },
    "denoise": {
        "denoiser": "oidn",
        "oidn": {
            "oidn_root_path": {"linux": "/opt/oidn", "windows": "", "darwin": ""},
            "denoise_exe": "oidnDenoise",
            "wrapper_script_path": "L:/scripts/{version}/oidn_denoise.py",
            "beauty_channel": "beauty",
            "albedo_channel": "albedo",
            "normal_channel": "N",
            "beauty_rename_map": [
                {"source": "beauty.r", "target": "R"},
                {"source": "a.Z", "target": "A"},
            ],
        },
    },
}


def _oidn_settings_with(**overrides):
    import copy
    settings = copy.deepcopy(OIDN_SETTINGS)
    settings["denoise"]["oidn"].update(overrides)
    return settings


def test_oidn_name_and_combine_flag():
    backend = OidnDenoiser()
    assert backend.name == "oidn"
    assert backend.requires_combine is True
    assert backend.wrapper_filename == "oidn_denoise.py"


def test_oidn_arguments():
    backend = OidnDenoiser()
    args = backend.get_arguments(make_instance(), OIDN_SETTINGS)
    assert args.startswith("L:/scripts/")
    assert "--oidn-root-linux /opt/oidn" in args
    assert "--oidn-exe-name oidnDenoise" in args
    assert "--oiio-root-linux /opt/oiio" in args
    assert "--oiio-exe-name oiiotool" in args
    assert "--oidn-exe " not in args
    assert "--oiiotool " not in args
    assert "--input /renders/shot/main/shot_main.1001.exr" in args
    assert "--output-dir /renders/shot/main/denoised" in args
    assert "--frame-start 1001" in args
    assert "--frame-end 1100" in args
    assert "--beauty-channel beauty" in args
    assert "--albedo-channel albedo" in args
    assert "--normal-channel N" in args
    assert "--rename beauty.r=R" in args
    assert "--rename a.Z=A" in args


def test_oidn_environment_returns_empty():
    backend = OidnDenoiser()
    env = backend.get_environment(OIDN_SETTINGS)
    assert env == {}


def test_oidn_validate_requires_wrapper_path():
    backend = OidnDenoiser()
    bad = _oidn_settings_with(wrapper_script_path="")
    with pytest.raises(RuntimeError, match="oidn.wrapper_script_path"):
        backend.validate(make_instance(), bad)


def test_oidn_validate_requires_guide_channels():
    backend = OidnDenoiser()
    bad = _oidn_settings_with(albedo_channel="")
    with pytest.raises(RuntimeError, match="albedo_channel"):
        backend.validate(make_instance(), bad)


import denoisers  # noqa: E402


def test_registry_returns_backend_instances():
    assert denoisers.get_denoiser_backend("renderman").name == "renderman"
    assert denoisers.get_denoiser_backend("oidn").name == "oidn"


def test_registry_unknown_name_raises_with_known_list():
    with pytest.raises(RuntimeError, match="oidn"):
        denoisers.get_denoiser_backend("optix")


def test_rename_pair_args_skips_incomplete_pairs():
    backend = RendermanDenoiser()
    settings = {"denoise": {"renderman": {"beauty_rename_map": [
        {"source": "Ci.r", "target": "R"},
        {"source": "", "target": "X"},
        {"source": "Y", "target": ""},
    ]}}}
    assert backend.rename_pair_args(settings) == ["--rename", "Ci.r=R"]
