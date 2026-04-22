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
