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
- 按 `_pick_output_path` 候选序求值（`parm.evalAsString()`，**没有 evaluatedString()**）；**参数值是表达式，先 `hou.text.expandString` 整体展开再判 isabs**——evalAsString 不展开变量，不展开会把 "$HIP/..." join 成 "工程/$HIP/..." 错路径。
- **USD Render 特例**：outputimage 为空回退 loppath 指向节点的 picture（一般 lop/Karma Render Settings），非空永远 outputimage 优先。
- 取父目录 `os.startfile` 打开，目录不存在上溯最近存在祖先，真相对路径按 $HIP 拼接。
- 旧版 `filecache`（非 ::2.0）参数叫 `file` 不在候选内，无菜单。
- 内置 ROP 不是 HDA、`definition()` 为 None，无头探参数须实例化节点取 parmTemplateGroup。

## Capture Viewport to Node / Delete Viewport Capture

- Capture：所有节点可用（context expression 仅 `kwargs.get("node") is not None`）。
- Delete：仅带截图标记（`ht_capture_image`，与 MARKER_KEY 双处同步）的节点显示。
- 两者机制见 [capture-viewport.md](capture-viewport.md)。
