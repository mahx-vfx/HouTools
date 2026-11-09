"""Pack the HouTools release zip (necessary files only) and verify it.

Usage:
    python pack_zip.py <version> [--out <path>] [--root <repo>]

<version>  release number with or without the leading "v" (v1.4.0 / 1.4.0).
--out      output path; defaults to HouTools.zip in the user's REAL Downloads
           folder (resolved via SHGetKnownFolderPath -- user folders may be
           redirected, do not assume C:\\Users\\<name>\\Downloads). Use a temp
           path here for dry runs so the real zip is not clobbered.
--root     repo root; auto-detected by walking up from this script looking
           for HouTools.json + MainMenuCommon.xml when omitted.

Behavior:
1. Check (explicit raise, not assert -- python -O strips asserts) the
   working tree already carries <version> in houtools/__init__.py
   and docs/index.html (prevents packing a stale version).
2. Stage ONLY runtime-necessary files with a two-entry top level: the package
   manifest HouTools.json at the zip ROOT and everything else under a
   HouTools/ folder -- extraction yields exactly `HouTools.json` + `HouTools/`
   side by side, so unzipping straight into Documents/houdiniXX.X/packages/
   completes the install (the manifest points at $HOUDINI_PACKAGE_PATH/HouTools).
   Inside HouTools/: README.md, LICENSE, MainMenuCommon.xml,
   NetworkViewMenu.xml, OPmenu.xml, python3.11libs/ (H21 entry shim),
   python3.13libs/, python_panels/, scripts/, docs/
   LICENSE ships with the zip: GPL section 4 requires giving all recipients
   a copy of the license along with the Program, and README's [GPL-3.0](LICENSE)
   link only resolves in zip installs when LICENSE sits next to it.
   Excluded on purpose: tests/, AGENTS.md, settings/, __pycache__, *.pyc.
3. Zip with Python zipfile (forward-slash entry names; PowerShell 5.1
   Compress-Archive writes backslash entries -- do not use it).
4. Verify the finished zip: integrity, packaged index.html carries
   v<version>, no excluded paths, no pycache. Prints path/entries/size.
"""

import ctypes
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

FILES = ["README.md", "LICENSE", "MainMenuCommon.xml", "NetworkViewMenu.xml", "OPmenu.xml"]
DIRS = ["python3.11libs", "python3.13libs", "python_panels", "scripts", "docs"]
MANIFEST = "HouTools.json"  # zip top level, next to PACKAGE_DIR
PACKAGE_DIR = "HouTools"

_DOWNLOADS_GUID = uuid.UUID("{374DE290-123F-4565-9164-39C4925E467B}")  # FOLDERID_Downloads


def downloads_dir() -> Path:
    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    g = _GUID(_DOWNLOADS_GUID.time_low, _DOWNLOADS_GUID.time_mid,
              _DOWNLOADS_GUID.time_hi_version,
              (ctypes.c_ubyte * 8)(*_DOWNLOADS_GUID.bytes[8:]))
    buf = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(g), 0, None, ctypes.byref(buf)) == 0:
        return Path(buf.value)
    return Path.home() / "Downloads"


def find_repo_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit)
        if not (root / "HouTools.json").is_file():
            raise SystemExit("--root has no HouTools.json: %s" % root)
        return root
    for p in Path(__file__).resolve().parents:
        if (p / "HouTools.json").is_file() and (p / "MainMenuCommon.xml").is_file():
            return p
    raise SystemExit("repo root not found (no HouTools.json above the skill)")


def _flag_value(args: list[str], name: str) -> str | None:
    """取 --flag 的值；不在参数里返回 None，在末尾缺值直接报错——
    args[index+1] 裸取在 flag 是最后一个参数时抛 IndexError，报错
    形态（list index out of range）对不上真实原因。"""
    if name not in args:
        return None
    i = args.index(name)
    if i + 1 >= len(args):
        raise SystemExit(f"{name} 缺少值")
    return args[i + 1]


def _tracked_files(root: Path) -> set[str]:
    """git ls-files 白名单（仓库相对 POSIX 路径）。打包只收这里面的文件，
    本地未提交的草稿/密钥/临时文件不会混进发行包。"""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, check=True)
    return {p for p in out.stdout.decode("utf-8").split("\x00") if p}


