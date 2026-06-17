"""Unit tests for oiio_combine wrapper script pure functions."""

import json
import sys
from pathlib import Path

# Make the wrapper importable regardless of how pytest is invoked.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client" / "luma_denoise" / "scripts"))

import oiio_combine  # noqa: E402


def test_parse_args_minimal_surface():
    """CLI should accept the minimum set of required args."""
    args = oiio_combine.parse_args([
        "--denoised", "/a/denoised.exr",
        "--raw", "/a/raw.exr",
        "--output", "/a/out.exr",
        "--oiiotool", "/bin/oiiotool",
    ])
    assert args.denoised == "/a/denoised.exr"
    assert args.raw == "/a/raw.exr"
    assert args.output == "/a/out.exr"
    assert args.oiiotool == "/bin/oiiotool"
    assert args.exclude == []
    assert args.num_default_excludes == 0
    assert args.rename == []
    assert args.extra_args == ""
    assert args.compression == ""
    assert args.data_type == "preserve"
    assert args.write_manifest is False
    assert args.verbose is False


def test_parse_args_full_surface():
    """CLI should accept all optional flags."""
    args = oiio_combine.parse_args([
        "--denoised", "/a/d.exr",
        "--raw", "/a/r.exr",
        "--output", "/a/o.exr",
        "--oiiotool", "/bin/oiiotool",
        "--exclude", "*_mse",
        "--exclude", "sampleCount",
        "--rename", "Ci.r=R",
        "--rename", "Ci.g=G",
        "--extra-args", "--planarconfig separate",
        "--compression", "zips",
        "--data-type", "float",
        "--write-manifest",
        "--verbose",
    ])
    assert args.exclude == ["*_mse", "sampleCount"]
    assert args.rename == ["Ci.r=R", "Ci.g=G"]
    assert args.extra_args == "--planarconfig separate"
    assert args.compression == "zips"
    assert args.data_type == "float"
    assert args.write_manifest is True
    assert args.verbose is True


def test_read_channels_oiio_uses_oiio_module(monkeypatch):
    """_read_channels_oiio calls ImageInput.open and reads channelnames."""
    class FakeSpec:
        channelnames = ["R", "G", "B", "A", "Ci.r", "CryptoMaterials.R"]

    class FakeInput:
        def spec(self):
            return FakeSpec()
        def close(self):
            pass

    class FakeImageInput:
        @staticmethod
        def open(path):
            assert path == "/a/file.exr"
            return FakeInput()

    fake_oiio = type("oiio", (), {"ImageInput": FakeImageInput})
    result = oiio_combine._read_channels_oiio("/a/file.exr", fake_oiio)
    assert result == ["R", "G", "B", "A", "Ci.r", "CryptoMaterials.R"]


def test_read_channels_oiio_raises_on_open_failure():
    """Open returning None must raise."""
    class FakeInputFailsOpen:
        @staticmethod
        def open(path):
            return None

    fake_oiio = type("oiio", (), {"ImageInput": FakeInputFailsOpen})
    try:
        oiio_combine._read_channels_oiio("/a/missing.exr", fake_oiio)
    except RuntimeError as e:
        assert "/a/missing.exr" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


# Representative oiiotool --info -v output snippet (one real production frame,
# trimmed to the "channel list:" section).
SAMPLE_INFO_STDOUT = (
    "Reading /a/file.exr\n"
    "/a/file.exr :  705 x  307, 4 channel, half/half/half/half openexr\n"
    "    channel list: Ci.r (half), Ci.g (half), Ci.b (half), a.Z (half)\n"
    "    pixel data origin: x=1272, y=490\n"
    "    compression: \"zips\"\n"
)


def test_parse_oiiotool_info_channels():
    channels = oiio_combine._parse_oiiotool_info_channels(SAMPLE_INFO_STDOUT)
    assert channels == ["Ci.r", "Ci.g", "Ci.b", "a.Z"]


def test_parse_oiiotool_info_channels_with_spaces_and_types():
    stdout = (
        "    channel list: R (half), G (half), B (half), A (float), "
        "CryptoMaterials.R (float), CryptoMaterials00.R (float)\n"
    )
    channels = oiio_combine._parse_oiiotool_info_channels(stdout)
    assert channels == ["R", "G", "B", "A", "CryptoMaterials.R", "CryptoMaterials00.R"]


