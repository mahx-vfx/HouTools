"""HouTools 热键:默认分配 + 用户自定义
=====================================
会话启动时（`python3.13libs/uiready.py` 调用 `install_defaults`）把
HouTools 菜单项的键位写进 Houdini 热键系统。

键位来源优先级:
  1. `settings/hotkeys.json` 里的用户自定义（主菜单 `HouTools Settings`
     窗口的快捷键区写入）—— 每次启动都会应用(addAssignment 覆盖);
  2. 用户在 Houdini Hotkey Manager 里的手动自定义 —— 已有键位时不覆盖;
  3. 内置默认值。

符号约定:NetworkViewMenu.xml 里 id 为 ``pane.wsheet.<name>`` 的
scriptItem,其热键符号为 ``h.pane.wsheet.<name>``(网络编辑器上下文)。

新增可改键三处同步:加常量 → 登记 DEFAULT_ASSIGNMENTS（uiready 安装）
→ 登记 ASSIGNABLE（设置窗口快捷键区按此渲染，smoke test 校验两者对齐）。
"""

from houtools.core.log import get_logger

logger = get_logger("core.hotkeys")

# ---- 各快捷键的 (context, 符号, 默认键) 常量 ----
PASTE_MERGE_CONTEXT = "h.pane.wsheet"
PASTE_MERGE_SYMBOL = "h.pane.wsheet.houtools_paste_as_object_merge"
PASTE_MERGE_DEFAULT_KEY = "Ctrl+Shift+V"

COPY_OUTPUT_CONTEXT = "h.pane.wsheet"
COPY_OUTPUT_SYMBOL = "h.pane.wsheet.houtools_copy_output_path"
COPY_OUTPUT_DEFAULT_KEY = "Ctrl+Shift+C"

# (context, hotkey_symbol, default_key) —— uiready install_defaults 逐条应用
DEFAULT_ASSIGNMENTS = (
    (PASTE_MERGE_CONTEXT, PASTE_MERGE_SYMBOL, PASTE_MERGE_DEFAULT_KEY),
    (COPY_OUTPUT_CONTEXT, COPY_OUTPUT_SYMBOL, COPY_OUTPUT_DEFAULT_KEY),
)

# HouTools Settings 窗口可改键登记表:名称 -> (显示名, context, 符号, 默认键)
ASSIGNABLE = {
    "paste_merge": ("Paste as Object Merge", PASTE_MERGE_CONTEXT,
                    PASTE_MERGE_SYMBOL, PASTE_MERGE_DEFAULT_KEY),
    "copy_output_path": ("Copy Output Path", COPY_OUTPUT_CONTEXT,
                         COPY_OUTPUT_SYMBOL, COPY_OUTPUT_DEFAULT_KEY),
}


def hotkey_current_key(name: str) -> str:
    """当前生效的键位描述(自定义 > 会话内分配 > 默认)。

    import hou 在 try 内：无头（smoke test）直接回退默认键位。
    """
    _display, context, symbol, default = ASSIGNABLE[name]
    custom = get_custom_key(symbol)
    if custom:
        return custom
    try:
        import hou

        assigned = hou.hotkeys.assignments(context, symbol)
        if assigned:
            return assigned[0]
    except Exception as exc:
        logger.debug("查询当前键位失败: %s", exc)
    return default


def _sole_assignment(context: str, symbol: str, key: str) -> bool:
    """把 key 设为该符号的唯一键位，返回是否成功。

    hou.hotkeys.addAssignment 是**追加**语义（HOM 文档示例对同一符号连
    绑 alt+k 与 shift+del 两键），只 add 不摘会让旧键残留生效——改键后
    默认键仍触发、「恢复默认」清不掉自定义键。先加新键（失败不动原状），
    再沿 assignments() 把其余键逐个 removeAssignment。

    查询/摘除失败一律返回 False：调用方据此回滚，不能虚报成功——旧键
    摘除失败时新键与旧键并存，UI 与持久化都显示新键，实际却有两个键
    都能触发。
    """
    import hou

    if not hou.hotkeys.addAssignment(context, symbol, key):
        return False
    try:
        current = list(hou.hotkeys.assignments(context, symbol))
    except Exception as exc:
        logger.debug("查询键位失败: %s", exc)
        return False
    ok = True
    for old in current:
        # 大小写不敏感比较：addAssignment 可能归一化键名大小写
        if str(old).lower() == key.lower():
            continue
        try:
            if not hou.hotkeys.removeAssignment(context, symbol, old):
                logger.debug("摘除旧键位 %s 返回失败", old)
                ok = False
        except Exception as exc:
            logger.debug("摘除旧键位 %s 失败: %s", old, exc)
            ok = False
    return ok


