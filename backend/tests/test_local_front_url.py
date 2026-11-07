"""jt-ipam.sh 的 local_front_url：升級／doctor 檢查本機 nginx 時該走 https 還是 http（2026-11-07）。

「外部反向代理」架構（瀏覽器 → 前面的 HTTPS 代理 → 本機 nginx :80）本機不開 443，以前升級
拿 https://127.0.0.1 檢查永遠連不上，把成功的升級誤判成 FATAL，後面的步驟也被跳過。

這裡實際用 bash 跑那個函式（餵假的 `ss -ltn` 輸出，並開 pipefail 跟腳本一樣的嚴格模式），
不是只看字串。兩個埠都沒人聽時必須仍是 https：nginx 真的沒起來要照樣被報出來。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[2] / "scripts" / "jt-ipam.sh"

HEADER = "State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process\n"


def _func(name: str) -> str:
    src = INSTALLER.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", src, re.S | re.M)
    assert m, f"jt-ipam.sh 裡找不到 {name}()"
    return m.group(0)


def _front(tmp_path: Path, *listening: str) -> str:
    f = tmp_path / "ss.txt"
    f.write_text(HEADER + "".join(f"LISTEN 0 511 {a} 0.0.0.0:*\n" for a in listening))
    out = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{_func('local_front_url')}\nlocal_front_url"],
        capture_output=True, text=True, timeout=10,
        env={"PATH": "/usr/bin:/bin", "JT_IPAM_LISTEN_FILE": str(f)})
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_default_layout_uses_https(tmp_path: Path) -> None:
    assert _front(tmp_path, "0.0.0.0:443", "0.0.0.0:80", "127.0.0.1:8000") == "https://127.0.0.1"
    assert _front(tmp_path, "[::]:443", "127.0.0.1:8000") == "https://127.0.0.1"
    assert _front(tmp_path, "*:443") == "https://127.0.0.1"


def test_external_reverse_proxy_layout_uses_http(tmp_path: Path) -> None:
    assert _front(tmp_path, "0.0.0.0:80", "[::]:80", "127.0.0.1:8000") == "http://127.0.0.1"


def test_nothing_listening_stays_https_so_a_dead_nginx_is_still_reported(tmp_path: Path) -> None:
    assert _front(tmp_path, "127.0.0.1:8000") == "https://127.0.0.1"
    assert _front(tmp_path) == "https://127.0.0.1"


def test_similar_port_numbers_do_not_count(tmp_path: Path) -> None:
    # 8443 / 8080 / 4430 不是 443 / 80
    assert _front(tmp_path, "0.0.0.0:8443", "0.0.0.0:8080", "0.0.0.0:4430") == "https://127.0.0.1"
    assert _front(tmp_path, "0.0.0.0:8080", "0.0.0.0:80") == "http://127.0.0.1"


def test_upgrade_doctor_and_agent_url_all_use_the_helper() -> None:
    src = INSTALLER.read_text(encoding="utf-8")
    assert src.count("$(local_front_url)") >= 3
    # 這三處不能再寫死本機 443
    assert '_rdy_url="https://127.0.0.1/api/v1/system/version"' not in src
    assert '_up_url="https://127.0.0.1"; else' not in src
