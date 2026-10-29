# 外部拖放导入 .abc/.fbx/.usd*

适用：`scripts/externaldragdrop.py` + `houtools/dragdrop.py`（AGENTS.md 同名行的细节展开）。

## 入口分发（脚本只做转发）

- 钩子文件被 Houdini 以 `exec(<stdin>)` 执行——**没有 `__file__`、每次 drop 即时执行（改码免重启）**，故分发器只做两行转发，逻辑在 `houtools.dragdrop`（吃 Reload 热加载）。
- `dropAccept` 返回 True=接管 / False=交还原生：无 .abc/.fbx/.usd*、非网络编辑器落点（保住"拖文件到参数框填路径"）、其他层级一律 False。
- **钩子内裸抛异常会让 Houdini 把拖入文件当 hip 文件打开（弹 "Unexpected end of .hip file" 错误框），drop_accept 必须全程兜底（日志+状态栏+return True）**。
- 拖放条目实测为纯路径字符串（正斜杠、含空格原样），file:// URL 形态仅作解码兜底。
- 官方 `$HFS/houdini/scripts/` **不自带** externaldragdrop.py（22.0.429 实查），项目钩子不存在遮蔽官方行为的问题；返回 False 时原生行为完全接管。

## .abc

- 拖到 obj 层级网络：鼠标处创建 `alembicarchive`（等价 File > Import > Alembic Scene...——官方菜单条目是 C++ actionItem 无脚本可抄）。
- **拖到 Sop 层级网络则建 SOP `alembic` 节点**（等价 Tab 菜单 alembic；坏文件由节点错误旗标反馈，创建本身不失败，无 failed 判定）。
- **`buildHierarchy` 是按钮参数而非 Python 方法（hou.ObjNode 无此方法），须 `parm("buildHierarchy").pressButton()`**；坏 abc 静默失败（不抛异常、不建子节点）按 children 数反馈。

## .fbx

- 拖到 obj 层级调官方 `hou.hipFile.importFBX`（等价 File > Import > Filmbox FBX...——官方菜单条目 `h.import_fbx` 是 C++ actionItem 无脚本可抄，该 API 文档自述等价菜单）。
- **恒定导入到 /obj 根**（无论 pwd），返回 `(subnet, messages)`，建完 `setPosition` 摆到落点；pwd 为嵌套 Object 网络时落点摆不到、留官方默认位置，状态栏如实报 /obj。
- **拖到 Sop 层级建 SOP `file` 节点**（parm 名是 `file` 非 fileName；file SOP 原生可读 .fbx，无头实测 cook 出几何）。
- importFBX 实测（H22.0.429 无头）：坏文件不抛异常、返回 `(None, 错误串)` 且不污染场景，文件缺失才 OperationFailed；不参与 undo（与官方导入一致，该分支不加 undo 组）；`suppress_save_prompt=True` 防拖放流中途弹保存模态框；subnet 命名/重名后缀由官方 API 自理。
- **官方导入默认给 file 节点加 `hou.nodeFlag.Lock` 冻结旗标（黄橙样式、参数只读）——对应导入对话框的 Unlock Geometry/Deformation File SOPs 两项，`importFBX(unlock_geometry=True, unlock_deformations=True)` 传参后导入即无锁（官方选项内建能力，不做事后遍历解锁；无头实测零锁节点、cook 正常）。**

## .usd/.usda/.usdc/.usdz（LOP 层级）

- 拖到 Lop 层级网络（/stage）：逐文件创建 `sublayer` 节点、文件路径填 `filepath1`（sublayer 默认只有这一槽文件参数，22.0.429 无头实查），等价 Tab 菜单 sublayer。
- **节点名取文件名主干**（与 abc/fbx 同一套 `_clean_name`）；中文等全非法名回退 `usd_import`（回退名按扩展名生成）。
- 坏文件由节点错误旗标反馈、创建本身不失败（与 SOP alembic/file 同语义，`succeeded=None`）；usdz 包可直接挂 sublayer。
- 整批一个 undo 槽（`Import USD Drag`）；复用 `_import_nodes`（纵向错开/per-file try/状态栏汇报全套既有行为）。
- **Object/SOP 层级拖 .usd 不接管**（分支不命中 → False 交还原生），避免吞掉原生行为；混拖（如 .abc+.usd 拖进 Lop）时只有命中的类型被导入、返回 True，未命中类型无原生兜底——混拖场景罕见，可接受。
- `createNode` 撞名去重是 Houdini 自带规则：**数字结尾的名字递增数字**（hero_v3 → hero_v4），非数字结尾才追加 _1（pack → pack1）——断言/排查时别按"_1 后缀"想当然。

## 通用

- 混合拖放各类型共用一套纵向错开序（`stack_from` 续位）；多文件纵向错开（按 `node.size()`）；abc/fbx/usd 各自整批单 undo 槽（SOP fbx file 节点分支独立 undo 槽）。
- 节点名须清洗：Houdini 只收 `[A-Za-z0-9._-]`，中文直接 OperationFailed，同批同名 createNode 自动加后缀；回退名/数字开头前缀按扩展名生成 `abc_import`/`fbx_123` 等，防 .fbx 得到 abc_* 误导名。
- `_import_nodes` 循环体 per-file try（一个坏文件不中止整批，与 FBX 分支的逐文件隔离一致）。
