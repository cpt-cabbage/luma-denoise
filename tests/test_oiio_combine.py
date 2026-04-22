"""Unit tests for oiio_combine wrapper script pure functions."""

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
