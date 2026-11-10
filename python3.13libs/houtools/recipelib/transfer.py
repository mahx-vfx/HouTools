"""Recipe 库数据打包：导出 / 导入（.zip 交换格式，设置面板「数据」页）。

导出把"用户积累"打成单个 zip：标签/收藏/自定义显示名/卡片颜色
（settings/recipelib.json 按当前库里的 recipe 过滤，陈旧条目不带）+
缩略图文件 + Markdown 文档（含 assets 媒体），可选连同样式工具的
.hda 库文件——"打包带走、换机/与人分享"。

导入按 recipe 内部名逐条合并：包内条目覆盖本地同名条目（标签/文档/
缩略图等），本地独有内容一律不动；.hda 只复制"其内所有 recipe 名在
本地都不存在"的文件——同名 recipe 意味着两份不同内容的同名定义，
Houdini 按库优先级二选一，行为不可预期，宁跳过不覆盖（导入摘要中
明示，删掉本地同名后可重新导入）。

zip 布局（manifest.json 的 format_version=1）：
  manifest.json            # 格式版本/导出信息/hda_files（文件→内含 recipe 名）
  recipelib.json           # 过滤后的元数据；thumbs 值改写为包内路径 thumbs/<名>
  thumbs/<safe名>.<ext>
  docs/<safe名>/doc.md + assets/*
  vex_python_lib/<语言>/<内部名>.h|.py   # 代码块（一卡一文件，随包直传）
  recipes/<原文件名>.hda     # 仅勾选「连同工具打包」时

代码块（2026-10-18 起一卡一文件，见 codeblock.py）按文件整体随包走，
包内路径镜像本地布局（vex_python_lib/<语言>/<名>），导入时解回
settings/vex_python_lib/；调用方传入的条目列表里含代码块 CodeInfo
（同样暴露 .name），标签/收藏/缩略图/文档按内部名过滤的既有逻辑对其
自动生效。**旧包兼容**：早期版本把代码放在 recipelib.json 的
code_blocks 段——读取端仍解析该键，导入时落成文件并改挂元数据键。

零 hou 依赖（枚举由调用方传入 store.list_recipes 的结果；导入后的库
安装走浏览器 reload() → ensure_libraries_installed 链路）——本模块只
做文件与 JSON 搬运，无头环境（smoke test）可完整测试。

取消语义：导出全程可取消（写临时文件，成功才原子替换，半成品即清）；
导入在元数据落盘前可取消——已解出的文件留在磁盘但不改任何设置，重跑
一次导入即完整（导入幂等）。
"""

import json
import os
import shutil
import threading
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

from houtools.core.log import get_logger
from houtools.recipelib import codeblock, metadata

log = get_logger("recipelib.transfer")

MANIFEST = "manifest.json"
METADATA = "recipelib.json"
FORMAT_VERSION = 1
APP_TAG = "HouTools Recipe Library"

# zip 资源配额：导入是"接收不可信第三方分享包"的入口，路径穿越之外还要
# 防压缩炸弹/海量成员把磁盘或内存打爆（检查/导入阶段即拒绝）。上限远
# 大于合法包的正常体量（配方库 + 媒体文档），只拦恶意/异常包
MAX_MEMBERS = 10000                            # 包内成员数上限
MAX_MEMBER_UNCOMPRESSED = 512 * 1024 * 1024    # 单成员解压后上限
MAX_TOTAL_UNCOMPRESSED = 2 * 1024 * 1024 * 1024  # 累计解压上限
MAX_META_BYTES = 16 * 1024 * 1024              # 单个 JSON 元数据成员上限

_THUMB_ARC_DIR = "thumbs/"
_DOC_ARC_DIR = "docs/"
_HDA_ARC_DIR = "recipes/"
_CODE_ARC_DIR = "vex_python_lib/"


class TransferError(RuntimeError):
    """包不合法/版本不认识等预期内失败，UI 直接展示 message。"""


class TransferCancelled(Exception):
    """用户在进度对话框点了取消（半成品已清理，不算错误）。"""


def _norm(path):
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(path))


def _compress_for(arcname):
    """json/md/代码文本走 deflate，其余（png/gif/视频/hda）原样存储——
    媒体本身已压缩，deflate 只烧 CPU 不减体积。"""
    if arcname.lower().endswith((".json", ".md", ".txt", ".h", ".py")):
        return zipfile.ZIP_DEFLATED
    return zipfile.ZIP_STORED


def _report(progress, done, total, text):
    """进度回调；返回真值 = 用户请求取消。回调自身异常不中断打包。"""
    if progress is None:
        return False
    try:
        return bool(progress(done, total, text))
    except Exception as exc:
        log.debug("progress callback failed: %s", exc)
        return False


