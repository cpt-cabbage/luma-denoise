"""Unit tests for the renderman_denoise farm wrapper."""

import json
import sys
from pathlib import Path

# Make the wrapper importable regardless of how pytest is invoked.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client" / "luma_denoise" / "scripts"))

import renderman_denoise  # noqa: E402


def _args(**overrides):
    base = [
        "--denoise-exe", "/opt/pixar/bin/denoise_batch",
        "--input", "/renders/shot_main.1001.exr",
        "--output-dir", "/renders/denoised",
        "--frame-start", "1001",
        "--frame-end", "1100",
        "--addon-version", "0.2.0",
    ]
    extra = overrides.pop("extra", [])
    return renderman_denoise.parse_args(base + extra)


def test_parse_args_minimal():
    args = _args()
    assert args.denoise_exe == "/opt/pixar/bin/denoise_batch"
    assert args.input == "/renders/shot_main.1001.exr"
    assert args.output_dir == "/renders/denoised"
    assert args.frame_start == 1001
    assert args.frame_end == 1100
    assert args.cross_frame is False
    assert args.tiles is None
    assert args.verbose is False


def test_build_denoise_argv_basic():
    argv = renderman_denoise.build_denoise_argv(_args())
    assert argv == [
        "/opt/pixar/bin/denoise_batch",
        "-a", "0", "-v", "--clean-alpha", "--progress",
        "-o", "/renders/denoised",
        "/renders/shot_main.1001.exr",
        "1001-1100",
    ]


def test_build_denoise_argv_cross_frame_and_tiles():
    argv = renderman_denoise.build_denoise_argv(
        _args(extra=["--cross-frame", "--tiles", "2", "2"]))
    assert "-cf" in argv
    tiles_i = argv.index("--tiles")
    assert argv[tiles_i:tiles_i + 3] == ["--tiles", "2", "2"]


def test_build_manifest_contents():
    manifest = renderman_denoise.build_manifest(_args())
    assert manifest["denoiser"] == "renderman"
    assert manifest["addon_version"] == "0.2.0"
    assert manifest["source_pattern"] == "/renders/shot_main.####.exr"
    assert manifest["output_pattern"] == "/renders/denoised/shot_main.####.exr"
    assert manifest["beauty_channel_map"] == {
        "Ci.r": "R", "Ci.g": "G", "Ci.b": "B", "a.Z": "A"}
    assert manifest["frames"] == [1001, 1100]
    assert "guide_channels" not in manifest


def test_write_manifest_writes_denoise_sidecar(tmp_path):
    manifest = {"denoiser": "renderman"}
    ok = renderman_denoise.write_manifest(
        str(tmp_path / "shot_main.1001.exr"), manifest)
    assert ok is True
    sidecar = tmp_path / "shot_main.denoise.json"
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text()) == manifest


def test_main_propagates_exit_code_and_writes_manifest(tmp_path, monkeypatch):
    calls = []

    class FakeResult:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return FakeResult()

    monkeypatch.setattr(renderman_denoise.subprocess, "run", fake_run)
    rc = renderman_denoise.main([
        "--denoise-exe", "/opt/pixar/bin/denoise_batch",
        "--input", str(tmp_path / "shot_main.1001.exr"),
        "--output-dir", str(tmp_path / "denoised"),
        "--frame-start", "1001",
        "--frame-end", "1100",
        "--addon-version", "0.2.0",
    ])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == "/opt/pixar/bin/denoise_batch"
    assert (tmp_path / "denoised" / "shot_main.denoise.json").is_file()


def test_main_failure_skips_manifest(tmp_path, monkeypatch):
    class FakeResult:
        returncode = 3
        stdout = ""
        stderr = "license error"

    monkeypatch.setattr(
        renderman_denoise.subprocess, "run", lambda *a, **k: FakeResult())
    rc = renderman_denoise.main([
        "--denoise-exe", "/opt/pixar/bin/denoise_batch",
        "--input", str(tmp_path / "shot_main.1001.exr"),
        "--output-dir", str(tmp_path / "denoised"),
        "--frame-start", "1001",
        "--frame-end", "1100",
    ])
    assert rc == 3
    assert not (tmp_path / "denoised" / "shot_main.denoise.json").exists()


def test_build_manifest_rename_pairs_override_default():
    args = _args(extra=["--rename", "Ci.r=red", "--rename", "a.Z=alpha"])
    manifest = renderman_denoise.build_manifest(args)
    assert manifest["beauty_channel_map"] == {"Ci.r": "red", "a.Z": "alpha"}


def test_main_malformed_rename_fails_before_denoise(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        renderman_denoise.subprocess, "run",
        lambda *a, **k: calls.append(a))
    rc = renderman_denoise.main([
        "--denoise-exe", "/opt/pixar/bin/denoise_batch",
        "--input", str(tmp_path / "shot_main.1001.exr"),
        "--output-dir", str(tmp_path / "denoised"),
        "--frame-start", "1001",
        "--frame-end", "1100",
        "--rename", "no-equals-sign",
    ])
    assert rc == 1
    assert calls == []
