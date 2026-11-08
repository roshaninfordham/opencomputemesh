"""The install script's platform mapping (no network, no downloads)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def asset_for(os_name: str, arch: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "OCM_OS": os_name, "OCM_ARCH": arch}
    return subprocess.run(
        ["sh", str(SCRIPT), "--print-asset"], capture_output=True, text=True, env=env, timeout=20
    )


@pytest.mark.parametrize(("os_name", "arch", "asset"), [
    ("Darwin", "arm64", "macos-apple-silicon"),
    ("Darwin", "x86_64", "macos-intel"),
    ("Linux", "x86_64", "linux-x64"),
    ("Linux", "amd64", "linux-x64"),
])
def test_picks_the_build_for_the_machine(os_name, arch, asset):
    done = asset_for(os_name, arch)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == asset


@pytest.mark.parametrize(("os_name", "arch", "message"), [
    ("Linux", "aarch64", "no Linux arm64 build yet"),
    ("Linux", "riscv64", "unsupported Linux architecture"),
    ("FreeBSD", "x86_64", "unsupported operating system"),
    ("MINGW64_NT-10.0", "x86_64", "install.ps1"),
])
def test_refuses_what_it_cannot_install(os_name, arch, message):
    done = asset_for(os_name, arch)
    assert done.returncode != 0
    assert done.stdout == ""
    assert message in done.stderr


def test_help_lists_the_overrides():
    done = subprocess.run(["sh", str(SCRIPT), "--help"], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0
    assert "--print-asset" in done.stdout and "OCM_INSTALL_DIR" in done.stdout
