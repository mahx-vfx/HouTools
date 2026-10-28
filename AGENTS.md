# HouTools Knowledge Base

**Houdini 22.0 (Python 3.13 / PySide6 6.8.3) 插件工具集** — 包机制加载 + 开发期手动热加载；兼容 Houdini 21.0 (Python 3.11)：`python3.11libs` 是提交进仓库的入口壳，用户拿发行包直装两版即用（见 Conventions「多版本单一源码」）。
环境事实：Houdini 22.0.429；`requests` 内置；`hffmpeg/hffprobe` 随 Houdini 附带（`$HFS/bin`）。

> **知识分工**：本文件只保留横切规则与指针；各功能的实现细节/逐条坑表在 `dev-notes/`（opmenu / capture-viewport / dragdrop / fonts / thumb-crop / thumb-snip / automation / videoseq / recipelib / codeblock / hdrlight）——表格行尾标注了对应笔记，**改该模块前必读**。

## Structure

```
root/
├── MainMenuCommon.xml             # Houdini 顶部菜单（薄分发器 scriptItem）
├── NetworkViewMenu.xml            # 网络编辑器面板菜单栏（HouTools 顶层菜单，注入机制同主菜单）
├── OPmenu.xml                     # 节点右键菜单（HOUDINI_PATH 同名合并；自有条目分割线+[HT] 后缀，标签英文）
├── HouTools.json                # 包清单副本（生效的一份在 Documents/houdini22.0/packages/）
├── python_panels/Automation.pypanel  # Automation 的 Python Panel 界面定义
├── python3.11libs/                # H21 (Python 3.11) 入口壳（提交进仓库、随发行包分发）：uiready.py 转发 + houtools/__init__.py 扩展 __path__——真身唯一在 python3.13libs，本目录严禁写业务代码（会漂移成第二份维护）
├── python3.13libs/uiready.py      # UI 启动钩子：装默认键位 + 启动即装 recipe 库。官方机制是"Houdini 执行 Houdini 路径上所有 uiready.py"（docs: Python script locations），各包同名钩子互不遮蔽、HFS 自带那份自己会跑——严禁在此补执行/链式其他 uiready.py（曾因此双执行官方钩子+扩大扫描面，已删）
├── python3.13libs/houtools/           # 核心 Python 包（python3.13libs 由 Houdini 自动加入 sys.path）
│   ├── dev/                       # ★ 热加载框架：reloader / dispatcher（永不参与重载）
│   ├── core/                      # constants（路径自算）/ log / settings(JsonStore)
│   ├── ui/                        # window_manager（工具窗口单例登记，Reload 前统一关闭）/ taskbar（Win32 任务栏常驻）
│   ├── automation/                # Automation：task_types / data_manager / execution_engine / styles / window
│   ├── videoseq/                  # 视频转序列图：ffmpeg 查找 + 窗口（拖放 + 可编辑路径框）
│   ├── hdrlight/                  # Hdr Library：HDR 库浏览 + 缩略图 + 双击赋给灯光
│   ├── recipelib/                 # Recipe Library：官方 recipes 资产库（网格浏览/标签/收藏/Markdown 文档/拖入网络创建）
│   ├── icons/                     # UI 图标（SVG，文件名不含空格）
│   ├── fonts/                     # 工具统一显示字体（阿里妈妈数黑体 Bold，ui/fonts.py 加载）
│   └── tools/                     # 工具入口：<tool_id>.py 暴露 run()
├── tests/smoke_test.py            # 无头回归：菜单 XML / 全包导入 / reload_all / TaskItem 往返 / 各窗口实例化（Automation/视频/HDR）
├── docs/
│   └── index.html                 # About 离线使用手册（用户向；about_shots/ 真实窗口截图）
├── dev-notes/                     # ★ 各功能实现细节与坑表（本文件表格巨行的拆分，改对应模块前必读；不入发行包）
└── settings/                      # 运行时生成的用户设置（gitignored）
```

## Where to Look

**行尾标注的 `dev-notes/<topic>.md` 是该功能完整机制与坑表，改对应模块前必读；其余行为全文。**

