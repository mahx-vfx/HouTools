# HDR 库浏览器

适用：`houtools/hdrlight/browser.py` + `tools/hdr_library.py`（AGENTS.md「HDR 库浏览器」行的细节展开）。

## 库与侧栏

- 菜单 Hdr Library；递归扫描 `.hdr/.hdri/.exr`，第一级子文件夹即分类（左侧侧栏切换 全部/收藏/各分类，侧栏右键可新建/打开分类文件夹，空分类也列出）。
- 侧栏行样式与 Recipe Library 分类板块同款（共享 `houtools/ui/sidebar.py` 的 SidebarDelegate：行首图标 grid/star/folder + 名称 + 右对齐计数、选中蓝底圆角，计数/图标经 ItemRole 携带，行底色/视口同色 #26262b）。
- 右键收藏 HDR（存 `settings/hdr_library.json` 的 `favorites`，normcase 路径），侧栏"★ 收藏"过滤。
- **收藏标识 = 缩略图右上角角标**（与 Recipe Library 共用 `ui/badge.py` 的 FavoriteBadge，名字不加前缀；过滤重建与后台批量补图两条路径都经 _item_icon 合成角标）。
- **侧栏「新建分类」的名称校验面与 automation `_sanitize_config_name` 对齐**（分隔符之外还须拒 NUL、纯点号——".." 不含非法字符却会"建出"指向库目录上级的分类——与 Windows 保留名）。

## 双击赋 HDR 给灯光

- 双击缩略图把路径赋给选中的灯光（可多选批量赋同一 HDR，左上角显示单个路径或选中数量（hou.ui.addSelectionCallback 推送驱动，无常驻轮询，closeEvent 注销回调））。
- 支持 OBJ `envlight`（`env_map`，自动关 `skymap_enable` 程序化天空）、RenderMan dome 灯（`env_map`/`rman__EnvMap`）、LOP `domelight` 全家族（USD 输入 `inputs:texture:file`，参数名被 punycode 化成 `xn__inputstexturefile_*`，按"名字含 texturefile"特征匹配，`_control` 作者状态参数须为 `'set'` 才写入 stage；已实测 DomeLight_1 prim 的 `inputs:texture:file` 成功 authored）。
- 灯光过滤器（`pxr*lightfilter` 等）在 is_light_node 排除，防 `map` 参数误匹配；非 dome 灯型报友好错误。

## 网格（刚体，同 Recipe Library 逻辑）

- 网格尺寸固定、不随面板宽伸缩（用户指定）：滑条值 = 图标宽（64~256，高为其半），格宽 = 图标宽+GRID_PADDING_X、行高 = 图标高+GRID_PADDING_Y，__init__ 显式应用一次；面板宽窄只改变每列数量（Qt 原生换行），**无任何视口宽依赖的重排**（原"列宽自适应铺满"方案会随面板宽拉伸缩略图——用户否决，缩略图是刚体）。
- 分类过滤为**重建式**（只添加匹配条目——IconMode+gridSize 下 setHidden 的条目不绘制但仍占网格槽位，分类视图顶部会空一片）。
- 缩略图就绪后 400ms 批量应用图标（每张 setIcon 都触发一次全网格延迟重排，逐张应用=持续闪烁）。

## 缩略图后台生成（hoiiotool）

- 缩略图后台 QThread 内部线程池（**无 parent**，Reload 销毁窗口不会连带销毁运行中的线程；并发按核数/4 夹在 2..4 自动定档；底部有进度条 + 暂停/继续/停止，暂停在每张开工前生效；单张异常兜底记 warning 不中止整批；刷新起用新线程池时进度/完成信号按 sender 过滤，旧线程余量不污染新一轮；hoiiotool 子进程等待 1s 粒度可中断，CREATE_NO_WINDOW 防止批量生成时连续弹控制台黑框）。
- 调 `$HFS/bin/hoiiotool` 生成（缓存 256px 存库目录 `.thumb_cache/`，按相对路径+扩展名命名避免同名/同名不同扩展冲突，mtime 旧于 HDR 自动失效，先写 `~tmp_<线程id>_*.jpg` 临时文件（线程标识防刷新时新旧两池并发覆写同一临时文件）再原子改名（临时名必须保留 `.jpg` 后缀——OIIO 靠扩展名选输出写入器，`.part` 之类未知后缀会让 hoiiotool 直接失败；坑：`--resize` 必须写全 WxH，`--colorconvert` 需 OCIO 配置、缺失时自动注入 `$HFS/packages/ocio/`，glob 结果须 sorted 再取 [0]）。
- **刷新先 `_stop_worker` 打断旧线程再 `clean_stale_thumbs`**（顺序反了清理会把在途的 `~tmp_` 临时文件当无主缩略图删掉，旧线程 os.replace 失败——单张异常有兜底但那张白生成）。
- **运行中的线程由模块级 `_LIVE_THREADS` 留引用**（NameError 守卫活过 Reload，同 automation `_LIVE_ENGINES`；线程无 parent 只保证"父窗口死不掉它"，不保证"没人引用就不被 GC 析构"——Qt6 下析构运行中的 QThread 直接 qFatal）。
- **hoiiotool 路径/OCIO 环境只在主线程解析**（`_oiiotool_path`/`_ocio_env` 会调 `hou.text.expandString`，启动线程前解析好作构造参数传入——工作线程内零 HOM 调用，线程结构上保证）。
- **缓存名对含 `__` 的相对路径追加 md5 前 8 位后缀**（`__` 连接非单射，`a__b/x.hdr` 与 `a/b/x.hdr` 曾互相覆盖；不含 `__` 的命名不变，旧缓存不受影响）。
- **`clean_stale_thumbs` 对 `~tmp_` 前缀在途临时文件一律跳过**（2026-10 复审修复）：reload 先 `_stop_worker` 打断旧线程，但中断按 1s 粒度生效、子进程 kill+收尾可超 1s，`wait(1000)` 未必等到线程真正退出——"先停再清"的顺序防护升级为内容防护，旧线程随后的 os.replace 才不会对着被删的临时名失败。`.part` 是历史版本残片照删。
- **`_image_size` 的失败告警过中断守卫**（同 make_thumbnail）：停止/关窗/刷新 kill 掉的 `--info`（rc=-1）属有意行为，不记假告警。
- **「打开所在文件夹」的 `/select,` 与路径并成单 token**（2026-10 复审修复）：拆两个参数时 list2cmdline 产出 `explorer "/select," "C:\..."`，部分 Windows 版本不选中文件。
- **新建分类校验追加结尾点/空格与"保留名+扩展名"**（2026-10 复审修复，与 automation `_sanitize_config_name` 同步）：Win32 CreateDirectory 剥掉结尾点/空格，实际建出 `abc` 而 `_category_key` 记 `abc.`，重建侧栏匹配不到。

## 库目录

存 `settings/hdr_library.json`（未配置时顶部路径显示为空、打开不自动扫描，点「刷新」才扫描+补缩略图；默认 `~/HouTools/hdri`/`HDR_TOOL_LIB` 仅作「更换目录」对话框起始位置）。

## 已知代价备忘（暂不修，2026-10 审查）

- **「刷新」的全量 os.walk + 逐文件 stat 在主线程**：显式按钮触发、可接受，但共享盘/超大库会冻结 Houdini UI 数秒（缩略图生成本身已在后台线程）。库变大后与 Recipe Library 同款排队程化（扫描挪 QThread、HOM 调用回主线程）。
