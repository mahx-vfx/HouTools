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
    """
    import hou

    if not hou.hotkeys.addAssignment(context, symbol, key):
        return False
    try:
        current = hou.hotkeys.assignments(context, symbol)
    except Exception as exc:
        logger.debug("查询键位失败，跳过旧绑定清理: %s", exc)
        return True
    for old in current:
        # 大小写不敏感比较：addAssignment 可能归一化键名大小写
        if str(old).lower() == key.lower():
            continue
        try:
            hou.hotkeys.removeAssignment(context, symbol, old)
        except Exception as exc:
            logger.debug("摘除旧键位 %s 失败: %s", old, exc)
    return True


def hotkey_apply_key(name: str, key: str) -> bool:
    """把键位写入 Houdini 会话并持久化;返回是否成功。"""
    _display, context, symbol, _default = ASSIGNABLE[name]
    if not _sole_assignment(context, symbol, key):
        return False
    set_custom_key(symbol, key)
    return True


def hotkey_reset_default(name: str) -> bool:
    """清除自定义键位、恢复默认(会话内即时生效 + 删除持久化记录)。

    返回是否成功。注意:直接应用默认值(不走 install_defaults 的
    "已有键位不覆盖"分支)——用户明确要求恢复默认。
    """
    _display, context, symbol, default = ASSIGNABLE[name]
    if not _sole_assignment(context, symbol, default):
        return False
    clear_custom_key(symbol)
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


def set_custom_key(symbol: str, key: str) -> None:
    """持久化用户自定义键位(JsonStore 自动落盘)。"""
    _store().set(symbol, key)


def clear_custom_key(symbol: str) -> None:
    """删除用户自定义键位(回退 Hotkey Manager 已有键位 / 默认值)。"""
    _store().remove(symbol)


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

            if not hou.hotkeys.addAssignment(context, symbol, key):
                failed.append(f"{symbol} <- {key}(符号未注册或键位无效)")
        except Exception as e:
            failed.append(f"{symbol} <- {key}: {e}")

    if failed:
        logger.warning("默认键位分配失败: %s", "; ".join(failed))
    return failed
