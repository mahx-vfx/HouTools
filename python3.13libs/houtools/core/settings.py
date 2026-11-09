"""JSON file-backed settings storage."""

import json
import os
import threading

from houtools.core.constants import SETTINGS_DIR
from houtools.core.log import get_logger

log = get_logger("core.settings")


class JsonStore:
    """A single JSON file exposed as a dict.

    Usage: store = JsonStore("my_tool.json", defaults={"size": 100})
    Values are kept in memory; call save() (or use set(..., save=True),
    the default) to persist. Loaded values are merged over defaults, so
    adding new default keys never breaks existing settings files.
    """

    def __init__(self, filename, defaults=None):
        self.path = SETTINGS_DIR / filename
        self._defaults = dict(defaults or {})
        self._lock = threading.RLock()
        self._data = {}
        # 会话内改动过的键：save 时只把这些键合并到磁盘最新内容上——
        # 两个 Houdini 实例/同进程两个 Store 写同一文件的不同键时，后写
        # 者不再用整份旧快照覆盖对方刚写入的键（旧版整文件写回会丢更新）
        self._dirty = set()
        self.load()

    def load(self):
        with self._lock:
            # OSError 分支（text=None 跳过解析块）也要有值可合并：漏初始化
            # 会让 UnboundLocalError 在 import 期炸掉 metadata/hdrlight 等
            # 模块级 JsonStore（整个工具不可用）
            on_disk = {}
            disk_ok = True
            if self.path.exists():
                try:
                    text = self.path.read_text(encoding="utf-8")
                except OSError as exc:
                    # 读不了 ≠ 文件坏了（杀软/同步盘瞬时占用等）：不动
                    # 原文件，本次先用默认值，下次还能读到
                    log.warning("cannot read %s: %s", self.path, exc)
                    text = None
                    disk_ok = False   # 磁盘内容未知：现有键全标脏，save 全量重写
                if text is not None:
                    try:
                        on_disk = json.loads(text)
                        if not isinstance(on_disk, dict):
                            raise TypeError(
                                "顶层必须是 JSON 对象，实际是 "
                                + type(on_disk).__name__)
                    except Exception as exc:
                        # 解析失败或顶层不是对象：留底回退默认，防止下次
                        # save() 把尚可抢救的内容直接覆盖掉
                        log.warning("cannot read %s: %s", self.path, exc)
                        disk_ok = self._backup_corrupt()
                        on_disk = {}
            merged = dict(self._defaults)
            merged.update(on_disk)
            self._data = merged
            self._dirty = set() if disk_ok else set(self._data)

    def _backup_corrupt(self):
        """读不了的文件改名为 .bak 留底再回退默认，防止下次 save() 把
        尚可抢救的内容直接覆盖掉。返回是否留底成功——失败时写入方必须
        拒绝本次写入（原件还在原位，可能仍可人工抢救）。"""
        bak = self.path.with_name(self.path.name + ".bak")
        try:
            os.replace(self.path, bak)
            log.warning("corrupt settings file kept as %s", bak)
            return True
        except OSError as exc:
            log.warning("cannot back up corrupt %s: %s", self.path, exc)
            return False

    def _read_disk_locked(self):
        """save 前重读磁盘最新内容（持锁调用）。返回 (dict, ok)：
        ok=False 表示读不到或损坏且留底失败——本次写入必须拒绝，
        不能基于空合并基覆盖。"""
        on_disk = {}
        if self.path.exists():
            try:
                text = self.path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("cannot read %s: %s", self.path, exc)
                return None, False
            try:
                on_disk = json.loads(text)
                if not isinstance(on_disk, dict):
                    raise TypeError(
                        "顶层必须是 JSON 对象，实际是 "
                        + type(on_disk).__name__)
            except Exception as exc:
                log.warning("cannot read %s: %s", self.path, exc)
                if not self._backup_corrupt():
                    return None, False
                on_disk = {}
        return on_disk, True

    def save(self):
        """落盘；返回是否成功（目录不可建/盘满等只告警不抛——调用方拿
        bool 决定会话态是否可信，异常逃出去会让 Qt 槽零反馈）。

        写入是"磁盘最新内容 + 本会话脏键"的合并：只把改动过的键写回，
        其余键以磁盘为准——多实例/多进程写同一文件的不同键不再互相
        丢更新。save() 失败时磁盘不动、脏键保留（下次 save 重试），内存
        中已发生的编辑也不回滚——Automation 的"改动即存"语义要求编辑
        不因一次瞬时写盘失败从界面消失；需要"失败即回滚"的调用方（如
        热键应用）在调用侧自行保存 previous 并恢复。
        """
        with self._lock:
            tmp = self.path.with_name(
                self.path.name + ".~tmp%d_%d"
                % (os.getpid(), threading.get_ident()))
            try:
                # mkdir 也在保护内：配置目录被删/只读共享盘时 OSError
                # 不能逃出 save()（曾在 try 外，会把热键会话态与持久化
                # 态劈叉且 UI 零反馈）
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if not self._dirty:
                    # 无改动：文件已存在则不动（空写会把其他进程刚写入
                    # 的键冲回旧快照）；文件缺失时补建 defaults
                    if self.path.exists():
                        return True
                    merged = dict(self._data)
                else:
                    disk, ok = self._read_disk_locked()
                    if not ok:
                        return False
                    merged = dict(self._defaults)
                    merged.update(disk)
                    for key in self._dirty:
                        if key in self._data:
                            merged[key] = self._data[key]
                        else:
                            merged.pop(key, None)
                # 临时文件 + 原子替换：写盘中途崩溃/断电不会截断原文件
                tmp.write_text(
                    json.dumps(merged, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp, self.path)
                # 提交内存态：非脏键以磁盘为准刷新（拾取其他进程的改动）
                self._data = merged
                self._dirty.clear()
                return True
            except Exception as exc:
                log.error("cannot save %s: %s", self.path, exc)
                try:
                    tmp.unlink(missing_ok=True)
                except OSError as unlink_exc:
                    log.debug("cannot remove tmp %s: %s", tmp, unlink_exc)
                return False

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key, value, save=True):
        with self._lock:
            self._data[key] = value
            self._dirty.add(key)
            if save:
                return self.save()
            return True

    def remove(self, key, save=True):
        with self._lock:
            self._data.pop(key, None)
            self._dirty.add(key)
            if save:
                return self.save()
            return True

    def as_dict(self):
        with self._lock:
            return dict(self._data)