def test_parse_oiiotool_info_channels_missing_raises():
    stdout = "Reading foo.exr\n    compression: \"zips\"\n"
    try:
        oiio_combine._parse_oiiotool_info_channels(stdout)
    except RuntimeError as e:
        assert "channel list" in str(e).lower()
    else:
        raise AssertionError("expected RuntimeError")


def test_read_channels_dispatch_prefers_oiio(monkeypatch):
    """read_channels should use OIIO Python when available."""
    calls = []

    def fake_oiio_reader(path, oiio_mod):
        calls.append(("oiio", path))
        return ["Ci.r", "Ci.g"]

    def fake_subprocess_reader(path, oiiotool_path):
        calls.append(("subprocess", path))
        return ["SHOULD_NOT_HAPPEN"]

    monkeypatch.setattr(oiio_combine, "_read_channels_oiio", fake_oiio_reader)
    monkeypatch.setattr(oiio_combine, "_read_channels_subprocess", fake_subprocess_reader)
    monkeypatch.setattr(oiio_combine, "_try_import_oiio", lambda: "FAKE_OIIO_MODULE")

    result = oiio_combine.read_channels("/a/file.exr", oiiotool_path="/bin/oiiotool")
    assert result == ["Ci.r", "Ci.g"]
    assert calls == [("oiio", "/a/file.exr")]


def test_read_channels_falls_back_to_subprocess(monkeypatch):
    """read_channels should fall back when OIIO Python is not importable."""
    calls = []

    def fake_subprocess_reader(path, oiiotool_path):
        calls.append(("subprocess", path, oiiotool_path))
        return ["R", "G", "B"]

    monkeypatch.setattr(oiio_combine, "_read_channels_subprocess", fake_subprocess_reader)
    monkeypatch.setattr(oiio_combine, "_try_import_oiio", lambda: None)

    result = oiio_combine.read_channels("/a/file.exr", oiiotool_path="/bin/oiiotool")
    assert result == ["R", "G", "B"]
    assert calls == [("subprocess", "/a/file.exr", "/bin/oiiotool")]


def test_apply_exclude_patterns_matches_glob():
    channels = ["Ci.r", "Ci.g", "albedo_mse.r", "mse.r", "mse.g", "sampleCount", "normal.x"]
    patterns = ["*_mse", "mse", "sampleCount"]
    kept, excluded = oiio_combine.apply_exclude_patterns(channels, patterns)
    assert "sampleCount" in excluded
    assert "normal.x" in kept
    assert "Ci.r" in kept


def test_apply_exclude_patterns_layer_aware():
    """Pattern 'mse' should match all subchannels via layer-prefix matching."""
    channels = ["mse.r", "mse.g", "mse.b", "albedo_mse.r"]
    patterns = ["mse"]
    kept, excluded = oiio_combine.apply_exclude_patterns(channels, patterns)
    assert set(excluded) == {"mse.r", "mse.g", "mse.b"}
    assert kept == ["albedo_mse.r"]


def test_apply_exclude_patterns_empty_returns_all():
    channels = ["R", "G", "B"]
    kept, excluded = oiio_combine.apply_exclude_patterns(channels, [])
    assert kept == ["R", "G", "B"]
    assert excluded == []


def test_compute_extra_channels_basic_set_diff():
    denoised = ["Ci.r", "Ci.g", "Ci.b", "a.Z", "diffuse.r"]
    raw = ["Ci.r", "Ci.g", "Ci.b", "a.Z", "diffuse.r",
           "CryptoMaterials00.R", "normal.x", "mse.r"]
    exclude_patterns = ["mse", "*_mse"]
    extras = oiio_combine.compute_extra_channels(denoised, raw, exclude_patterns)
    assert extras == ["CryptoMaterials00.R", "normal.x"]


def test_compute_extra_channels_preserves_raw_order():
    denoised = []
    raw = ["C", "A", "B"]
    assert oiio_combine.compute_extra_channels(denoised, raw, []) == ["C", "A", "B"]


