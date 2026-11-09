"""Create the GitCode release for a pushed tag and upload HouTools.zip to it.

Usage:
    python create_release.py <version> [--notes <file>] [--zip <path>] [--root <repo>]

<version>  release tag with or without the leading "v" (v1.6.0 / 1.6.0). The
           tag must already exist locally AND on the remote (skill step 6
           pushes it after the user confirms the notes); its annotated
           message doubles as the release notes.
--notes    notes file override; default reads the annotated tag message via
           `git tag -l --format=%(contents)` (skill step 6 writes the notes
           there, so the default path needs no extra file).
--zip      attachment override; defaults to HouTools.zip in the user's REAL
           Downloads folder (SHGetKnownFolderPath -- user folders may be
           redirected, same helper as pack_zip.py).
--root     repo root; auto-detected by walking up from this script looking
           for HouTools.json + MainMenuCommon.xml (same as pack_zip.py).

Token lookup (mirrors the zhuque-ai-detector skill's key handling):
1. config file ~/.gitcode_api_token -- one plain-text line; utf-8-sig and
   utf-16 are both tolerated so PowerShell redirects work too;
2. persistent user env var GITCODE_TOKEN read from registry HKCU\\Environment.
   Deliberately NOT os.environ: a process env is a snapshot taken at launch
   and may be stale; the registry value is what setx writes, so a fresh
   `setx GITCODE_TOKEN <value>` takes effect immediately without restarting.

GitCode OpenAPI v5 flow (verified 2026-10-17 shipping v1.6.0):
1. GET  /user                     token check; fails fast with guidance.
2. GET  /releases/tags/<tag>      reuse an existing release, never duplicate.
3. POST /releases                 create {tag_name, name, body,
                                  release_status: latest}. The tag already
                                  exists on the remote, so target_commitish
                                  is not needed. An existing release keeps
                                  its body UNLESS it is empty (then PATCH
                                  the notes in) -- a differing non-empty
                                  body is treated as user edits, kept as-is.
4. GET  .../upload_url            short-lived OBS signed URL plus a header
                                  set that MUST be forwarded verbatim --
                                  x-obs-callback is what links the asset to
                                  the release afterwards.
5. PUT  the zip to OBS            NO GitCode token on this request and NO
                                  redirect following (the signed URL is
                                  single-purpose). Use it immediately: the
                                  signature expires quickly.
6. poll GET /releases/tags/<tag>  until HouTools.zip shows up in assets
                                  (the OBS callback lags a few seconds).

Idempotent: re-running never duplicates the release or the attachment.
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "https://api.gitcode.com/api/v5"
FILE_NAME = "HouTools.zip"
TOKEN_FILE = ".gitcode_api_token"
TOKEN_PORTAL = "https://gitcode.com/setting/token-classic"
POLL_TRIES, POLL_WAIT_S = 6, 3

_DOWNLOADS_GUID = uuid.UUID("{374DE290-123F-4565-9164-39C4925E467B}")  # FOLDERID_Downloads


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(NoRedirect)


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


def read_token():
    """配置文件 ~/.gitcode_api_token → 注册表用户环境变量 GITCODE_TOKEN。

    故意不读 os.environ：进程环境是启动时的一次性快照可能过时；注册表
    HKCU\\Environment 是 setx 的持久落点，改完即时生效无需重开终端。
    """
    key_file = os.path.join(os.path.expanduser("~"), TOKEN_FILE)
    try:
        try:
            with open(key_file, "r", encoding="utf-8-sig") as f:
                v = f.read()
        except UnicodeDecodeError:  # PowerShell 重定向默认写出 UTF-16
            with open(key_file, "r", encoding="utf-16") as f:
                v = f.read()
        v = v.replace("\x00", "").strip().strip('"').strip("'")
        if v:
            return v, "配置文件 %s" % key_file
    except OSError:
        pass
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                raw, _ = winreg.QueryValueEx(k, "GITCODE_TOKEN")
        except OSError:
            raw = None
        v = str(raw or "").replace("\x00", "").strip().strip('"').strip("'")
        if v:
            return v, "用户环境变量 GITCODE_TOKEN（注册表）"
    return None, None


def token_help():
    print("未找到 gitcode 令牌。二选一配置：")
    print('  ① echo <令牌> > ~/%s   （bash；令牌在 %s 创建）' % (TOKEN_FILE, TOKEN_PORTAL))
    print("  ② setx GITCODE_TOKEN <令牌>   （用户环境变量，即时生效）")
    print("令牌需授予仓库 releases 相关权限。")


def git_output(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def parse_owner_repo(remote_url: str) -> tuple[str, str]:
    m = re.search(r"gitcode\.com[/:](.+?)/(.+?)\.git/?$", remote_url.strip())
    if not m:
        raise SystemExit("cannot parse gitcode owner/repo from origin: %s" % remote_url)
    return m.group(1), m.group(2)


def call(method, url, token=None, headers=None, data=None, timeout=120):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.data = data
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        return 0, str(e.reason).encode("utf-8", "replace")


def jtext(b: bytes) -> str:
    return b.decode("utf-8", "replace")


def release_url(owner: str, repo: str, tag: str) -> str:
    return "https://gitcode.com/%s/%s/releases/%s" % (owner, repo, tag)


def asset_names(release: dict) -> list[str]:
    return [a.get("name") or a.get("file_name") or "" for a in release.get("assets") or []]


def _verify_zip(zip_path: Path, version: str) -> None:
    """上传前校验本地 zip：完整性 + 顶层结构 + 版本与 tag 一致。

    pack_zip.py 已做过全套校验，但下载目录可能残留上一版 HouTools.zip、
    或包在打包后被替换/损坏——只检查 is_file() 会把旧包/坏包当附件挂
    到新 release 上（且"附件已存在"的幂等早退会阻止修复重传）。
    """
    import zipfile
    try:
        with zipfile.ZipFile(zip_path) as z:
            if z.testzip() is not None:
                raise SystemExit("zip corrupt entry: %s" % zip_path)
            names = z.namelist()
            manifest = "HouTools.json"
            if manifest not in names:
                raise SystemExit("zip missing manifest at top level: %s" % zip_path)
            init_name = "HouTools/python3.13libs/houtools/__init__.py"
            if init_name not in names:
                raise SystemExit("zip missing %s: %s" % (init_name, zip_path))
            init_src = z.read(init_name).decode("utf-8")
            if '__version__ = "%s"' % version not in init_src:
                raise SystemExit(
                    "zip __init__.py is not version %s (stale package?) - "
                    "re-run pack_zip.py" % version)
    except zipfile.BadZipFile as exc:
        raise SystemExit("zip unreadable %s: %s" % (zip_path, exc))


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


def main(argv: list[str]) -> None:
    args = list(argv[1:])
    if not args:
        raise SystemExit(__doc__)
    tag = "v" + args[0].lstrip("vV")
    version = args[0].lstrip("vV")
    root = find_repo_root(_flag_value(args, "--root"))

    # 先验 annotated：轻量 tag 上 %(contents) 回退为所指 commit 的完整
    # message——release 说明会静默变成提交文案并 POST 到公网 release 页
    # （下方空串守卫拦不住带正常 commit message 的轻量 tag）
    objtype = git_output(
        root, "tag", "-l", "--format=%(objecttype)", tag).strip()
    if objtype != "tag":
        raise SystemExit(
            "tag %s is a lightweight tag (points at a %s), not an annotated "
            "tag - skill step 6 writes the confirmed release notes into an "
            "annotated tag first" % (tag, objtype or "missing ref"))
    notes = git_output(root, "tag", "-l", "--format=%(contents)", tag).strip()
    notes_file = _flag_value(args, "--notes")
    if notes_file:
        notes = Path(notes_file).read_text(encoding="utf-8").strip()
    if not notes:
        raise SystemExit("tag %s has no annotated message - skill step 6 writes "
                         "the confirmed release notes into the tag first" % tag)
    zip_val = _flag_value(args, "--zip")
    zip_path = (Path(zip_val) if zip_val
                else downloads_dir() / FILE_NAME)
    if not zip_path.is_file():
        raise SystemExit("attachment not found: %s (run pack_zip.py first)" % zip_path)
    # 任何 API 写操作之前先验本地包：完整性/结构/版本与 tag 一致
    _verify_zip(zip_path, version)
    owner, repo = parse_owner_repo(git_output(root, "remote", "get-url", "origin"))

    token, src = read_token()
    if not token:
        token_help()
        raise SystemExit(1)
    print("token source:", src)

    # 1. token check
    s, b = call("GET", BASE + "/user", token)
    print("[1] token check:", s)
    if s == 401:
        print("    令牌无效或已吊销——更新 ~/%s 或注册表 GITCODE_TOKEN 后重试" % TOKEN_FILE)
        raise SystemExit(1)
    if s != 200:
        print(jtext(b)[:300])
        raise SystemExit(1)
    me = json.loads(jtext(b))
    print("    user:", me.get("login") or me.get("username") or me.get("name"))

    # 2. existing release?
    s, b = call("GET", BASE + "/repos/%s/%s/releases/tags/%s" % (owner, repo, tag), token)
    release = json.loads(jtext(b)) if s == 200 else None
    print("[2] existing release:", "yes" if release else "no (http %s)" % s)

    # 3. create or patch
    if release is None:
        body = {"tag_name": tag, "name": tag, "body": notes, "release_status": "latest"}
        s, b = call("POST", BASE + "/repos/%s/%s/releases" % (owner, repo), token,
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(body).encode("utf-8"))
        print("[3] create release:", s)
        if s not in (200, 201):
            print(jtext(b)[:500])
            raise SystemExit(1)
        release = json.loads(jtext(b))
    else:
        cur = (release.get("body") or "").strip()
        if cur == notes:
            print("[3] body already matches tag notes, skip patch")
        elif not cur:
            s2, b2 = call("PATCH", BASE + "/repos/%s/%s/releases/%s" % (owner, repo, tag),
                          token, headers={"Content-Type": "application/json"},
                          data=json.dumps({"name": release.get("name") or tag,
                                           "body": notes}).encode("utf-8"))
            print("[3] patch empty body:", s2)
            if s2 not in (200, 201):
                # PATCH 失败仍继续上传会把"说明没发出去"包装成 RELEASE OK
                # ——发行说明是发布契约的一部分，失败必须终止
                print(jtext(b2)[:300])
                raise SystemExit(1)
            # PATCH 成功后回读验证：确认服务端真的落了 notes 再继续
            s3, b3 = call("GET", BASE + "/repos/%s/%s/releases/tags/%s"
                          % (owner, repo, tag), token)
            if s3 != 200 or (json.loads(jtext(b3)).get("body") or "").strip() != notes:
                print("[3] body verification FAILED after patch")
                raise SystemExit(1)
            print("[3] body verified after patch")
        else:
            print("[3] existing body differs from tag notes, kept as-is")

    # 4-5. attachment
    if FILE_NAME in asset_names(release):
        # 幂等早退：附件已存在时不会重传（GitCode 无替换接口的既有约束）。
        # 本地 zip 已通过 _verify_zip（完整性/版本与 tag 一致），但远端
        # 附件内容无法回读比对——显式提醒"已存在 = 旧包不会被替换"，
        # 需要修复版本必须删掉旧附件/新开 tag 再发
        print("[4] attachment already present (NOT replaced; remote content "
              "not verified - delete the asset or cut a new tag to fix)")
        print("RELEASE OK", release_url(owner, repo, tag))
        return

    s, b = call("GET", BASE + "/repos/%s/%s/releases/%s/upload_url?file_name=%s"
                % (owner, repo, tag, urllib.parse.quote(FILE_NAME)), token, timeout=60)
    print("[4] upload_url:", s)
    if s != 200:
        print(jtext(b)[:500])
        raise SystemExit(1)
    up = json.loads(jtext(b))
    payload = zip_path.read_bytes()
    print("    zip bytes:", len(payload), "; obs headers:", sorted(up.get("headers", {})))

    s, b = call("PUT", up["url"], headers=up.get("headers", {}), data=payload, timeout=300)
    print("[5] PUT to OBS:", s)
    if s not in (200, 201):
        print(jtext(b)[:500])
        raise SystemExit(1)

    # 6. verify association (OBS callback may lag a few seconds)
    for _ in range(POLL_TRIES):
        time.sleep(POLL_WAIT_S)
        s, b = call("GET", BASE + "/repos/%s/%s/releases/tags/%s" % (owner, repo, tag), token)
        if s == 200:
            names = asset_names(json.loads(jtext(b)))
            if FILE_NAME in names:
                print("[6] verified assets:", names)
                print("RELEASE OK", release_url(owner, repo, tag))
                return
            print("    waiting for callback... assets=%s" % names)
    print("[6] asset not visible after %ds - check the release page shortly"
          % (POLL_TRIES * POLL_WAIT_S))
    raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv)
