"""HarmonyOS build-profile 密码加解密（调用 DevEco 兼容 Node 脚本）。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_ENCRYPTED_RE = re.compile(r"^000000[0-9a-fA-F]+$")
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "harmony_signing_password.js"


def is_encrypted_password(value: str) -> bool:
    text = (value or "").strip()
    return bool(_ENCRYPTED_RE.fullmatch(text)) and len(text) >= 32


def _run_node(action: str, material_dir: Path, value: str) -> str:
    if not _SCRIPT.is_file():
        raise FileNotFoundError(f"缺少加密脚本：{_SCRIPT}")
    proc = subprocess.run(
        ["node", str(_SCRIPT), action, str(material_dir.resolve()), value],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err or f"密码{action}失败")
    return proc.stdout.strip()


def encrypt_signing_password(material_dir: Path, plaintext: str) -> str:
    pwd = (plaintext or "").strip()
    if not pwd:
        raise ValueError("密码不能为空")
    if is_encrypted_password(pwd):
        return pwd
    return _run_node("encrypt", material_dir, pwd)


def decrypt_signing_password(material_dir: Path, encrypted_hex: str) -> str:
    return _run_node("decrypt", material_dir, encrypted_hex)