def test_parse_rename_pairs_valid():
    pairs = ["Ci.r=R", "Ci.g=G", "a.Z=A"]
    parsed = oiio_combine.parse_rename_pairs(pairs)
    assert parsed == {"Ci.r": "R", "Ci.g": "G", "a.Z": "A"}


def test_parse_rename_pairs_invalid_raises():
    try:
        oiio_combine.parse_rename_pairs(["Ci.r-R"])
    except ValueError as e:
        assert "Ci.r-R" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_resolve_chnames_only_renames_present_channels():
    final_channels = ["Ci.r", "Ci.g", "Ci.b", "a.Z", "diffuse.r", "CryptoMaterials00.R"]
    rename_map = {"Ci.r": "R", "Ci.g": "G", "Ci.b": "B", "a.Z": "A", "missing": "NOPE"}
    result = oiio_combine.resolve_chnames(final_channels, rename_map)
    assert result == ["R", "G", "B", "A", "diffuse.r", "CryptoMaterials00.R"]


def test_resolve_chnames_returns_none_when_no_matches():
    final_channels = ["foo", "bar"]
    rename_map = {"Ci.r": "R"}
    assert oiio_combine.resolve_chnames(final_channels, rename_map) is None


def test_build_oiiotool_argv_normal_path():
    argv = oiio_combine.build_oiiotool_argv(
        oiiotool="/bin/oiiotool",
        denoised="/a/d.exr",
        raw="/a/r.exr",
        output="/a/o.exr",
        extra_channels=["CryptoMaterials00.R", "normal.x"],
        chnames_override=["R", "G", "B", "A", "diffuse.r"],
        compression="zips",
        data_type="preserve",
        extra_args="",
        pass_through=False,
    )
    assert argv[0] == "/bin/oiiotool"
    assert argv[1] == "/a/d.exr"
    assert argv[2] == "/a/r.exr"
    assert "--ch" in argv
    ch_idx = argv.index("--ch")
    assert argv[ch_idx + 1] == "CryptoMaterials00.R,normal.x"
    assert "--chappend" in argv
    assert "--chnames" in argv
    chn_idx = argv.index("--chnames")
    assert argv[chn_idx + 1] == "R,G,B,A,diffuse.r"
    assert "--compression" in argv
    assert argv[argv.index("--compression") + 1] == "zips"
    assert argv[-2] == "-o"
    assert argv[-1] == "/a/o.exr"
    assert "--format" not in argv


def test_build_oiiotool_argv_pass_through_skips_raw_and_chappend():
    argv = oiio_combine.build_oiiotool_argv(
        oiiotool="/bin/oiiotool",
        denoised="/a/r.exr",
        raw="/a/r.exr",
        output="/a/o.exr",
        extra_channels=[],
        chnames_override=["R", "G", "B", "A"],
        compression="zips",
        data_type="preserve",
        extra_args="",
        pass_through=True,
    )
    assert argv.count("/a/r.exr") == 1
    assert "--ch" not in argv
    assert "--chappend" not in argv
    assert "--chnames" in argv
    assert argv[-2:] == ["-o", "/a/o.exr"]


def test_build_oiiotool_argv_no_chnames_when_override_none():
    argv = oiio_combine.build_oiiotool_argv(
        oiiotool="/bin/oiiotool",
        denoised="/a/d.exr",
        raw="/a/r.exr",
        output="/a/o.exr",
        extra_channels=["normal.x"],
        chnames_override=None,
        compression="",
        data_type="preserve",
        extra_args="",
        pass_through=False,
    )
    assert "--chnames" not in argv
    assert "--compression" not in argv


def test_build_oiiotool_argv_data_type_float_adds_format():
    argv = oiio_combine.build_oiiotool_argv(
        oiiotool="/bin/oiiotool",
        denoised="/a/d.exr",
        raw="/a/r.exr",
        output="/a/o.exr",
        extra_channels=["normal.x"],
        chnames_override=None,
        compression="",
        data_type="float",
        extra_args="",
        pass_through=False,
    )
    assert "--format" in argv
    assert argv[argv.index("--format") + 1] == "float"