| Task | Location | Notes |
|------|----------|-------|
| 热加载机制 | `houtools/dev/reloader.py` | 按 sys.modules 插入序重载（=依赖序）；跳过 `houtools.dev*`；失败模块弹出 sys.modules 供下次重导 |
| 菜单点击入口 | `houtools/dev/dispatcher.py` | `run(tool_id)` → `importlib.import_module("houtools.tools." + tool_id)` → 调其 `run()` |
| 菜单定义 | `MainMenuCommon.xml` / `NetworkViewMenu.xml` | 顶层子菜单 id：`houtools_tools_menu` / `houtools_network_view_menu`；均插在 help_menu 前 |
| 节点右键菜单 | 仓库根 `OPmenu.xml` + `houtools/tools/open_cache_folder.py`、`capture_viewport_embed.py`、`delete_viewport_capture.py` | 条目块以 `<separatorItem/>` 起头与系统项隔开（悬空分割线由菜单系统自动收起）+ 标签尾部 `[HT]` 后缀；**标签一律英文/ASCII——中文 `<label>` 条目行出现但文字不渲染**；结构改动需重启 Houdini；scriptCode 只写两行分发器、工具代码走热加载；**context `<expression>` 只用最保守的 Python 子集（属性调用+比较+and/or，禁 walrus/推导式——H22 求值器语法面无官方承诺，smoke test 的 eval 对照才有意义）**。**Open Cache Folder 候选序/expandString/USD Render 特例/`_OUTPUT_PARMS` 双处同步等坑表 → `dev-notes/opmenu.md`（改前必读）** |
| 新增工具 | `houtools/tools/<tool_id>.py` + 两份菜单 XML | 模块暴露 `run()`；菜单 scriptCode 只写两行分发器 |
| 默认热键 | `houtools/core/hotkeys.py`（清单+自定义存储）+ `python3.13libs/uiready.py`（执行） | 菜单 item id 须为 `pane.wsheet.<name>` 前缀，热键符号才是 `h.pane.wsheet.<name>`；`settings/hotkeys.json` 的自定义键每次启动强制应用，其次尊重 Hotkey Manager 已有键位，最后落默认值 |
| 工具统一字体 | `houtools/ui/fonts.py` + `houtools/fonts/` | 阿里妈妈数黑体 Bold 私有加载（不装系统）；**Houdini 在应用层给控件类设了自家字体、压过 Qt 父子继承——必须逐控件显式 setFont 刷整棵子树 + QApplication ChildAdded 过滤器兜底（无头验证通过≠会话生效）**；卡片度量基准走 `_win.font()`；新字重往 `_FONT_FILES` 追加；详见 `dev-notes/fonts.md` |
| 粘贴为 Object Merge | `houtools/tools/paste_as_object_merge.py` | 网络编辑器菜单入口 / `Ctrl+Shift+V`（`Paste Hotkey Settings` 可改）；按"目标上下文×源类别"建引用（XXX=源名，颜色随源）：SOP→SOP / OBJ→SOP object_merge(Merge_XXX)；SOP→OBJ geo(XXX) 内 object_merge(Merge_XXX)；SOP→LOP / OBJ→LOP sopimport(SOP_XXX)；LOP→SOP lopimport(LOP_XXX)；LOP→LOP fetch(LOP_XXX)；LOP→ROP usdrender(XXX)；SOP→ROP fetch(SOP_XXX, source；源为 File Cache 时路径追加 /render)；SOP→DOP staticobject(Object_XXX)；SOP→COP(新COP) sopimport(SOP_XXX, usesoppath=1)；未列组合跳过；单 undo 槽。源路径靠 OS 剪贴板文本（内部剪贴板无法反查原件），SOP 网络文本失效时 `pasteItemsFromClipboard` 粘贴副本兜底 |
| 视窗截图嵌入（Capture Viewport to Node / Delete Viewport Capture） | `houtools/tools/capture_viewport_embed.py`（主逻辑）+ `delete_viewport_capture.py`（薄入口，run 内懒 import 主模块防 reload 依赖序颠倒）+ OPmenu 条目 | 右键截图 → JPG 落 `$HIP/HouTools_cfg/captures/` → 网络背景图钉在节点旁（`setRelativeToPath` 锚定，**只存路径引用不进 hip**）；**marker（userData）是不可信输入（分享的 hip 可伪造）——喂 os.remove/copyfile 前必须过 `_is_valid_marker`（纯 basename + .jpg），穿越形态直接拒绝**；**flipbook 设置必须 stash()、落库必须调官方 nodegraphutils.saveBackgroundImages、重截刷新=官方清空重放手法（严禁 setPwd 层级往返）、节点删除只清条目不删文件、改名跟随/死锚点清理——全表 → `dev-notes/capture-viewport.md`（改前必读）** |
| 粘贴边界框 | `houtools/tools/paste_bbox.py` + `paste_bbox_absolute.py`/`paste_bbox_expressions.py` 薄入口 + OPmenu.xml 子菜单 | 右键目标节点把剪贴板源节点的包围盒尺寸/中心贴到 size/t（sizex/y/z←bbox、tx/y/z←centroid）；**context expression 与 `is_bbox_target` 双处同步、必须 all() 两元组各三分量**（grid 的 size 二向量，any() 会凭 t 三分量误命中；smoke test 用假节点直测工具函数与 eval XML 表达式对照）；源解析懒 import 复用 paste_as_object_merge 的 `_clipboard_node_paths`（点击时才加载，防 reload 旧引用，同 delete_viewport_capture 手法）；绝对值走 `hou.hscriptExpression`（与表达式模式同一套函数，两模式结果天然一致），**写入前必须 `deleteAllKeyframes()`——实测 `parm.set()` 不覆盖已有表达式/关键帧**（表达式存活继续生效；`setExpression` 以单表达式键帧整体替换；对干净参数 deleteAllKeyframes 是无害 no-op）；cook 出错源上 hscriptExpression **不抛异常静默返 0**，先 `src.errors()` 预检；锁定参数 set/setExpression 抛 PermissionError，逐分量 `isLocked()` 预检跳过+状态栏提示；同节点守卫；表达式路径同网络 `relativePathTo`（`../A`）、跨层级绝对路径、显式 Hscript 语言；单 undo 槽；**逐分量写入各自捕 PermissionError 记入跳过列表**（isLocked() 预检只查参数级锁，节点级/定义级锁要在 set 时才抛——不逐分量兜住会留下"size 写了一半 t 没写"的半应用状态） |
| 外部拖放导入 .abc/.fbx | `scripts/externaldragdrop.py` + `houtools/dragdrop.py` | 钩子被 `exec(<stdin>)` 执行（无 `__file__`、每次 drop 即时执行），逻辑全在 `houtools.dragdrop` 吃热加载；**`drop_accept` 必须全程兜底（裸抛异常=拖入文件被当 hip 文件打开）**；非网络编辑器落点/其他层级一律 False 交还原生。**importFBX/alembicarchive/buildHierarchy 细节与坑表 → `dev-notes/dragdrop.md`（改前必读）** |
| 快捷键设置 | `houtools/tools/paste_hotkey_settings.py` | 捕获式改键 → `hou.hotkeys.addAssignment` 会话即时生效 + `settings/hotkeys.json` 持久化；经 `window_manager` 单例 |
| About 页面 | `houtools/tools/about_houtools.py` + `docs/index.html` | 主菜单 About HouTools → `os.startfile` 用默认浏览器打开离线使用手册（全局性条目，仅注册主菜单不进 NetworkViewMenu）；**零外部网络资源**（样式/JS 内联、系统字体栈、不引 CDN——无互联网工作站），配图 `docs/about_shots/` 是**真实窗口截图**，由 `tests/gen_about_shots.py` offscreen 离屏生成（QApplication 暗色 QPalette + 打桩演示数据 + 真 hoiiotool 缩略图；窗口 UI 改动后重跑该脚本再生成），smoke test 固化"无外部 http 引用"断言防退化；轮播 = 原生 JS（自动播放/hover 暂停/箭头/圆点/触摸滑动），Object Merge 与 Alembic 两张暂为内联 SVG 示意（网络编辑器场景离屏截不了）；页面内容从 README 工具小节提炼，**条目名照抄工具内实际选项文字（如「打开DW」）**，改工具时 README 与 index.html 双处同步；路径走 `constants.PROJECT_ROOT`（不依赖 cwd），页面缺失时状态栏 Error 提示不抛异常 |
| 窗口单例 | `houtools/ui/window_manager.py` | `open_window(tool_id, factory)`；close_all 供 Reload 前调用。**close_all 只对 close() 接受的窗口 deleteLater**；closeEvent 被 ignore 且窗口声明 `is_close_deferred()` 为真（如 videoseq 转换进行中）时跳过 deleteLater、窗口引用移入 `_closing` 集合防 GC 析构运行中的 QThread（Qt6 qFatal），由窗口在延迟关闭收尾时**自行 deleteLater**（destroyed 信号自动摘除集合条目）。**`_closing` 必须活过 Reload**：本模块会被 Reload Modules 重载，模块体重跑会把绑定换成新空集合、旧集合随之失去唯一外部根（运行中的延迟关闭窗口可被 GC 析构）——用 `try: _closing / except NameError: _closing = set()` 守卫（reload 在同一 module dict 里执行、已有名字可见，重载后沿用旧集合）；全仓模块级"运行中对象登记表"（automation `_LIVE_ENGINES`、HDR `_LIVE_THREADS`）同款守卫 |
| 缩略图裁剪 | `houtools/recipelib/crop.py` | ThumbCropDialog 头像式框选（选框锁缩略图区比例 1:0.66），确认返回原图分辨率 QPixmap；静态图先弹裁剪再按**源图格式**存（`metadata.thumb_format_for_source`，换格式旧文件 `_clean_stale_thumbs` 清理）、GIF 跳过裁剪原样复制；**QPen 静默不可见坑/像素断言/裸 python 编解码插件坑 → `dev-notes/thumb-crop.md`（改前必读）** |
| 截取缩略图（屏幕框选，多显示器） | `houtools/recipelib/capture.py` + browser `_snip_thumb` 系 | 多屏 SnipOverlay 会话（每屏一块遮罩铺冻结抓屏）：共享选框存**虚拟桌面坐标**、跨屏必须用 `globalPosition()`（Qt 隐式抓取下本地坐标不可信）、确认按屏 DPR 换算回物理像素（跨屏重采样拼合）；交互对齐 ThumbCropDialog + ✓/✗ 按钮条（Enter/Esc 同语义）；收尾先压 `_need_reload` 再 show、先落库再刷 UI；确认存 JPEG q90（用户指定）。**全表 → `dev-notes/thumb-snip.md`（改前必读）** |
| 对话框按钮中文化 | `houtools/ui/dialogs.py` | `localize_buttons(dialog)` 把 QDialogButtonBox 标准按钮改中文（确认/取消/关闭）；`warn()` 是中文化版 QMessageBox.warning。Qt 按钮文字跟平台主题走（英文系统出英文按钮），必须实例化对话框调用（QInputDialog.getText / QMessageBox.question 等静态便利函数拿不到内部按钮）；QColorDialog 还要 `DontUseNativeDialog` 选项，加自定义按钮走 `findChild(QDialogButtonBox).addButton(widget, ActionRole)`（QColorDialog 没有 addButton API） |
| 任务栏常驻 | `houtools/ui/taskbar.py` | `apply_appwindow_flags(window)`：Win32 `WS_EX_APPWINDOW`（Get+OR，保留原扩展样式位），失败走 debug 日志；独立工具窗口（videoseq / Hdr Library）构造时调用 |
| 项目路径 | `houtools/core/constants.py` | `PROJECT_ROOT = Path(__file__).parents[3]`，不依赖 cwd |
| 日志 | `houtools/core/log.py` | `get_logger("tools.xxx")`；handler 全局只配置一次，重载安全 |
| JSON 设置 | `houtools/core/settings.py` | `JsonStore(filename, defaults)`，defaults 合并语义，存项目 `settings/`；**save 为临时文件 + os.replace 原子替换**（临时名带 PID+线程 id——固定名会被同盘并发写互踩）；load 失败时坏文件改存 `.bak` 留底再回退 defaults（防下次 save 把可抢救内容覆盖掉），**"顶层不是对象"（null/数组/字符串的合法 JSON）也走同一条留底路径**——曾让 TypeError 从 load() 逃逸且不留 .bak（metadata/hdrlight 在 import 期构造 JsonStore，逃逸=整个工具不可用）；OSError（杀软/同步盘瞬时占用）只告警不挪原文件；另提供 `remove(key)` |
| 自动化任务模型 | `houtools/automation/task_types.py` | TaskType 枚举 + dataclass 参数 + TaskItem.to_dict/from_dict；**from_dict 与 data_manager.deserialize_params 同容错语义**（params 缺字段走默认值，缺 type 才报错——load_tasks 公开 API 直接调它，硬下标会把手改坏的配置文件变成 KeyError）；**读档入口布尔/字符串字段经 `as_bool`/`str()` 归一**——裸 bool("false") 是 truthy，会把禁用任务反转成启用并在"加载—保存"往返中固化 |
| 自动化持久化 | `houtools/automation/data_manager.py` | 按 `$HIP/HouTools_cfg/Automation_json/` 存取（跟场景走，是有意设计）；多配置文件；写入为临时文件 + os.replace 原子替换（临时名带 PID+线程 id，防同盘并发写互踩），损坏 JSON 改存 `.bak` 再回退；`$HIP` 取不到落 `%TEMP%` 时 warning 一次；空字符串 dw_exe_path 一律视为未配置 |
| 自动化执行引擎 | `houtools/automation/execution_engine.py` | QThread；Houdini API 经 `hdefereval.executeDeferred` + Event 同步派发主线程；**无超时同步等待是有意设计、禁止加超时**（原因与取消语义见 Conventions「Automation 的同步等待」）；dl_Submit 点击前强制 `hipFile.save()`（防保存对话框阻塞无人值守队列）、flipbook 设置必须 stash() 复制后再改（直接改活对象会留脏用户设置）；细节 → `dev-notes/automation.md` |
| 界面接入（Python Panel） | `houtools/automation/window.py` | `create_panel_widget` / `open_floating_panel`（`hou.pypanel.installFile` + `createFloatingPanel`）；运行态收尾单点归 `_on_all_completed`（closeEvent 只请求取消，**不复位 `_running`、不还原 stdout**——取消是协作式的，在途任务日志要连续到引擎收尾，pane 子部件收不到 close）；**stdout 重定向生命周期必须不依赖面板存活（destroyed 连模块级兜底恢复，连接只在 `__init__` 建一次）**；细节 → `dev-notes/automation.md` |
| ffmpeg 查找 | `houtools/videoseq/ffmpeg.py` | 优先级：项目根 `ffmpeg.exe` → `$HFS/bin/hffmpeg` → `$HFS/bin/ffmpeg` → PATH（hffmpeg 优先）；`find_ffprobe` 从选中 ffmpeg 的**同目录**推导（含 hffmpeg→hffprobe 名字映射，通用名 ffprobe 优先于映射），同目录没有时回退 `$HFS/bin` → PATH 的 hffprobe/ffprobe——项目根只放 ffmpeg.exe 的场景探测仍走真 ffprobe（读容器元数据对构建版本不敏感），不再退回"正则解析 ffmpeg -i 输出" |
| 视频拖放/路径框 | `houtools/videoseq/window.py` | 整窗+`_VideoSourceGroup` 双层拖放、`_load_video` 共用入口；**子进程管道必须有人读（stdout progress 无人读会挂死→隐藏窗口永不关闭）、stderr 用 DEVNULL、text=True 一律 utf-8+replace（cp936 会炸）**；取消协作式、语义对齐 Automation「停止中…」（按钮禁用到 worker 收尾信号到达，`_retired_workers` 保引用防 GC）；分辨率宽/高两框 + 预设下拉；**提取 worker 在 `_on_convert` 里也 `_retire_worker` 登记进 `_LIVE_WORKERS`，转换中 `is_close_deferred` 必须为 True（否则 Reload 的 close_all deleteLater 掉保着运行线程唯一引用的窗口 = qFatal）；输出路径（含目录）禁 `%`（image2 把整条路径当 printf 模板）；同前缀旧序列改名 `.bak` 让位——成功才删、取消/失败还原**；完成后写相机 `vm_background`+`vm_bgenable=1`（装饰性步骤失败不绑架主流程）。**相机查找/showEvent 刷新/取消细节 → `dev-notes/videoseq.md`（改前必读）** |
| Recipe Library（recipes 资产库） | `houtools/recipelib/`（store/metadata/docs/capture/browser/transfer）+ `tools/recipe_library.py` | 基于 H22 官方 recipes 的资产面板，**只加载用户库**（`settings/recipelib.json` 的 `lib_dirs`）；元数据/缩略图/文档全在 `settings/` 与库文件解耦；**卡片右对齐+格宽恒定、卡间距 2×GAP=8px 是用户多轮拍板的最终形态（勿改回自适应/居中）**；**原 1.2 万字符机制与坑表拆至 `dev-notes/recipelib.md` 与 `dev-notes/codeblock.md`（改前必读：扫描防护/侧栏三保险/卡片绘制/预览/文档/应用闸/追加写入三态预检/transfer 防穿越/safe_name 撞名/H21 枚举特征检测）** |
| HDR 库浏览器 | `houtools/hdrlight/browser.py` + `tools/hdr_library.py` | 菜单 Hdr Library；递归扫 `.hdr/.hdri/.exr`、一级子文件夹=分类；双击赋给选中灯光（envlight/RenderMan dome/LOP domelight 全家族，`addSelectionCallback` 推送驱动）；后台 QThread 线程池调 hoiiotool 出缩略图（运行线程挂 `_LIVE_THREADS`）；网格刚体不随面板宽伸缩（用户指定）。**hoiiotool 缓存/OCIO/赋灯参数名/新建分类校验 → `dev-notes/hdrlight.md`（改前必读）** |

