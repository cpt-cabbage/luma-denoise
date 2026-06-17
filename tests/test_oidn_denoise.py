"""Unit tests for the oidn_denoise farm wrapper."""

import json
import sys
from pathlib import Path

import pytest

# Make the wrapper importable regardless of how pytest is invoked.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client" / "luma_denoise" / "scripts"))

import oidn_denoise  # noqa: E402


CHANNELS = [
    "beauty.r", "beauty.g", "beauty.b",
    "albedo.r", "albedo.g", "albedo.b",
    "N.x", "N.y", "N.z",
    "a.Z", "depth.Z",
    "CryptoObject00.r", "CryptoObject00.g", "CryptoObject00.b",
]


def _argv(tmp_path):
    argv = [
        "--oidn-exe", "/opt/oidn/bin/oidnDenoise",
        "--oiiotool", "/opt/oiio/bin/oiiotool",
        "--input", str(tmp_path / "shot_main.1001.exr"),
        "--output-dir", str(tmp_path / "denoised"),
        "--frame-start", "1001",
        "--frame-end", "1002",
        "--beauty-channel", "beauty",
        "--albedo-channel", "albedo",
        "--normal-channel", "N",
        "--addon-version", "0.2.0",
    ]
    return argv


def test_parse_args():
    args = oidn_denoise.parse_args(_argv(Path("/renders")))
    assert args.oidn_exe == "/opt/oidn/bin/oidnDenoise"
    assert args.beauty_channel == "beauty"
    assert args.albedo_channel == "albedo"
    assert args.normal_channel == "N"
    assert args.frame_start == 1001
    assert args.frame_end == 1002
    assert args.keep_temps is False


def test_frame_path_substitutes_frame_number():
    assert oidn_denoise.frame_path("/r/shot.1001.exr", 1005) == "/r/shot.1005.exr"
    assert oidn_denoise.frame_path("/r/shot.0099.exr", 100) == "/r/shot.0100.exr"


def test_frame_path_without_frame_digits_raises():
    with pytest.raises(RuntimeError, match="frame digits"):
        oidn_denoise.frame_path("/r/shot.exr", 1001)


def test_layer_channels_groups_by_prefix():
    assert oidn_denoise.layer_channels(CHANNELS, "beauty") == [
        "beauty.r", "beauty.g", "beauty.b"]
    assert oidn_denoise.layer_channels(CHANNELS, "N") == ["N.x", "N.y", "N.z"]
    assert oidn_denoise.layer_channels(CHANNELS, "missing") == []


def test_require_layer_missing_raises_actionable_error():
    with pytest.raises(RuntimeError) as excinfo:
        oidn_denoise.require_layer(CHANNELS, "diffuse", "albedo guide",
                                   "oidn.albedo_channel")
    message = str(excinfo.value)
    assert "diffuse" in message
    assert "oidn.albedo_channel" in message


def test_build_frame_commands_sequence(tmp_path):
    args = oidn_denoise.parse_args(_argv(tmp_path))
    in_path = str(tmp_path / "shot_main.1001.exr")
    out_path = str(tmp_path / "denoised" / "shot_main.1001.exr")
    beauty, cmds = oidn_denoise.build_frame_commands(
        args, in_path, out_path, CHANNELS, str(tmp_path / "tmp"))

    assert beauty == ["beauty.r", "beauty.g", "beauty.b"]
    assert len(cmds) == 5
    # 3 extractions with the right channel groups
    assert cmds[0][0] == "/opt/oiio/bin/oiiotool"
    assert "beauty.r,beauty.g,beauty.b" in cmds[0]
    assert "albedo.r,albedo.g,albedo.b" in cmds[1]
    assert "N.x,N.y,N.z" in cmds[2]
    # oidnDenoise with guides
    assert cmds[3][0] == "/opt/oidn/bin/oidnDenoise"
    assert "--hdr" in cmds[3] and "--alb" in cmds[3] and "--nrm" in cmds[3]
    # reassembly selects 3 channels, then renames back to the original beauty names
    assert cmds[4][0] == "/opt/oiio/bin/oiiotool"
    assert "R,G,B" in cmds[4]
    assert "--chnames" in cmds[4]
    assert "beauty.r,beauty.g,beauty.b" in cmds[4]
    assert cmds[4][-1] == out_path