def test_build_oiiotool_argv_extra_args_inserted_between_chnames_and_output():
    argv = oiio_combine.build_oiiotool_argv(
        oiiotool="/bin/oiiotool",
        denoised="/a/d.exr",
        raw="/a/r.exr",
        output="/a/o.exr",
        extra_channels=["normal.x"],
        chnames_override=["R", "G", "B", "A"],
        compression="",
        data_type="preserve",
        extra_args="--planarconfig separate",
        pass_through=False,
    )
    planar_idx = argv.index("--planarconfig")
    chn_idx = argv.index("--chnames")
    o_idx = argv.index("-o")
    assert chn_idx < planar_idx < o_idx


def test_build_oiiotool_argv_empty_extras_no_ch_flags():
    argv = oiio_combine.build_oiiotool_argv(
        oiiotool="/bin/oiiotool",
        denoised="/a/d.exr",
        raw="/a/r.exr",
        output="/a/o.exr",
        extra_channels=[],
        chnames_override=["R", "G", "B", "A"],
        compression="",
        data_type="preserve",
        extra_args="",
        pass_through=False,
    )
    # extras empty but not pass-through: raw should NOT appear as input.
    assert "/a/r.exr" not in argv
    assert "--ch" not in argv
    assert "--chappend" not in argv
    assert "--chnames" in argv


def test_build_manifest_structure():
    manifest = oiio_combine.build_manifest(
        denoised_path="/a/d.exr",
        raw_path="/a/r.exr",
        output_path="/a/o.exr",
        pass_through=False,
        denoised_channels=["Ci.r", "Ci.g", "Ci.b", "a.Z"],
        raw_channels=["Ci.r", "CryptoMaterials00.R", "mse.r"],
        exclude_patterns_user=["debug_*"],
        exclude_patterns_default=["*_mse", "mse", "sampleCount"],
        excluded_channels=["mse.r"],
        appended_channels=["CryptoMaterials00.R"],
        chnames_applied={"Ci.r": "R", "Ci.g": "G", "Ci.b": "B", "a.Z": "A"},
        oiiotool_argv=["/bin/oiiotool", "/a/d.exr", "-o", "/a/o.exr"],
    )
    assert manifest["denoised_path"] == "/a/d.exr"
    assert manifest["raw_path"] == "/a/r.exr"
    assert manifest["output_path"] == "/a/o.exr"
    assert manifest["pass_through"] is False
    assert manifest["denoised_channels"] == ["Ci.r", "Ci.g", "Ci.b", "a.Z"]
    assert manifest["raw_channels"] == ["Ci.r", "CryptoMaterials00.R", "mse.r"]
    assert manifest["exclude_patterns_user"] == ["debug_*"]
    assert manifest["exclude_patterns_default"] == ["*_mse", "mse", "sampleCount"]
    assert manifest["excluded_channels"] == ["mse.r"]
    assert manifest["appended_channels"] == ["CryptoMaterials00.R"]
    assert manifest["chnames_applied"] == {"Ci.r": "R", "Ci.g": "G", "Ci.b": "B", "a.Z": "A"}
    assert manifest["oiiotool_command"] == "/bin/oiiotool /a/d.exr -o /a/o.exr"
    assert "timestamp" in manifest
    # Sequence-level: per-frame fields are intentionally absent.
    assert "frame" not in manifest
    assert "exit_code" not in manifest


def test_build_manifest_normalizes_frame_token():
    manifest = oiio_combine.build_manifest(
        denoised_path="/a/denoised/shot.0042.exr",
        raw_path="/a/shot.0042.exr",
        output_path="/a/combined/shot.0042.exr",
        pass_through=False,
        denoised_channels=[],
        raw_channels=[],
        exclude_patterns_user=[],
        exclude_patterns_default=[],
        excluded_channels=[],
        appended_channels=[],
        chnames_applied={},
        oiiotool_argv=["/bin/oiiotool", "/a/denoised/shot.0042.exr",
                       "-o", "/a/combined/shot.0042.exr"],
    )
    assert manifest["denoised_path"] == "/a/denoised/shot.####.exr"
    assert manifest["raw_path"] == "/a/shot.####.exr"
    assert manifest["output_path"] == "/a/combined/shot.####.exr"
    assert ("/a/denoised/shot.####.exr" in manifest["oiiotool_command"]
            and "/a/combined/shot.####.exr" in manifest["oiiotool_command"])