def hotkey_apply_key(name: str, key: str) -> bool:
    """把键位写入 Houdini 会话并持久化;返回是否成功。

    先落盘再改会话：持久化失败（配置目录不可建/只读共享盘等）直接
    False、会话不被改动——反过来的顺序曾让"会话已生效但 hotkeys.json
    没落盘"，重启回退、用户以为没生效且 UI 零反馈（save() 抛的异常
    从 Qt 槽穿出）。落盘成功但会话装失败时回滚为事务：恢复 previous
    自定义键（有的话），否则清掉记录；无 previous 不得清空——旧版
    无条件 clear_custom_key 会把用户原有的自定义配置删掉。

    注意 JsonStore 的 set 失败仍会保留内存改动（改动即存语义），所以
    previous 必须在改动前快照，恢复走显式 set，而不是依赖 store 自动
    回滚。
    """
    _display, context, symbol, _default = ASSIGNABLE[name]
    previous = get_custom_key(symbol)
    if not set_custom_key(symbol, key):
        return False
    if not _sole_assignment(context, symbol, key):
        # 会话里新键可能已半装上：尽力摘掉，再恢复持久化与键位的旧值
        try:
            import hou
            hou.hotkeys.removeAssignment(context, symbol, key)
        except Exception as exc:
            logger.debug("回滚新键位 %s 失败: %s", key, exc)
        if previous:
            if not set_custom_key(symbol, previous):
                logger.error(
                    "恢复旧自定义键位失败 symbol=%s previous=%s",
                    symbol, previous)
        else:
            clear_custom_key(symbol)
        return False
    return True


def hotkey_reset_default(name: str) -> bool:
    """清除自定义键位、恢复默认(会话内即时生效 + 删除持久化记录)。

    返回是否成功。注意:直接应用默认值(不走 install_defaults 的
    "已有键位不覆盖"分支)——用户明确要求恢复默认。同样先落盘
    （删记录）再改会话：旧顺序在落盘失败时 json 里旧自定义键还在，
    下次启动又被装回来——"恢复默认"被静默回滚。
    """
    _display, context, symbol, default = ASSIGNABLE[name]
    previous = get_custom_key(symbol)
    if not clear_custom_key(symbol):
        return False
    if not _sole_assignment(context, symbol, default):
        # 极小概率（默认键被 Houdini 拒绝）：恢复原自定义记录，
        # 不丢用户设置
        if previous:
            set_custom_key(symbol, previous)
        return False
    return True


_STORE = None


def _store():
    """热键自定义存储(懒加载,避免 import 期副作用)。"""
    global _STORE
    if _STORE is None:
        from houtools.core.settings import JsonStore

        _STORE = JsonStore("hotkeys.json", defaults={})
    return _STORE


def get_custom_key(symbol: str) -> str | None:
    """返回用户通过 HouTools Settings 设置的自定义键位。"""
    value = _store().get(symbol)
    return value if isinstance(value, str) and value.strip() else None


def set_custom_key(symbol: str, key: str) -> bool:
    """持久化用户自定义键位，返回落盘是否成功(JsonStore 自动落盘)。"""
    return _store().set(symbol, key)


def clear_custom_key(symbol: str) -> bool:
    """删除用户自定义键位(回退 Hotkey Manager 已有键位 / 默认值)，
    返回落盘是否成功。"""
    return _store().remove(symbol)


def install_defaults() -> list[str]:
    """分配所有默认键位,返回失败的条目描述列表(空 = 全部成功或已跳过)。"""
    import hou

    failed: list[str] = []
    for context, symbol, default_key in DEFAULT_ASSIGNMENTS:
        # key 先绑定默认值：get_custom_key 抛异常（如设置文件损坏）时
        # 异常分支才有东西可报，不会变成 UnboundLocalError 掩盖真因
            key = default_key
            try:
                custom = get_custom_key(symbol)
                key = custom or default_key
                if not custom:
                    # 无自定义时尊重 Houdini Hotkey Manager 的已有键位
                    try:
                        if hou.hotkeys.assignments(context, symbol):
                            continue  # 已有键位(含用户自定义),不覆盖
                    except Exception as e:
                        # 查询失败不阻塞分配,由 addAssignment 的返回值兜底
                        logger.debug("查询已有键位失败 %s: %s",
                                     symbol, e)

                if custom:
                    # 自定义键必须走 _sole_assignment：addAssignment 是
                    # 追加语义（见该函数 docstring），裸 add 会让默认键
                    # 残留并存——两个键都能触发、设置窗口显示与实际
                    # 生效集合不一致
                    if not _sole_assignment(context, symbol, custom):
                        failed.append(
                            f"{symbol} <- {custom}(符号未注册或键位无效)")
                elif not hou.hotkeys.addAssignment(context, symbol, key):
                    failed.append(f"{symbol} <- {key}(符号未注册或键位无效)")
            except Exception as e:
                failed.append(f"{symbol} <- {key}: {e}")

    if failed:
        logger.warning("默认键位分配失败: %s", "; ".join(failed))
    return failed
