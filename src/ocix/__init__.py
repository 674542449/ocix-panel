"""OCIX · Oracle Always Free 开机面板。"""

from pathlib import Path


def _read_version() -> str:
    # VERSION 文件是唯一版本号来源；scripts/release.sh 每次发布都会改它
    for candidate in (Path(__file__).resolve().parent / "VERSION",
                      Path(__file__).resolve().parents[2] / "VERSION"):
        try:
            return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return "0.0.0"


__version__ = _read_version()