def test_write_manifest_handles_write_failure(tmp_path):
    """Manifest write errors must be caught and logged without raising."""
    output = tmp_path / "does" / "not" / "exist" / "out.exr"
    rc = oiio_combine.write_manifest(str(output), {"foo": "bar"})
    assert rc is False


def test_write_manifest_writes_sidecar(tmp_path):
    output = tmp_path / "out.exr"
    output.write_bytes(b"fake exr content")
    rc = oiio_combine.write_manifest(str(output), {"foo": "bar"})
    # Sequence-level: '<stem>.combine.json', not '<name>.<ext>.combine.json'.
    sidecar = tmp_path / "out.combine.json"
    assert rc is True
    assert sidecar.exists()
    import json
    assert json.loads(sidecar.read_text())["foo"] == "bar"


def test_write_manifest_strips_frame_from_sidecar(tmp_path):
    output = tmp_path / "combined" / "shot.1001.exr"
    output.parent.mkdir(parents=True)
    rc = oiio_combine.write_manifest(str(output), {"foo": "bar"})
    sidecar = tmp_path / "combined" / "shot.combine.json"
    assert rc is True
    assert sidecar.exists()
    # The per-frame variant must NOT exist.
    assert not (tmp_path / "combined" / "shot.1001.exr.combine.json").exists()


def test_strip_frame_token():
    assert oiio_combine._strip_frame_token("/a/shot.1001.exr") == "/a/shot.####.exr"
    assert oiio_combine._strip_frame_token("/a/shot.0042.exr") == "/a/shot.####.exr"
    assert oiio_combine._strip_frame_token("/a/shot_name.exr") == "/a/shot_name.exr"
    assert oiio_combine._strip_frame_token("/a/shot.v044.exr") == "/a/shot.v044.exr"


def test_sequence_sidecar_path():
    p = oiio_combine._sequence_sidecar_path("/a/shot.1001.exr")
    assert p.as_posix() == "/a/shot.combine.json"
    p = oiio_combine._sequence_sidecar_path("/a/no_frame.exr")
    assert p.as_posix() == "/a/no_frame.combine.json"


def test_main_end_to_end_mocked(monkeypatch, tmp_path):
    """main() orchestrates reading channels, building argv, running oiiotool, and exiting with its rc."""
    denoised = tmp_path / "denoised" / "shot.1001.exr"
    raw = tmp_path / "shot.1001.exr"
    output = tmp_path / "combined" / "shot.1001.exr"
    denoised.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True)
    denoised.write_bytes(b"")
    raw.write_bytes(b"")

    recorded = {}

    def fake_read_channels(path, oiiotool_path):
        if "denoised" in path:
            return ["Ci.r", "Ci.g", "Ci.b", "a.Z", "diffuse.r"]
        return ["Ci.r", "Ci.g", "Ci.b", "a.Z", "diffuse.r",
                "CryptoMaterials00.R", "normal.x", "mse.r"]

    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()

    monkeypatch.setattr(oiio_combine, "read_channels", fake_read_channels)
    monkeypatch.setattr(oiio_combine.subprocess, "run", fake_run)

    rc = oiio_combine.main([
        "--denoised", str(denoised),
        "--raw", str(raw),
        "--output", str(output),
        "--oiiotool", "/bin/oiiotool",
        "--exclude", "*_mse",
        "--exclude", "mse",
        "--exclude", "sampleCount",
        "--rename", "Ci.r=R",
        "--rename", "Ci.g=G",
        "--rename", "Ci.b=B",
        "--rename", "a.Z=A",
        "--compression", "zips",
        "--write-manifest",
        "--verbose",
    ])

    assert rc == 0
    argv = recorded["argv"]
    assert argv[0] == "/bin/oiiotool"
    assert argv[-2:] == ["-o", str(output)]
    ch_idx = argv.index("--ch")
    extras = argv[ch_idx + 1].split(",")
    assert "CryptoMaterials00.R" in extras
    assert "normal.x" in extras
    assert "mse.r" not in extras
    sidecar = tmp_path / "combined" / "shot.combine.json"
    assert sidecar.exists()
    import json
    manifest = json.loads(sidecar.read_text())
    assert "frame" not in manifest
    assert "exit_code" not in manifest
    assert manifest["pass_through"] is False
    assert manifest["output_path"].endswith("shot.####.exr")