## Conventions

- **新增工具三步**：`houtools/tools/<tool_id>.py`（暴露 `run()`）→ 两份菜单 XML 各加 `scriptItem`（scriptCode 仅 `import houtools.dev.dispatcher` + `_houtools_dispatcher.run("<tool_id>")` 两行）→ 重启 Houdini 一次让菜单出现；此后该工具代码迭代全部走 Reload 热加载。
- **菜单栏标签一律英文/ASCII**（H22 菜单栏对中文渲染不可靠；右键菜单 OPmenu 同款——中文 label 条目行出现但文字不渲染）；工具窗口内部 UI 可用中文。
- **顶层窗口与 Python Panel**：独立 QDialog 必须经 `window_manager.open_window()` 创建（如视频转序列图），否则 Reload 无法自动关闭旧窗口；Automation 是 Python Panel 部件（pane 托管），**不得**注册进 window_manager。
- **Reload 与 pypanel**：Reload Modules 不重建已打开的 Python Panel（避免丢弃未 Start 保存的编辑），由用户点面板工具条自带的刷新按钮重建界面。
- **`import hou` 只放函数内**或 try/except，保证模块在 Houdini 外可导入（冒烟测试依赖这一点）。
- **Houdini MCP（fxhoudini）使用协议**：需要连 GUI 会话前，先向用户说明本次要在 Houdini 里做什么，等用户手动打开 MCP 再动；GUI 内探针一律小步拆分、先无头（hython + 隔离 `HOUDINI_USER_PREF_DIR`）验证，把会话里长/重的操作（HDA destroy、全量 rescan 等）放在无头环境做——曾有 destroy 链路把 GUI 会话搞崩的先例。测试用节点/配方用完即清，用户设置（lib_dirs 等）测前测后保持一致。
- **线程**：QThread + Signal；任何回改 Houdini 的调用经 `hdefereval.executeDeferred` 派发主线程（参考 `execution_engine._run_deferred`）。
- **Automation 的同步等待是有意设计，勿"修复"**：引擎对主线程调用做**无超时**阻塞等待（`execution_engine._run_deferred` 的 `ready.wait()`）。本工具本质是逐个触发 Houdini 按钮，顺序执行的唯一保证是"上一任务的主线程调用返回后再派发下一个"——主线程被解算/缓存阻塞多久就等多久（数小时是正常值），加超时会在任务实际完成前放行下一任务、破坏顺序语义；模态对话框等阻塞在本语义下同样属于"当前任务未完成，继续等"。取消是协作式，仅在任务边界生效、在途任务不可打断；因此 UI（`window._cancel_execution`）**不立即复位**：按钮保持「停止中…」并禁用，直到引擎真正发出 `all_completed` 才回空闲——否则留下"假取消"窗口，期间二次 Start 会被旧引擎迟到的 `all_completed` 打翻新引擎状态。运行中引擎由 `window._LIVE_ENGINES` 模块级留引用（引擎无 parent），防 GC 销毁运行中的 QThread。已知会弹阻塞对话框的按钮逐案消除弹框根源（`dl_Submit` 点击前强制存盘），不改等待机制。
- **菜单结构改动**（增删菜单项）需重启 Houdini——H22 硬约束；顶栏菜单与节点右键菜单 OPmenu 实测均如此（2026-10：menurefresh 刷不出 OPmenu 改动，旧说法"menurefresh 覆盖 OPmenu/PARMmenu 等右键菜单"对 OPmenu 不成立，PARMmenu 未复验）；右键菜单条目背后的工具代码仍走 Reload 热加载。
- **reload 语义**：`houtools/dev` 自身永不重载（改 dev 框架需重启）；重载前自动 close 所有登记窗口；状态反馈走 `hou.ui.setStatusMessage`（`houtools.dev.status`）。
- **多版本单一源码**：`python3.13libs` 是唯一真身；`python3.11libs`（H21/py3.11 入口壳，随发行包分发）只含 uiready 转发与 `houtools/__init__.py` 的 `__path__` 扩展。Houdini 只加载/执行自己版本对应的 pythonX.Ylibs（两版互不可见对方目录，各跑一份 uiready 不双跑）；版本差异（如 H21 缺 `RecipeCategory.data`）一律在真身里用能力探测（`getattr(..., None)`）消化，**禁止往壳目录复制业务代码**（会漂移成第二份维护）。
- **二进制不入库**：`*.exe` 已被 .gitignore 排除。
- **提交**：git 仓库在 gitcode（`mahx-vfx/HouTools`，SSH 远程），main 分支；无构建步骤，纯 Python 即发布。