def test_build_frame_commands_missing_guide_raises(tmp_path):
    args = oidn_denoise.parse_args(_argv(tmp_path))
    no_albedo = [ch for ch in CHANNELS if not ch.startswith("albedo.")]
    with pytest.raises(RuntimeError, match="oidn.albedo_channel"):
        oidn_denoise.build_frame_commands(
            args, "/r/in.exr", "/r/out.exr", no_albedo, "/tmp/x")


def test_build_manifest_contents(tmp_path):
    args = oidn_denoise.parse_args(_argv(tmp_path))
    manifest = oidn_denoise.build_manifest(
        args, ["beauty.r", "beauty.g", "beauty.b"])
    assert manifest["denoiser"] == "oidn"
    assert manifest["addon_version"] == "0.2.0"
    assert manifest["beauty_channel_map"] == {
        "beauty.r": "R", "beauty.g": "G", "beauty.b": "B", "a.Z": "A"}
    assert manifest["guide_channels"] == {"albedo": "albedo", "normal": "N"}
    assert manifest["frames"] == [1001, 1002]
    assert "####" in manifest["source_pattern"]
    assert "####" in manifest["output_pattern"]


def test_main_runs_all_frames_and_writes_manifest(tmp_path, monkeypatch):
    calls = []

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        oidn_denoise, "read_channels", lambda path, oiiotool: list(CHANNELS))
    monkeypatch.setattr(
        oidn_denoise.subprocess, "run",
        lambda argv, **kw: calls.append(argv) or FakeResult())

    rc = oidn_denoise.main(_argv(tmp_path))
    assert rc == 0
    # 2 frames x 5 commands
    assert len(calls) == 10
    sidecar = tmp_path / "denoised" / "shot_main.denoise.json"
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text())["denoiser"] == "oidn"


def test_main_command_failure_aborts_with_exit_code(tmp_path, monkeypatch):
    class FakeResult:
        returncode = 9
        stdout = ""
        stderr = "oidn error"

    monkeypatch.setattr(
        oidn_denoise, "read_channels", lambda path, oiiotool: list(CHANNELS))
    monkeypatch.setattr(
        oidn_denoise.subprocess, "run", lambda argv, **kw: FakeResult())

    rc = oidn_denoise.main(_argv(tmp_path))
    assert rc == 9
    assert not (tmp_path / "denoised" / "shot_main.denoise.json").exists()


def test_main_missing_guide_channel_fails_loudly(tmp_path, monkeypatch, capsys):
    no_normal = [ch for ch in CHANNELS if not ch.startswith("N.")]
    monkeypatch.setattr(
        oidn_denoise, "read_channels", lambda path, oiiotool: no_normal)

    rc = oidn_denoise.main(_argv(tmp_path))
    assert rc == 1
    captured = capsys.readouterr()
    assert "oidn.normal_channel" in captured.err


def test_main_inverted_frame_range_fails(tmp_path, capsys):
    argv = _argv(tmp_path)
    argv[argv.index("--frame-start") + 1] = "1010"
    argv[argv.index("--frame-end") + 1] = "1001"
    rc = oidn_denoise.main(argv)
    assert rc == 1
    assert "frame_start" in capsys.readouterr().err


def test_build_manifest_rename_pairs_override_derivation(tmp_path):
    argv = _argv(tmp_path) + ["--rename", "beauty.r=red", "--rename", "a.Z=alpha"]
    args = oidn_denoise.parse_args(argv)
    manifest = oidn_denoise.build_manifest(
        args, ["beauty.r", "beauty.g", "beauty.b"])
    assert manifest["beauty_channel_map"] == {"beauty.r": "red", "a.Z": "alpha"}


def test_main_malformed_rename_fails_before_work(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        oidn_denoise.subprocess, "run", lambda *a, **k: calls.append(a))
    rc = oidn_denoise.main(_argv(tmp_path) + ["--rename", "bad"])
    assert rc == 1
    assert calls == []
    assert "rename" in capsys.readouterr().err