def _safe_rel(rel):
    """校验包内相对路径（防 zip 路径穿越），返回 os 路径，不合法抛错。"""
    parts = [p for p in rel.replace("\\", "/").split("/")
             if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        raise TransferError("包内路径不合法: {}".format(rel))
    return os.path.join(*parts)


def _extract_member(zf, info, dst, budget=None):
    """把 zip 成员解到 dst（流式拷贝，文档里的视频可能上 GB）。

    budget：[累计解压字节] 的单元素列表（导入侧配额计数）。解压前按
    ZipInfo 声明大小校验单成员与累计上限——防压缩炸弹/超大成员写满磁盘。
    先写临时文件再 os.replace 原子落位：直接开正式名写一半失败（磁盘
    满/包损坏）会留下截断文件——.hda 落 lib_dir 后每次 reload 都对着
    坏文件告警重试、回滚名单还抓不到"正在写的这个"；文档/缩略图/代码
    文件同理。临时名带 PID+线程 id（同 settings.JsonStore 的约定，
    同进程两线程并发导入也不互踩）。落位前顺带过图片像素维度护栏
    （_assert_image_dimensions）——在临时文件上检查，像素炸弹根本
    不会以正式名落盘。
    """
    if info.file_size > MAX_MEMBER_UNCOMPRESSED:
        raise TransferError("包内文件过大: {}（{} MB）".format(
            info.filename, info.file_size // (1024 * 1024)))
    if budget is not None:
        if budget[0] + info.file_size > MAX_TOTAL_UNCOMPRESSED:
            raise TransferError(
                "包累计解压体积超过上限（{} GB），已中止导入".format(
                    MAX_TOTAL_UNCOMPRESSED // (1024 ** 3)))
        budget[0] += info.file_size
    dst = str(dst)
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = dst + ".~part%d_%d" % (os.getpid(), threading.get_ident())
    try:
        with zf.open(info) as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        # 扩展名判定必须用正式名 dst：tmp 后缀是 .~part*，不在图片白名单，
        # 会让 50MP 像素护栏对所有图片静默跳过（历史缺陷）
        _assert_image_dimensions(tmp, ext_name=dst)
        os.replace(tmp, dst)
    except (zipfile.BadZipFile, zlib.error) as exc:
        # 中央目录能读、成员数据损坏——典型是下载/传输截断的分享包。
        # BadZipFile/zlib.error 都不是 OSError 子类，不收进 TransferError
        # 会逃出调用侧 (TransferError, OSError) 的捕获，用户看不到
        # "导入失败"只留控制台 traceback（本模块契约见 _read_manifest）
        raise TransferError("包内成员损坏（包可能传输不完整）: {} ({})"
                            .format(info.filename, exc))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError as exc:
                log.warning("cannot remove partial member %s: %s", tmp, exc)


# 图片像素维度上限：zip 配额只数声明字节数，而 PNG/GIF 头里的像素数与
# 字节数无关（几 KB 的 PNG 可声明 1e5 x 1e5）——导入后文档/卡片渲染时
# QImage / QMovie（CacheAll 全帧缓存）会按全分辨率尝试数 GB 分配。解压
# 落位前按 QImageReader 探尺寸拦下（见 _extract_member）；视频没有
# QImageReader 解码器，维持字节配额约束。
MAX_IMAGE_PIXELS = 50 * 1000 * 1000
# GIF 额外全帧预算：单帧上限管不住帧数，QMovie CacheAll 按"全帧 ×
# 原始分辨率"缓存解码内存（800x600x1000 帧 ≈ 1.9GB）。导入期即拒收
# 累计超限动画；播放期同预算降级静态首帧（docs._gif_over_budget）。
MAX_GIF_TOTAL_PIXELS = 50 * 1000 * 1000


def _assert_image_dimensions(path, ext_name=None):
    """图片类成员的像素维度护栏；超限抛 TransferError（导入即拒收）。

    由 _extract_member 在 os.replace 之前对临时文件调用——检查放在
    落位后会让超限图片已经以正式名留在 thumbs/docs 目录（元数据未
    引用、也没有清理路径），成为永久垃圾。ext_name 提供扩展名判定
    依据（临时文件后缀是 .~part*，必须按正式名判图片类型）。

    GIF 额外检查全帧累计像素（imageCount × 单帧面积）：帧数不受单帧
    上限约束，是 CacheAll 播放的内存 DoS 面。imageCount 未知（-1/0）
    时跳过累计检查——不因探测失败拒收合法动画，单帧上限仍生效。"""
    name_for_ext = str(ext_name) if ext_name is not None else str(path)
    ext = os.path.splitext(name_for_ext)[1].lower()
    if ext not in metadata.IMAGE_EXTS:
        return
    from PySide6 import QtGui
    reader = QtGui.QImageReader(str(path))
    size = reader.size()
    if not size.isValid():
        return
    area = size.width() * size.height()
    if area > MAX_IMAGE_PIXELS:
        raise TransferError(
            "图片像素尺寸超限（{}x{}，上限 {}MP）: {}".format(
                size.width(), size.height(),
                MAX_IMAGE_PIXELS // (1000 * 1000), path))
    if ext == ".gif":
        count = reader.imageCount()
        if count > 0 and area * count > MAX_GIF_TOTAL_PIXELS:
            raise TransferError(
                "GIF 全帧解码尺寸超限（{}x{} x {} 帧，累计 {}MP，"
                "上限 {}MP——播放需全帧缓存内存）: {}".format(
                    size.width(), size.height(), count,
                    area * count // (1000 * 1000),
                    MAX_GIF_TOTAL_PIXELS // (1000 * 1000), path))


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------

def export_recipes(recipes, zip_path, include_hda=True, progress=None):
    """把 recipes（store.list_recipes 的结果）打包为交换 zip，返回摘要。

    只读 RecipeInfo 的内部名与 .library；元数据按现存 recipe 过滤
    （已删 recipe 的陈旧标签/缩略图不带）。progress(done, total, text)
    返回真值表示取消 → 抛 TransferCancelled，半成品已清理，目标路径
    不会留下半个 zip。
    """
    names = []
    for r in recipes or []:
        if getattr(r, "name", "") and r.name not in names:
            names.append(r.name)
    if not names:
        raise TransferError("当前库为空，没有可导出的内容")
    name_set = set(names)
    raw = metadata.all_metadata()

    # ---- 收集待打包文件（先列全，进度总数才准确） ----
    entries = []      # (包内路径, 源文件路径)
    thumbs_arc = {}   # 内部名 -> 包内缩略图路径（元数据改写用）
    for name in names:
        src = metadata.get_thumb(name)   # 含存在性检查，缺图自动跳过
        if not src:
            continue
        arc = _THUMB_ARC_DIR + os.path.basename(src)
        thumbs_arc[name] = arc
        entries.append((arc, src))

    for name in names:
        if not metadata.doc_exists(name):
            continue
        root = metadata.doc_dir(name)
        # 包内目录名取 doc_dir 实际目录名（真实撞名时是带摘要的
        # unique 名），与本地查找路径（doc_dir）保持一致
        arc_dir = os.path.basename(root.rstrip("\\/"))
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                entries.append((_DOC_ARC_DIR + arc_dir + "/" + rel, full))

    hda_files = {}    # 包内名 -> {"original": 磁盘文件名, "recipes": [内部名]}
    if include_hda:
        by_lib = {}   # realpath -> (磁盘路径, [内部名...])，保持首见顺序
        for r in recipes:
            lib = getattr(r, "library", "") or ""
            if lib and os.path.isfile(lib):
                by_lib.setdefault(_norm(lib), (lib, []))[1].append(r.name)
        used = set()
        for lib, rec_names in by_lib.values():
            base = os.path.basename(lib)
            arc, i = base, 2
            while arc.lower() in used:   # 不同库文件夹里的同名文件
                stem, ext = os.path.splitext(base)
                arc = "{}_{}{}".format(stem, i, ext)
                i += 1
            used.add(arc.lower())
            full_arc = _HDA_ARC_DIR + arc   # manifest 键 = 完整包内路径
            hda_files[full_arc] = {"original": base, "recipes": rec_names}
            entries.append((full_arc, lib))

    # 代码块：一卡一文件，包内路径镜像本地布局
    code_names = []
    for item in recipes:
        if not getattr(item, "is_code", False):
            continue
        src = codeblock.code_path(item.name, item.language)
        if not src.is_file():
            log.warning("export: code file missing for %s, skipped",
                        item.name)
            continue
        lang_dir = "python" if item.language == "python" else "vex"
        arc = _CODE_ARC_DIR + lang_dir + "/" + src.name
        entries.append((arc, str(src)))
        code_names.append(item.name)

    packaged = {
        "favorites": [n for n in raw["favorites"] if n in name_set],
        "tags": {n: list(v) for n, v in (raw["tags"] or {}).items()
                 if n in name_set and v},
        "thumbs": dict(thumbs_arc),
        "display_names": {n: v for n, v in
                          (raw["display_names"] or {}).items()
                          if n in name_set and v},
        "colors": {n: v for n, v in (raw["colors"] or {}).items()
                   if n in name_set and v},
    }
    manifest = {
        "format_version": FORMAT_VERSION,
        "app": APP_TAG,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "recipe_count": len(names),
        "recipes": names,
        "code_blocks": sorted(code_names),
        "include_hda": bool(include_hda),
        "hda_files": hda_files,
    }

    # ---- 写包：临时文件 → 成功后原子替换（取消/失败不产半截 zip） ----
    zip_path = os.path.abspath(zip_path)
    # 临时名带 PID+线程 id（同 _extract_member 约定）：固定名会被同盘
    # 并发写互踩
    tmp = "{}.~part{}_{}".format(zip_path, os.getpid(), threading.get_ident())
    total = len(entries) + 2
    done = 0
    ok = False
    try:
        parent = os.path.dirname(zip_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with zipfile.ZipFile(tmp, "w") as zf:
            if _report(progress, done, total, "打包元数据..."):
                raise TransferCancelled()
            zf.writestr(METADATA, json.dumps(packaged, indent=2,
                                             ensure_ascii=False))
            done += 1
            for arc, src in entries:
                if _report(progress, done, total, "打包 {}".format(arc)):
                    raise TransferCancelled()
                zf.write(src, arc, compress_type=_compress_for(arc))
                done += 1
            if _report(progress, done, total, "写入清单..."):
                raise TransferCancelled()
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2,
                                             ensure_ascii=False))
        os.replace(tmp, zip_path)
        ok = True
    finally:
        if not ok and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError as exc:
                log.warning("cannot remove partial export %s: %s", tmp, exc)
    return {
        "path": zip_path,
        "recipes": len(names),
        "code_blocks": len(code_names),
        "docs": sum(1 for a, _ in entries
                    if a.startswith(_DOC_ARC_DIR) and a.endswith("/doc.md")),
        "thumbs": len(thumbs_arc),
        "hda": len(hda_files),
    }


# --------------------------------------------------------------------------
# 导入
# --------------------------------------------------------------------------

def _open_zip(zip_path):
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise TransferError("无法读取 zip 包: {}".format(exc))
    if len(zf.infolist()) > MAX_MEMBERS:
        zf.close()
        raise TransferError("包内成员过多（超过 {} 个），疑似异常包，已拒绝"
                            .format(MAX_MEMBERS))
    return zf


def _read_manifest(zf):
    try:
        if zf.getinfo(MANIFEST).file_size > MAX_META_BYTES:
            raise TransferError("manifest.json 过大，疑似异常包")
        raw = zf.read(MANIFEST)
    except KeyError:
        raise TransferError("不是本工具导出的包（缺少 manifest.json）")
    except (zipfile.BadZipFile, zlib.error) as exc:
        raise TransferError("manifest.json 数据损坏: {}".format(exc))
    try:
        m = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        # RecursionError：深层嵌套 JSON 不是 ValueError 子类，漏了会以
        # 裸 traceback 逃出 import_package，违背"预期内失败一律
        # TransferError"的本模块契约
        raise TransferError("manifest.json 损坏: {}".format(exc))
    if not isinstance(m, dict) or m.get("app") != APP_TAG:
        raise TransferError("不是本工具导出的包")
    ver = m.get("format_version")
    if not isinstance(ver, int) or ver > FORMAT_VERSION:
        raise TransferError("导出包格式过新（v{}），请先升级 HouTools"
                            .format(ver))
    # 段级类型契约：recipes/hda_files 驱动导入决策，手改包写成数组/数字
    # 时不能让 TypeError 逃出"预期内失败一律 TransferError"（调用侧
    # 只捕 TransferError/OSError）
    if m.get("recipes") is not None \
            and not isinstance(m["recipes"], list):
        raise TransferError("manifest.json 的 recipes 段类型异常")
    if m.get("hda_files") is not None \
            and not isinstance(m["hda_files"], dict):
        raise TransferError("manifest.json 的 hda_files 段类型异常")
    return m


def _seg_dict(value, what):
    """元数据段级类型收敛：非 dict（手改/损坏包）按空处理并告警。

    `or {}` 只兜 falsy——非空 list/str 会让 .items() 抛 AttributeError
    逃出"预期内失败一律 TransferError"的本模块契约（调用侧只捕
    TransferError/OSError，异常从 Qt 槽穿出 = 用户看不到导入失败弹窗）。
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        log.warning("import: 包元数据段 %s 类型异常（%s），按空处理",
                    what, type(value).__name__)
        return {}
    return value


def _seg_list(value, what):
    """同 _seg_dict 的 list 版（favorites/recipes 等列表段）。"""
    if value is None:
        return []
    if not isinstance(value, list):
        log.warning("import: 包元数据段 %s 类型异常（%s），按空处理",
                    what, type(value).__name__)
        return []
    return value


def _read_packaged_metadata(zf):
    try:
        if zf.getinfo(METADATA).file_size > MAX_META_BYTES:
            raise TransferError("recipelib.json 过大，疑似异常包")
        raw = zf.read(METADATA)
    except KeyError:
        raise TransferError("包缺少 recipelib.json，可能已损坏")
    except (zipfile.BadZipFile, zlib.error) as exc:
        raise TransferError("recipelib.json 数据损坏: {}".format(exc))
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise TransferError("recipelib.json 损坏: {}".format(exc))
    if not isinstance(data, dict):
        raise TransferError("recipelib.json 内容异常")
    # 代码块条目收紧：只收"有非空代码、语言可识别"的记录（v1 旧包没有
    # 该键，按空处理）；语言不认识一律按 vex 收敛（metadata 同款容错）
    code_blocks = {}
    for k, v in _seg_dict(data.get("code_blocks"), "code_blocks").items():
        if not isinstance(v, dict):
            continue
        code = str(v.get("code") or "")
        if not code.strip():
            continue
        language = str(v.get("language") or "vex")
        if language not in ("vex", "python"):
            language = "vex"
        code_blocks[str(k)] = {
            "language": language,
            "code": code,
            "created": str(v.get("created") or ""),
            "modified": str(v.get("modified") or ""),
        }
    # tags 的值必须是 list：裸迭代会把字符串拆成逐字符标签（"abc" →
    # ["a","b","c"]）静默入库，数字直接 TypeError——一律跳过该键并告警
    tags_seg = {}
    for k, v in _seg_dict(data.get("tags"), "tags").items():
        if not isinstance(v, list):
            log.warning("import: tags[%r] 类型异常（%s），跳过该键",
                        k, type(v).__name__)
            continue
        if v:
            tags_seg[str(k)] = [str(t) for t in v]
    return {
        "favorites": [str(x) for x in _seg_list(data.get("favorites"),
                                                "favorites") if x],
        # 空列表条目不收：导入按"包内覆盖同名"合并，空标签会误删本地内容
        "tags": tags_seg,
        "thumbs": {str(k): str(v).replace("\\", "/")
                   for k, v in _seg_dict(data.get("thumbs"),
                                         "thumbs").items()
                   if v and isinstance(v, str)},
        "display_names": {str(k): str(v) for k, v in
                          _seg_dict(data.get("display_names"),
                                    "display_names").items() if v},
        "colors": {str(k): str(v) for k, v in
                   _seg_dict(data.get("colors"), "colors").items() if v},
        "code_blocks": code_blocks,
    }


def _doc_dirs_of(members):
    """包内含 doc.md 的文档目录名（safe 名）集合。

    目录名与叶子路径一样来自包内成员名，同在可信边界之外（包作者可
    任意伪造），必须过 _safe_rel 同款校验——否则 "docs/../doc.md" 会
    折出 ".."（target = DOCS_DIR 的上级 = 整个 settings/）、
    "docs//doc.md" 会折出空段（target = DOCS_DIR 本身），喂给
    rmtree 就是灾难。校验在读取阶段（inspect/import 共用）完成，
    非法包在确认对话框处就被拒绝。
    """
    dirs = set()
    for m in members:
        if not m.startswith(_DOC_ARC_DIR):
            continue
        rest = m[len(_DOC_ARC_DIR):]
        # 只认 docs/<safe>/doc.md 的**精确两段**结构：更深层成员（如
        # assets/doc.md）不是根文档——旧写法 endswith("/doc.md") 会把
        # "X/assets/doc.md" 判成文档目录 X，而 _doc_member_ok 又拒绝解出
        # 该成员，落一个空 incoming 整目录换入，把本地原文档顶掉
        parts = rest.split("/")
        if len(parts) != 2 or parts[1] != "doc.md":
            continue
        safe = parts[0]
        if "\\" in safe:
            # 反斜杠不是 zip 成员名的合法分隔符（zipfile 一律写 /）：
            # "docs/a\b/doc.md" 会被 _safe_rel 折成 a/b 两段过检，落盘
            # 却用原始串——校验口径与使用口径劈叉（Windows 上多出一层
            # 目录、文档成孤儿）。含反斜杠的目录名一律按非法拒绝
            raise TransferError("包内文档路径不合法: {}".format(m))
        try:
            _safe_rel(safe)
        except TransferError:
            raise TransferError("包内文档路径不合法: {}".format(m))
        dirs.add(safe)
    return sorted(dirs)


def _hda_plan(manifest, members, existing):
    """决策每个 .hda 包内文件：复制（无同名冲突）或跳过（含本地同名）。"""
    hda_import, hda_skip = [], []
    for arc, info in _seg_dict(manifest.get("hda_files"),
                               "hda_files").items():
        arc = str(arc)
        if arc not in members:
            continue
        if info is not None and not isinstance(info, dict):
            log.warning("import: manifest 的 hda_files[%r] 类型异常（%s），"
                        "跳过该文件", arc, type(info).__name__)
            continue
        rec = [str(n) for n in _seg_list((info or {}).get("recipes"),
                                         "hda_files[%s].recipes" % arc)]
        if any(n in existing for n in rec):
            hda_skip.append((arc, rec))
        else:
            # manifest 在可信边界之外：original 只取文件名（包作者可
            # 任意伪造），防 ../ 相对路径与绝对路径把解包目标写出库目录
            # 之外。裸 ".." / "." 的 basename 是其自身（不收敛），落盘
            # 目标会指到目录本体、以裸 OSError 而非 TransferError 失败
            # ——这类伪造名按非法拒绝
            original = str((info or {}).get("original") or arc)
            target = os.path.basename(original)
            if target in ("", ".", ".."):
                raise TransferError(
                    "manifest 的 hda 文件名不合法: {}".format(original))
            hda_import.append((arc, target))
    return hda_import, hda_skip


def _code_file_plan(members):
    """包内 vex_python_lib/ 下的代码文件 → [(rel, 语言)]（读取阶段校验，
    非法包在确认对话框处即被拒）。路径来自包成员（可信边界之外）：
    一律过 _safe_rel，落盘时再取 basename 双保险。"""
    plan = []
    for m in members:
        if not m.startswith(_CODE_ARC_DIR):
            continue
        rel = m[len(_CODE_ARC_DIR):]
        parts = [p for p in rel.replace("\\", "/").split("/") if p]
        if len(parts) != 2 or parts[0] not in metadata.CODE_LANGUAGES:
            continue
        lang = parts[0]
        if not parts[1].lower().endswith(codeblock.FILE_EXT[lang]):
            continue
        try:
            _safe_rel(rel)
        except TransferError:
            raise TransferError("包内代码路径不合法: {}".format(m))
        plan.append((rel, lang))
    return plan


def inspect_package(zip_path, existing_names=()):
    """读包摘要（导入确认对话框用）：数量统计 + 同名冲突预判，不落盘。"""
    existing = set(existing_names or ())
    with _open_zip(zip_path) as zf:
        manifest = _read_manifest(zf)
        meta = _read_packaged_metadata(zf)
        members = {i.filename for i in zf.infolist() if not i.is_dir()}
        hda_import, hda_skip = _hda_plan(manifest, members, existing)
        thumb_plan = _thumb_plan(meta, members)   # 非法缩略图在确认对话框处即被拒
        # 包内代码路径同批校验（docstring 承诺"非法包在确认对话框处即被
        # 拒"——不跑这条会推迟到导入中途才报错，用户已确认过一次）
        _code_file_plan(members)
    names = [str(n) for n in manifest.get("recipes") or []]
    try:
        size = os.path.getsize(zip_path)
    except OSError:
        size = 0
    return {
        "recipes": names,
        "conflicts": [n for n in names if n in existing],
        "code_blocks": [str(n) for n in manifest.get("code_blocks") or []],
        "docs": _doc_dirs_of(members),
        "thumbs": [n for n, _a, _e in thumb_plan],
        "hda_import": hda_import,
        "hda_skip": hda_skip,
        "size": size,
        "exported_at": str(manifest.get("exported_at") or ""),
    }


def _thumb_plan(meta, members):
    """包内 thumbs/ 成员 → [(内部名, arc, 扩展名)]（读取阶段校验，非法包
    在确认对话框处即被拒）。

    包内文件名来自导出方的 safe_name（不可信）：路径必须收在 thumbs/ 一级、
    扩展名必须收图片白名单，防任意内容解进 settings/recipe_thumbs/。
    """
    plan = []
    for name, arc in meta["thumbs"].items():
        if arc not in members:
            continue
        parts = arc.split("/")
        ext = os.path.splitext(arc)[1].lower()
        if (len(parts) != 2 or parts[0] != _THUMB_ARC_DIR.rstrip("/")
                or ext not in metadata.IMAGE_EXTS):
            raise TransferError("包内缩略图路径不合法: {}".format(arc))
        plan.append((str(name), arc, ext))
    return plan


def _doc_member_ok(rel):
    """docs/<safe>/ 内成员白名单：doc.md 与 assets/ 下的图片/视频。

    导出会原样打包用户文档目录里的全部文件，白名单外的成员跳过不拒
    （硬拒绝会误伤合法包）；媒体边界与 docs._on_anchor 的点击放行一致
    ——可执行内容即使混进包也不会被解出、更不会被文档链接拉起。
    """
    if rel == "doc.md":
        return True
    parts = rel.split("/")
    return (len(parts) == 2 and parts[0] == "assets"
            and os.path.splitext(parts[1])[1].lower()
            in metadata.IMAGE_EXTS + metadata.VIDEO_EXTS)


def _doc_target_map(doc_dirs, manifest):
    """包内文档目录名 → 本地落盘目录名（导入端的折名撞名防线）。

    v1 manifest 不带"目录 ↔ 配方名"映射，只能按 safe_name 反查：非撞名
    场景包内目录名 == safe_name(配方名)，唯一反查命中后改挂到本地的
    unique_safe_name（本地撞名时是摘要名，与本地 doc_dir 查找路径一致
    ——直用包内名会让文档登记不上，还可能整目录覆盖本地另一配方的
    文档）。反查不唯一/无命中（导出方撞名的摘要目录、配方不在 manifest
    里）保持原名；此时若 stems 登记显示目标目录属于本次导入之外的本地
    配方，拒绝该目录（整目录换入会清掉对方的文档）。
    """
    names = [str(n) for n in _seg_list(manifest.get("recipes"), "recipes")]
    mapping = {}
    docs_root = str(metadata.DOCS_DIR)
    for safe in doc_dirs:
        matches = [n for n in names if metadata.safe_name(n) == safe]
        local = (metadata.unique_safe_name(matches[0])
                 if len(matches) == 1 else safe)
        others = [o for o in metadata.stem_owners(local) if o not in names]
        if others:
            raise TransferError(
                "包内文档目录 {} 与本地配方 {} 的文档目录同名，"
                "为避免覆盖已拒绝导入".format(safe, "、".join(others)))
        if local == safe:
            # 二道防线（覆盖 stems 登记之外的存在）：反查不唯一/无命中时
            # 保持包内原名落盘，若该名在大小写不敏感盘上与 DOCS_DIR 里
            # 已有目录撞名（含仅大小写差异），换入会整目录顶掉对方文档
            # 且 .~old 备份被 rmtree——原文不可恢复
            try:
                existing_dirs = os.listdir(docs_root)
            except OSError:
                existing_dirs = []
            hit = [d for d in existing_dirs
                   if d != local
                   and os.path.normcase(d) == os.path.normcase(local)]
            if hit:
                raise TransferError(
                    "包内文档目录 {} 与本地文档目录 {} 同名，"
                    "为避免覆盖已拒绝导入".format(safe, "、".join(hit)))
        mapping[safe] = local
    return mapping


def import_package(zip_path, lib_dir=None, existing_names=(), progress=None):
    """把交换包合并导入当前环境，返回导入摘要 dict。

    - 元数据按内部名合并：包内覆盖同名，本地独有保留（最后一次性落盘，
      取消/失败不会留下改了一半的设置）
    - 缩略图：包内带图的名字，图按本地 unique_safe_name 重映射复制进
      settings 目录的 recipe_thumbs 并改写记录（存储形式 = 相对 settings
      目录，与 set_thumb_from_file 一致；防导出方折名撞名覆盖本地另一
      配方的图）
    - 文档：包内有 doc.md 的名字整目录替换本地（含 assets；目录名同样
      重映射，白名单外成员跳过）
    - .hda：只复制无同名冲突的文件到 lib_dir（不存在自动创建，重名
      文件自动加序号）；包里有可导文件却不给 lib_dir → 报错
    - 资源配额：成员数/单成员与累计解压体积/元数据大小超限即拒绝
    """
    existing = set(existing_names or ())
    with _open_zip(zip_path) as zf:
        manifest = _read_manifest(zf)
        meta = _read_packaged_metadata(zf)
        members = {i.filename: i for i in zf.infolist() if not i.is_dir()}
        hda_import, hda_skip = _hda_plan(manifest, members, existing)
        code_plan = _code_file_plan(members)
        legacy_blocks = meta.get("code_blocks") or {}
        thumb_plan = _thumb_plan(meta, members)
        doc_dirs = _doc_dirs_of(members)
        budget = [0]   # 累计解压字节（zip 配额，见 _extract_member）
        total = (len(thumb_plan) + len(doc_dirs) + len(hda_import)
                 + len(code_plan) + len(legacy_blocks) + 1)
        done = 0

        stems_before = metadata.stems_snapshot()
        committed = False
        # ---- 暂存/回滚账本：提交点（bulk_set 成功）之前不覆盖任何本地
        # 正式文件。缩略图/文档/代码/HDA 全部走暂存 + 旧文件备份，任何
        # 阶段取消/失败都在 finally 整体回滚——旧版文档在文档步就地换入、
        # 代码直接写正式名，后续阶段取消会让"导入已取消"的报告与磁盘
        # 状态劈叉且旧内容不可恢复
        pending_thumbs = []   # (暂存路径, 正式路径)
        thumb_staging = None
        doc_plans = []        # (incoming目录, 本地目标目录, 旧目录备份路径)
        code_plans = []       # (暂存文件, 正式目标文件)
        legacy_plans = []     # (stem, rec)——提交点才 save_code，防孤儿卡片
        code_staging = None   # 提交段创建；finally 清理按 None 守卫
        hda_copied = []       # 外层账本：后续阶段失败也要清掉本次复制的 hda
        doc_swaps = []        # 提交段换入成功后的回滚登记 (备份, 目标)
        thumb_swaps = []      # (旧文件备份, 目标, 是否有旧文件)
        code_swaps = []       # (旧文件备份, 目标, 是否有旧文件)
        legacy_created = []   # 提交段 save_code 新建的文件（回滚时删除）
        if thumb_plan:
            thumb_staging = (Path(metadata.THUMBS_DIR)
                             / (".~incoming%d_%d"
                                % (os.getpid(), threading.get_ident())))
            shutil.rmtree(str(thumb_staging), ignore_errors=True)
        try:
            # 先整批登记 stems（一次落盘）：包内名字尚未进本地元数据，逐个
            # unique_safe_name 探测不到批内撞名伙伴（曾双双落 plain、缩略图
            # 互相覆盖），批量登记在内存中互查后才落盘，兼消掉逐名落盘的
            # 写放大（数百名字 = 数百次整文件 tmp+replace）
            metadata.register_names(
                [str(n) for n in (manifest.get("recipes") or [])]
                + [n for n, _arc, _ext in thumb_plan])

            # ---- 1) 缩略图：按本地 unique_safe_name 重映射解到暂存 ----
            # 包内文件名来自导出方的 safe_name，与本地布局无关；直用包内名
            # 会在折名撞名时整文件覆盖本地另一配方的缩略图（导入端漏配
            # unique_safe_name 防线），本地撞名时摘要名也与查找路径一致。
            # 正式落盘推迟到提交点（见 bulk_set 之后），中途取消/失败只
            # 留暂存目录、由 finally 整体清掉
            new_thumbs = {}
            for name, arc, ext in thumb_plan:
                if _report(progress, done, total,
                           "导入缩略图 {}".format(name)):
                    raise TransferCancelled()
                dst = str(Path(metadata.THUMBS_DIR)
                          / (metadata.unique_safe_name(name) + ext))
                tmp = str(thumb_staging
                          / (metadata.unique_safe_name(name) + ext))
                _extract_member(zf, members[arc], tmp, budget)
                pending_thumbs.append((tmp, dst))
                new_thumbs[name] = metadata._to_stored(dst)
                done += 1

            # ---- 2) 文档：包内有的整目录替换（含 assets），没有的不动 ----
            # 先解到 <safe>.~incoming 临时目录、全部成员解完才换入：中途
            # 失败（包损坏/磁盘满等）不破坏本地已有文档——旧版"先删后写"
            # 会在解包失败时让本地文档凭空消失。取消检查在目录边界，换入的
            # 都是完整目录，取消后重跑仍然幂等。落盘目录名经 _doc_target_map
            # 重映射（见该函数 docstring）
            doc_map = _doc_target_map(doc_dirs, manifest)
            docs_root = Path(metadata.DOCS_DIR)
            root_abs = os.path.realpath(str(docs_root)) + os.sep
            # 包内成员按文档目录分桶一次：逐目录全量扫描 members 是
            # O(目录数×成员数)（成员上限 10000），全程主线程，目录一多
            # 进度框两次 setValue 之间都会卡顿
            doc_members = {}
            for arc in members:
                if not arc.startswith(_DOC_ARC_DIR):
                    continue
                safe, sep, _rest = arc[len(_DOC_ARC_DIR):].partition("/")
                if sep:   # 只收目录下的文件（"docs/safe" 无斜杠形态无主）
                    doc_members.setdefault(safe, []).append(arc)
            for safe in doc_dirs:
                if _report(progress, done, total, "导入文档 {}".format(safe)):
                    raise TransferCancelled()
                local = doc_map[safe]
                prefix = _DOC_ARC_DIR + safe + "/"
                target = docs_root / local
                # 二次防线：目录名已在 _doc_dirs_of 过检，这里再断言落点
                # 收敛在 DOCS_DIR 之内
                if not (os.path.realpath(str(target)) + os.sep).startswith(root_abs):
                    raise TransferError("包内文档路径不合法: {}".format(local))
                incoming = docs_root / (local + ".~incoming")
                shutil.rmtree(str(incoming), ignore_errors=True)
                incoming.mkdir(parents=True)
                try:
                    for arc in doc_members.get(safe, ()):
                        rel = arc[len(prefix):]
                        if not _doc_member_ok(rel):
                            log.warning("import: skip non-media doc "
                                        "member %s", arc)
                            continue
                        member_dst = incoming / _safe_rel(rel)
                        _extract_member(zf, members[arc], member_dst, budget)
                except Exception:
                    shutil.rmtree(str(incoming), ignore_errors=True)
                    raise
                # 只解暂存，不换入：文档换入挪到提交段（bulk_set 附近），
                # 旧目录备份到提交段才建——此段取消/失败本地文档零改动
                backup = docs_root / (local + ".~old%d_%d"
                                      % (os.getpid(), threading.get_ident()))
                doc_plans.append((incoming, target, backup))
                done += 1

            # ---- 3) .hda 库文件（导入后由浏览器 reload 自动安装） ----
            # 逐文件检查取消。复制清单挂在外层账本 hda_copied：后续代码/
            # 提交段取消/失败时由 finally 统一清除——它们尚未安装（安装
            # 走 reload），留着会让"取消后重跑"因目标名被占生成 _2 重复
            # 文件（同名定义装两份，行为不可预期）
            if hda_import:
                if not lib_dir:
                    raise TransferError("未指定 .hda 的导入目标文件夹")
                if _report(progress, done, total, "复制库文件..."):
                    raise TransferCancelled()
                os.makedirs(lib_dir, exist_ok=True)
                taken = {n.lower() for n in os.listdir(lib_dir)}
                for arc, original in hda_import:
                    if _report(progress, done, total,
                               "复制库文件 {}".format(original)):
                        raise TransferCancelled()
                    stem, ext = os.path.splitext(original)
                    dst, i = original, 2
                    while dst.lower() in taken:   # 与盘上已有文件重名
                        dst = "{}_{}{}".format(stem, i, ext)
                        i += 1
                    taken.add(dst.lower())
                    _extract_member(zf, members[arc], Path(lib_dir) / dst,
                                    budget)
                    hda_copied.append(dst)
                    done += 1

            # ---- 4) 代码块：文件解到暂存目录（提交点才覆盖正式名）+ 旧包
            # json 段记账（提交点才 save_code，防取消留孤儿卡片）。落盘
            # 目标只取 basename，包内成员名不可信 ----
            code_done = 0
            pending_rekeys = []   # 旧包 htcode::X 的元数据改挂，攒到提交段
            root_abs = os.path.realpath(str(codeblock.CODELIB_ROOT)) + os.sep
            code_staging = (Path(codeblock.CODELIB_ROOT)
                            / (".~incoming%d_%d"
                               % (os.getpid(), threading.get_ident())))
            shutil.rmtree(str(code_staging), ignore_errors=True)
            for rel, lang in code_plan:
                if _report(progress, done, total, "导入代码块 {}".format(rel)):
                    raise TransferCancelled()
                target = codeblock.code_dir(lang) / os.path.basename(rel)
                if not (os.path.realpath(str(target)) + os.sep).startswith(root_abs):
                    raise TransferError("包内代码路径不合法: {}".format(rel))
                staged = code_staging / lang / os.path.basename(rel)
                _extract_member(zf, members[_CODE_ARC_DIR + rel], staged,
                                budget)
                code_plans.append((staged, target))
                code_done += 1
                done += 1
            for name, rec in legacy_blocks.items():
                if _report(progress, done, total, "导入代码块 {}".format(name)):
                    raise TransferCancelled()
                stem = codeblock.free_stem(name.split("::")[-1] or name)
                legacy_plans.append((stem, rec))
                if stem != name:
                    pending_rekeys.append((name, stem))
                code_done += 1
                done += 1

            # ---- 5) 提交段（不可取消，无 _report）：文件换入 → 元数据
            # 落盘为真正提交点。任何换入失败都带着下面的回滚账本整体还原，
            # "导入已取消/失败"的报告与磁盘状态保持一致 ----
            # 5a. 文档换入（旧目录先备份）
            for incoming, target, backup in doc_plans:
                swapped = False
                try:
                    if target.exists():
                        os.rename(str(target), str(backup))
                    os.rename(str(incoming), str(target))
                    swapped = True
                finally:
                    if not swapped:
                        # 换入失败：旧目录原样还原，本地文档不受影响
                        if backup.exists() and not target.exists():
                            os.rename(str(backup), str(target))
                if swapped:
                    doc_swaps.append((backup, target))
            # 5b. 缩略图换入（旧文件先备份；单张失败回滚该张并继续——
            # 元数据尚未落盘，该名留空即旧版"导入缺图"语义）
            for tmp, dst in pending_thumbs:
                dst_path = Path(dst)
                dst_backup = dst_path.with_name(
                    dst_path.name + ".~old%d_%d"
                    % (os.getpid(), threading.get_ident()))
                had_old = dst_path.exists()
                if had_old:
                    os.replace(str(dst_path), str(dst_backup))
                try:
                    os.replace(tmp, dst)
                except OSError as exc:
                    if had_old:
                        os.replace(str(dst_backup), str(dst_path))
                    log.warning("import: cannot move thumb to %s: %s",
                                dst, exc)
                    continue
                thumb_swaps.append((dst_backup, dst_path, had_old))
            # 5c. 代码块换入（旧文件先备份；失败即整体中止走回滚）
            for staged, target in code_plans:
                # 全新安装首次导入时 vex/python 目录还不存在：旧版直接
                # _extract_member 会顺带建父目录，staging 换入路径必须
                # 自建
                target.parent.mkdir(parents=True, exist_ok=True)
                tbackup = target.with_name(
                    target.name + ".~old%d_%d"
                    % (os.getpid(), threading.get_ident()))
                had_old = target.exists()
                if had_old:
                    os.replace(str(target), str(tbackup))
                try:
                    os.replace(str(staged), str(target))
                except OSError:
                    if had_old:
                        os.replace(str(tbackup), str(target))
                    raise
                code_swaps.append((tbackup, target, had_old))
            # 5d. 旧包 json 代码块落盘（free_stem 新名，无覆盖；登记建档
            # 供回滚删除，防"取消后留下无元数据的孤儿卡片"）
            for stem, rec in legacy_plans:
                path = codeblock.save_code(
                    stem, rec.get("language", "vex"), rec.get("code", ""))
                legacy_created.append(Path(path))
            # 5e. 元数据合并，一次落盘——真正的提交点。落盘失败（盘满/
            # 只读/占用）时提交点未过：把内存合并值从磁盘重读丢弃后抛
            # 错走 finally 回滚，提交段已换入的文件（旧备份此时都还在）
            # 整体还原——否则"文件已换入、旧备份已清而元数据未落盘"
            # 的不一致不可恢复
            local = metadata.all_metadata()
            favs = list(local["favorites"])
            for n in meta["favorites"]:
                if n not in favs:
                    favs.append(n)
            merged_thumbs = dict(local["thumbs"])
            merged_thumbs.update(new_thumbs)
            tags = dict(local["tags"])
            tags.update(meta["tags"])
            display = dict(local["display_names"])
            display.update(meta["display_names"])
            colors = dict(local["colors"])
            colors.update(meta["colors"])
            if not metadata.bulk_set(favorites=favs, tags=tags,
                                     thumbs=merged_thumbs,
                                     display_names=display, colors=colors):
                metadata.reload_from_disk()
                raise TransferError(
                    "导入中止：元数据落盘失败（磁盘满/只读/文件占用），"
                    "已回滚导入的文件与元数据")
            committed = True   # 不可取消点已过：此后失败不再回滚 stems
                               # （register_doc_names/rekey 还会继续改设置，
                               # 此时回滚反与已合并的元数据失步）
            # ---- 已提交：清备份/暂存残件，登记文档与改挂元数据键 ----
            for backup, _target in doc_swaps:
                shutil.rmtree(str(backup), ignore_errors=True)
            for backup, _dst, _had in thumb_swaps:
                try:
                    backup.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("cannot remove thumb backup %s: %s",
                                backup, exc)
            for backup, _target, _had in code_swaps:
                try:
                    backup.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("cannot remove code backup %s: %s",
                                backup, exc)
            # 登记本次导入产生文档的名字（unique_safe_name 撞名检测的数据源；
            # 按 manifest 里的 recipe 名反查，导出端的包内目录名与本地
            # doc_dir 在非撞名场景下天然一致）
            metadata.register_doc_names(
                n for n in (str(x) for x in manifest.get("recipes") or [])
                if metadata.doc_exists(n))
            # 旧包代码块的元数据改挂放在"不可取消点"之后执行：rekey 立即
            # 改写 favorites/tags/...，循环中途取消会留下半迁移状态（与
            # "取消不改任何设置"的契约相悖）；且必须在 bulk_set 之后——包内
            # 元数据可能仍以旧键 htcode::X 携带条目，先 rekey 后合并会把
            # 旧键原样加回来
            for old, new in pending_rekeys:
                metadata.rekey_metadata_name(old, new)
        finally:
            # 暂存目录无论成败都清掉：成功路径文件已全部换入（目录已空），
            # 取消/失败路径残件不能留成缩略图/代码垃圾
            if thumb_staging is not None:
                shutil.rmtree(str(thumb_staging), ignore_errors=True)
            if code_staging is not None:
                shutil.rmtree(str(code_staging), ignore_errors=True)
            for incoming, _target, _backup in doc_plans:
                shutil.rmtree(str(incoming), ignore_errors=True)
            if not committed:
                # 失败/取消：把提交段已换入的文件整体还原到导入前状态
                # （尽力而为，单项失败留痕不抛——回滚中断比原始异常更难排）
                for backup, target in reversed(doc_swaps):
                    shutil.rmtree(str(target), ignore_errors=True)
                    if backup.exists():
                        try:
                            os.rename(str(backup), str(target))
                        except OSError as exc:
                            log.warning("cannot restore doc dir %s: %s",
                                        target, exc)
                for backup, dst, had_old in reversed(thumb_swaps):
                    try:
                        if had_old and backup.exists():
                            os.replace(str(backup), str(dst))
                        elif not had_old and dst.exists():
                            os.remove(str(dst))
                    except OSError as exc:
                        log.warning("cannot restore thumb %s: %s", dst, exc)
                for backup, target, had_old in reversed(code_swaps):
                    try:
                        if had_old and backup.exists():
                            os.replace(str(backup), str(target))
                        elif not had_old and target.exists():
                            os.remove(str(target))
                    except OSError as exc:
                        log.warning("cannot restore code file %s: %s",
                                    target, exc)
                for path in legacy_created:
                    try:
                        if path.exists():
                            os.remove(str(path))
                    except OSError as exc:
                        log.warning("cannot remove legacy code %s: %s",
                                    path, exc)
                if lib_dir:
                    for dst in hda_copied:
                        try:
                            p = Path(lib_dir) / dst
                            if p.exists():
                                os.remove(str(p))
                        except OSError as exc:
                            log.warning("cannot clean up copied hda %s: %s",
                                        dst, exc)
                # 回滚本次登记增量：导入的取消契约是"元数据落盘前不改任何
                # 设置"，而登记发生在文件搬运之前（缩略图落盘要靠它定名）
                metadata.restore_stems(stems_before)
    return {
        "metadata_names": len(set(meta["tags"]) | set(meta["display_names"])
                              | set(meta["colors"]) | set(new_thumbs)
                              | set(meta["favorites"])),
        "code_blocks": code_done,
        "docs": len(doc_dirs),
        "thumbs": len(new_thumbs),
        "hda_copied": hda_copied,
        "hda_skipped": hda_skip,
    }