def _pick_release_files(root: Path, tracked: set[str]) -> list[Path]:
    """收集 FILES+DIRS 内需要打包的文件白名单。

    - 只收 git 已跟踪文件：copytree 会把本地未跟踪文件（备份、密钥、
      临时文件）整目录带进包
    - 拒绝符号链接：copytree 默认跟随链接，可能把包外目录内容打进 zip
    - `__pycache__`/`*.pyc` 维持旧行为排除（开发机原位运行会产生）
    """
    picked = []
    for entry in FILES + DIRS:
        p = root / entry
        if p.is_file():
            picked.append(p)
        elif p.is_dir():
            # 开发机原位运行会产生 __pycache__：收集阶段即排除（旧版
            # copytree ignore_patterns 的同语义），漏进白名单会让最终
            # 校验"pycache leaked"失败
            picked.extend(q for q in sorted(p.rglob("*"))
                          if q.is_file()
                          and "__pycache__" not in q.parts
                          and q.suffix != ".pyc")
        else:
            raise SystemExit("missing release path: %s" % p)
    for p in picked:
        rel = p.relative_to(root).as_posix()
        st = os.lstat(str(p))
        if stat.S_ISLNK(st.st_mode):
            raise SystemExit("symlink in release tree: %s" % rel)
        if rel not in tracked:
            raise SystemExit(
                "untracked file in release tree: %s (commit it or remove it)"
                % rel)
    return picked


def main(argv: list[str]) -> None:
    args = [a for a in argv[1:]]
    if not args:
        raise SystemExit(__doc__)
    version = args[0].lstrip("vV")
    out_val = _flag_value(args, "--out")
    out = Path(out_val) if out_val else None
    root = find_repo_root(_flag_value(args, "--root"))
    out = out or downloads_dir() / "HouTools.zip"

    init_py = (root / "python3.13libs" / "houtools" / "__init__.py").read_text(encoding="utf-8")
    html = (root / "docs" / "index.html").read_text(encoding="utf-8")
    # 校验用显式 raise 不用 assert：python -O 会剥掉全部 assert，版本
    # 错误的包会静默打出来
    if '__version__ = "%s"' % version not in init_py:
        raise SystemExit(
            "python3.13libs/houtools/__init__.py not bumped to %s" % version)
    if '<span class="v">v%s</span>' % version not in html:
        raise SystemExit(
            "docs/index.html hero version not bumped to v%s" % version)

    tmp = Path(tempfile.mkdtemp(prefix="houtools_pkg_"))
    try:
        stage = tmp / PACKAGE_DIR
        stage.mkdir()
        tracked = _tracked_files(root)
        for p in _pick_release_files(root, tracked):
            dst = stage / p.relative_to(root)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
        shutil.copy2(root / MANIFEST, tmp / MANIFEST)

        out.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时名再原子落位：直接写最终路径时中途崩溃/盘满会留下
        # 截断 zip，create_release 上传前只检查 is_file()，坏包会被当
        # 附件传上去。临时名带 PID+线程 id（同插件内原子写约定）
        tmp_zip = out.with_name(
            out.name + ".~tmp%d_%d" % (os.getpid(), threading.get_ident()))
        try:
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED,
                                 compresslevel=9) as z:
                for p in sorted(tmp.rglob("*")):
                    if p.is_file():
                        z.write(p, p.relative_to(tmp))
            os.replace(tmp_zip, out)
        finally:
            if tmp_zip.exists():
                try:
                    tmp_zip.unlink()
                except OSError as exc:
                    print("warn: cannot remove partial zip %s: %s"
                          % (tmp_zip, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    with zipfile.ZipFile(out) as z:
        # 以下全部显式 raise（-O 下 assert 失效，见上）
        if z.testzip() is not None:
            raise SystemExit("corrupt zip entry")
        names = z.namelist()
        if any("\\" in n for n in names):
            raise SystemExit("backslash entry name")
        if MANIFEST not in names:
            raise SystemExit("manifest not at zip top level")
        if any(n.startswith("%s/%s" % (PACKAGE_DIR, MANIFEST))
               for n in names):
            raise SystemExit("manifest leaked into package dir")
        if any("__pycache__" in n or n.endswith(".pyc") for n in names):
            raise SystemExit("pycache leaked")
        banned = ("%s/tests/" % PACKAGE_DIR,
                  "%s/AGENTS" % PACKAGE_DIR,
                  "%s/settings/" % PACKAGE_DIR)
        if any(n.startswith(banned) for n in names):
            raise SystemExit("dev file leaked")
        if "%s/LICENSE" % PACKAGE_DIR not in names:
            raise SystemExit("LICENSE missing from package")
        packaged_html = z.read("%s/docs/index.html" % PACKAGE_DIR).decode("utf-8")
        if "v%s" % version not in packaged_html:
            raise SystemExit("packaged index.html missing v%s" % version)

    print("zip:", out)
    print("entries:", len(names))
    print("size: %.2f MB" % (out.stat().st_size / 1048576))
    print("top level:", sorted(set(n.split("/")[0] for n in names)))
    print("PACK OK")


if __name__ == "__main__":
    main(sys.argv)
