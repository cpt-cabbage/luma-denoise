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