## Anti-Patterns (This Project)

- **模块级 `_window` 单例**：必须用 `window_manager`（它处理已删除 C++ 对象的 RuntimeError 重建）。
- **模块级可变容器（登记表/缓存/引用位）必须活过热重载**：可重载模块的模块级登记表会被 reload 重绑成新空容器、旧容器失去引用根——用 `try: _X / except NameError: _X = 初始值` 守卫（reload 在同一 module dict 里执行、已有名字可见），参照 `window_manager._closing`/`_LIVE_ENGINES`/HDR `_LIVE_THREADS`；或挂 QApplication 动态属性（`ui/fonts.py` 手法）。
- **凡是喂给 `rmtree`/`os.remove` 的路径，任一段来自不可信输入（zip 成员名、manifest 字段、用户输入）必须先校验/收敛**：`transfer._hda_plan` 的 basename、`_doc_dirs_of` 的 `_safe_rel` 校验、`delete_recipe` 的尺寸护栏——"读不到 = 已空"的宽 except + 直接删文件，一次瞬时读取失败就能毁掉装着几十个 recipe 的库文件。
- **`houtools/dev` 内 import `houtools.core/ui/tools`**：dev 框架必须独立于被重载代码，否则持旧引用。
- **import 期副作用**：包 `__init__` 保持薄，重活交给 dispatcher 懒加载——否则热加载会重放副作用。
- **静默异常**：禁止无日志的 `except: pass`；捕获后至少 `log.warning`。
- **把业务逻辑写进菜单 scriptCode**：只允许两行分发器；逻辑一律进 `houtools/tools/` 与模块。
- **在 reload 顺序上做假设**：新增模块间依赖时保持"先被 import 的是依赖"，插入序重载才成立；避免模块级循环依赖。
- **给 `execution_engine._run_deferred` 的 `ready.wait()` 加超时**：那是顺序执行语义本身，不是缺陷（见 Conventions「Automation 的同步等待」）；"Stop 按了没立刻停"同理——取消只在任务边界生效，UI 会如实显示「停止中…」。

## Unique Styles

- **暗色主题**：主色 `#18181b`/`#1D1D20`/`#2d2d2d`，强调 `#0d6399`(蓝)/`#8a5cf5`(紫)，错误 `#d1283e`；各模块 styles 常量共享这套配色。
- **配置语义**（Automation）：JSON 只在点 Start 时落盘 = "用户决定执行的任务"，关窗不保存是有意为之。
- **长驻窗口进 Houdini 任务栏**：Win32 `WS_EX_APPWINDOW`，统一走 `houtools/ui/taskbar.py`（Get+OR 保留原样式位；不做进程级 AppUserModelID——那会连 Houdini 主窗的任务栏分组一起改），失败静默。

## Commands

```
# 无头回归测试（不需要打开 Houdini；解释器路径按实际安装盘，测试脚本自带 C:/D: 探测）
"D:\Program Files\Side Effects Software\Houdini 22.0.429\python313\python.exe" tests\smoke_test.py

# 提交推送
git add -A && git commit -m "..." && git push
```

（无构建/打包步骤；热加载验证 = 改代码 → 菜单 Reload Modules (Dev) → 重开工具窗口）