def test_main_pass_through_when_denoised_equals_raw(monkeypatch, tmp_path):
    raw = tmp_path / "shot.1001.exr"
    output = tmp_path / "combined" / "shot.1001.exr"
    output.parent.mkdir(parents=True)
    raw.write_bytes(b"")

    def fake_read_channels(path, oiiotool_path):
        return ["beauty.r", "beauty.g", "beauty.b", "a.Z", "normal.x"]

    recorded = {}
    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(oiio_combine, "read_channels", fake_read_channels)
    monkeypatch.setattr(oiio_combine.subprocess, "run", fake_run)

    rc = oiio_combine.main([
        "--denoised", str(raw),
        "--raw", str(raw),
        "--output", str(output),
        "--oiiotool", "/bin/oiiotool",
        "--rename", "beauty.r=R",
        "--rename", "beauty.g=G",
        "--rename", "beauty.b=B",
        "--rename", "a.Z=A",
        "--write-manifest",
    ])
    assert rc == 0
    argv = recorded["argv"]
    assert "--ch" not in argv
    assert "--chappend" not in argv
    assert "--chnames" in argv

    sidecar = tmp_path / "combined" / "shot.combine.json"
    m = json.loads(sidecar.read_text())
    assert m["pass_through"] is True


# --- denoise manifest reading -------------------------------------------


def test_denoise_sidecar_path_strips_frame_token():
    p = oiio_combine._denoise_sidecar_path("/d/denoised/shot.1001.exr")
    assert str(p).replace("\\", "/") == "/d/denoised/shot.denoise.json"


def test_load_denoise_manifest_missing_returns_none(tmp_path):
    result = oiio_combine.load_denoise_manifest(
        str(tmp_path / "shot.1001.exr"))
    assert result is None


def test_load_denoise_manifest_reads_sidecar(tmp_path):
    sidecar = tmp_path / "shot.denoise.json"
    sidecar.write_text(json.dumps({
        "denoiser": "oidn",
        "beauty_channel_map": {"beauty.r": "R"},
    }))
    result = oiio_combine.load_denoise_manifest(str(tmp_path / "shot.1001.exr"))
    assert result["denoiser"] == "oidn"
    assert result["beauty_channel_map"] == {"beauty.r": "R"}


def test_load_denoise_manifest_corrupt_returns_none(tmp_path, capsys):
    (tmp_path / "shot.denoise.json").write_text("{not json")
    result = oiio_combine.load_denoise_manifest(str(tmp_path / "shot.1001.exr"))
    assert result is None
    assert "WARN" in capsys.readouterr().err


def test_resolve_rename_map_prefers_manifest(tmp_path):
    sidecar = tmp_path / "shot.denoise.json"
    sidecar.write_text(json.dumps({
        "denoiser": "oidn",
        "beauty_channel_map": {"beauty.r": "R", "a.Z": "A"},
    }))
    rename_map = oiio_combine.resolve_rename_map(
        denoised_path=str(tmp_path / "shot.1001.exr"),
        cli_rename_pairs=["Ci.r=R", "Ci.g=G"],
        pass_through=False,
        verbose=False,
    )
    assert rename_map == {"beauty.r": "R", "a.Z": "A"}


def test_resolve_rename_map_falls_back_to_cli(tmp_path):
    rename_map = oiio_combine.resolve_rename_map(
        denoised_path=str(tmp_path / "shot.1001.exr"),
        cli_rename_pairs=["Ci.r=R", "Ci.g=G"],
        pass_through=False,
        verbose=False,
    )
    assert rename_map == {"Ci.r": "R", "Ci.g": "G"}


def test_resolve_rename_map_pass_through_ignores_manifest(tmp_path):
    sidecar = tmp_path / "shot.denoise.json"
    sidecar.write_text(json.dumps({
        "beauty_channel_map": {"beauty.r": "R"},
    }))
    rename_map = oiio_combine.resolve_rename_map(
        denoised_path=str(tmp_path / "shot.1001.exr"),
        cli_rename_pairs=["beauty.r=R"],
        pass_through=True,
        verbose=False,
    )
    assert rename_map == {"beauty.r": "R"}


