# 节点右键菜单（OPmenu）

适用：仓库根 `OPmenu.xml` + `houtools/tools/open_cache_folder.py`、`capture_viewport_embed.py`、`delete_viewport_capture.py`（AGENTS.md「Where to Look ▸ 节点右键菜单」行的细节展开）。

## 机制与约定

- Houdini 沿 HOUDINI_PATH 合并同名 OPmenu.xml（包清单已把仓库根挂进 HOUDINI_PATH）。
- 条目 scriptCode 仍是两行分发器，`dispatcher.run(tool_id, kwargs)` 透传菜单 kwargs。
- **HouTools 自有条目约定：条目块以 `<separatorItem/>` 起头与系统项隔开**（官方文件同样把 separatorItem 与 context 条目相邻放置，悬空分割线由菜单系统自动收起）**+ 标签尾部 `[HT]` 后缀**。
- **标签一律英文/ASCII——H22 实测右键菜单中文 `<label>` 条目行出现但文字不渲染**（与顶栏菜单栏同款）。
- 结构改动需重启 Houdini（menurefresh 实测刷不出 OPmenu，2026-10 验证，旧说法"menurefresh 覆盖右键菜单"不成立）；scriptCode 引用的工具代码仍走 Reload 热加载（菜单每次点击时才 import 工具模块）。

## Open Cache Folder

- 右键带输出路径参数的节点：context expression 与工具内 `_OUTPUT_PARMS` **双处同步**——sopoutput=filecache::2.0/rop geometry/fbx、vm_picture=mantra、picture=karma、outputimage=USD Render；实测各参数名互不重叠、通常命中其一，`kwargs.get("node")` 防 None。
- 按 `_pick_output_path` 候选序求值（`parm.evalAsString()`，**没有 evaluatedString()**）；**参数值是表达式，先 `hou.text.expandString` 整体展开再判 isabs**——不展开会把 "$HIP/..." join 成 "工程/$HIP/..." 错路径。2026-11 实测（H22.0.429）：evalAsString 对**普通字符串值已做全展开**（含节点局部 $OS、帧号 $F4——mantra/filecache 默认值直出完整绝对路径），expandString 兜的是**表达式结果带原文**的形态（如 chs() 读回未展开文本）；另注 `$OS` 在节点上下文外 expandString 会展开成无关值，不能依赖全局展开处理节点局部变量。
- 展开 + 相对路径按 $HIP 拼接 + normpath 已抽成 **`expanded_output_path`** 共享层（Copy Output Path / Ctrl+Shift+C 复用同一取值，改展开逻辑两工具同步生效）；`resolve_folder` 在其上只做取目录 + 上溯存在祖先。注意 `unexpandedString()` 对带关键帧参数直接抛 OperationFailed（filecache::2.0 的 sopoutput 默认带帧表达式），取原文只能走 evalAsString。
- **两个工具的目录语义有意分叉**：`resolve_folder` 目录不存在时**上溯**最近存在祖先（打开资源管理器总能开个地方看）；Copy Output Path 的 `_output_folder` 只取 dirname 且**要求目录已存在**（不存在 = 红色错误通知、不复制——复制一个没写出来的目录没有意义，2026-11 用户指定）。改上溯逻辑只影响 Open Cache Folder，别顺手"统一"。
- **USD Render 特例**：outputimage 为空回退 loppath 指向节点的 picture（一般 lop/Karma Render Settings），非空永远 outputimage 优先。
- 取父目录 `os.startfile` 打开，目录不存在上溯最近存在祖先，真相对路径按 $HIP 拼接。
- 旧版 `filecache`（非 ::2.0）参数叫 `file` 不在候选内，无菜单。
- 内置 ROP 不是 HDA、`definition()` 为 None，无头探参数须实例化节点取 parmTemplateGroup。

## Capture Viewport to Node / Delete Viewport Capture

- Capture：所有节点可用（context expression 仅 `kwargs.get("node") is not None`）。
- Delete：仅带截图标记（`ht_capture_image`，与 MARKER_KEY 双处同步）的节点显示。
- 两者机制见 [capture-viewport.md](capture-viewport.md)。

## Paste Center

- **仅 SOP 节点显示**（2026-11 用户指定）：context expression 判
  `node.type().category().name() == "Sop"`，与 `paste_center.is_sop_target`
  **双处同步**（smoke test 假节点 type/category 链 eval 对照）。LOP/COP 的
  xform 映射工具侧保留（菜单不再露出，防误入非 SOP 网络）。
