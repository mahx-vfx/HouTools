# 视频转序列图窗口（拖放/路径框/提取）

适用：`houtools/videoseq/window.py`（AGENTS.md「视频拖放/路径框」行的细节展开）。

## 拖放与入口

整窗 + `_VideoSourceGroup` 双层接收拖放；`_load_media` 是浏览/拖放/手输共用入口（视频走原链，静帧走 `_load_still_sequence`）。

## 图标按钮改版（2026-11 用户指定）

- **两个「浏览」按钮文件夹图标化**（视频源/输出目录）：Automation flipbook 同款 22×22 无边框图标按钮，`_make_icon_button` 统一构建、`ui/icons.svg_icon` QSvgRenderer 离屏渲染（QtSvg 缺失回退文字）。**行为不变**——视频源仍走 `hou.ui.selectFile(collapse_sequences=True)` 折叠序列（Qt 对话框兜底链保留），输出目录仍走目录选择对话框；原 `QPushButton#browseBtn` 文本按钮样式随之删除。
- **相机行「选择」改 chooser 图标按钮**（官方 `BUTTONS/chooser_node.svg` 提取件 = 参数面板 "Open floating operator chooser" 同款图标：浅色节点块 + 白光标；**不是红光标的 `chooser.svg`**——chooser 家族 7 个变体离屏渲染逐一比对定案，"node" 语义也对得上选节点器）+ 前置跳转按钮：点击 `hou.ui.selectNode(title, initial_node=当前所选, custom_node_filter_callback=_cam_filter_callback)` 原生浮动操作员选择器选相机。**返回值坑**：`multiple_select=False` 返回单个路径 str、取消/清空返回 None——勿按 tuple 处理（`houpythonportion/ui.py` 实证：`_hou` C 层只有 `ui__selectNode`，公开名在该文件以 `@_addMethod(hou.ui)` 绑定）。`_cam_filter_callback` 只放行 OBJ cam（`type().name()=="cam"` 且 category Object——SOP Camera 等同名类型不放行，所选相机要吃 `vm_background`/`resx`/`resy`，必须 OBJ 相机）。旧 `_on_pick_camera`（QInputDialog 下拉）随之删除——原生选择器列完整路径树，"同名相机按 basename 反查永远命中第一个"的问题不复存在。
- **跳转按钮**（jump.svg，Automation 任务槽同款图标）：`ui/networknav.jump_to_node`（与 automation 同源实现，2026-11 迁出共享）定位当前所选相机（`_selected_camera_node()`，未选/已删黄字提示）。

## 静帧序列（EXR/PNG/TIF → JPG，2026-11 新增）

- **交互形态是用户拍板**：整条序列的唯一入口是「浏览」按钮的 Houdini 文件选择器（`hou.ui.selectFile(collapse_sequences=True, pattern="*")`，同一序列折叠成一个选项、返回 `name.$F4.exr` 形态）；拖入/手输**具体帧只转换该帧，不向兄弟帧扩张**。`pattern` 必须显式传 `"*"`——文档明确 `None` 会复用该文件类型上次使用的记忆值。
- **解析纯函数**（模块级、smoke test 直测）：`parse_still_frame`（具体帧 → 单帧 spec，无编号静帧返回 None 走"具体文件直转"）与 `parse_sequence_pattern`（`$F` 模式 → 扫盘枚举；位数取 `$F` 声明，裸 `$F` 且磁盘宽度唯一取磁盘宽度）。**两个坑都是点号**：`_SEQ_PATTERN_RE` 的 `ext` 捕获组不含前导点（`\.` 被消耗），拼 `%0Nd` 模式与枚举 regex 时都要显式补 `.`——两处漏补各自炸过一次（枚举 regex 少点 = 永远枚举 0 帧）。
- **ffmpeg 侧机制**（SideFX hffmpeg 7.1.0 实测）：image2 demuxer **不做 glob**，按 `-start_number` 逐帧 zfill 生成文件名——**缺帧即静默断流**（零报错、rc=0、只出到缺口前），故 `_on_convert` 有缺口预检（`max-min+1 ≠ 文件数` 弹确认框，告知"会在第一个缺帧处停止"）。输入/输出帧号解耦：demuxer/muxer 两侧各一个 `-start_number`，输入侧=探测出的首帧号，输出侧=UI 起始帧号。
- **色彩管理**：EXR 是线性编码，ffmpeg 默认直通转 JPG 全片偏暗（实测 0.502 线性灰 YAVG≈128）——转换命令对 `.exr` 输入加解码器选项 `-apply_trc iec61966_2_1`（放 `-i` **前**），转出 sRGB（YAVG≈188，与 hoiiotool `--colorconvert linear sRGB` 逐值一致）。**该选项是 exr 解码器专属**：png/tif 本身是显示色域编码，不套 TRC（重复套伽马会洗白），色彩分支只认扩展名 `.exr`。DWAA/DWAB 压缩实测可解（Karma/Arnold 常用）；**multipart EXR / deep data 不支持**（ffmpeg exr 解码器限制）；`>1.0` 高光直接裁切无 tonemap 滚动——视窗背景代理足够，lookdev 精查不是本工具定位。
- **探测**：序列模式跳过整条视频探测链（ffprobe 全量→ffmpeg 回退→逐帧计数），`_ProbeWorker` 收窗口主线程解析好的 `VideoInfo`（`sequence_info` 参数）只跑 `_probe_first_frame`（ffprobe 首帧取分辨率，失败静默——分辨率不是转换必要条件）；帧数=磁盘枚举数，fps/duration 恒 0（信息面板显示 "-"）。
- **$HIP 变量路径必须先展开**（GUI 实测报错修复）：Houdini 文件选择器与手输都可能返回 `$HIP/render/...` 形态，`os.listdir/isfile/ffprobe` 对字面 `$HIP` 直接 WinError 3。`expand_houdini_path`（模块级纯函数）只展开**目录部分**、basename 原样——`hou.text.expandString` 会把 `$F4` 展开成当前帧号、整条序列退化成单帧，所以不能整串展开；无头回退 `os.path.expandvars`（未知变量原样保留，交给 isfile 拒绝）。`_current_video_path` 存展开后的真实路径（探测/转换用），路径框显示保留 `$HIP` 原文（换机/分享更有意义）；`_on_path_editing_finished` 的相等守卫用展开后比对，否则 `$HIP` 形态每次失焦都会重新探测。
- **ffmpeg 查找优先级不变**：项目根 ffmpeg.exe 优先的老规则保留——项目根放了老版 ffmpeg 的用户走 EXR 可能报"参数不受支持"，属预期行为。
- 相机背景写回、旧序列 .bak 三态、取消语义对序列输入零改动（输出仍是 JPG 序列，`vm_background` 闭环成立：EXR 渲染 → 低质量 JPG 代理 → 相机背景）。

## 取消与子进程（踩坑重灾区）

- 提取取消为协作式：探测阶段（ffprobe 30s/ffmpeg 回退 120s 超时，期间点停止 ffmpeg 不得启动）与 Popen 前各查一次 `_cancelled`，收尾 terminate+`wait(15s)`+kill 兜底。
- **无人读 stdout 时 ffmpeg 会卡死在 progress 管道写上（缓冲区约 4KB 填满即停），裸 `wait()` 会永久挂起线程，而窗口 closeEvent 靠轮询 `isRunning()` 延迟关闭，线程挂起=隐藏的窗口永不关闭。**
- **stderr 用 DEVNULL**（stderr 管道无人读是同款挂死、换了个管道）。
- 所有子进程 `text=True` 一律 `encoding="utf-8", errors="replace"`（中文 Windows 默认 cp936 会解码失败）。
- **取消语义对齐 Automation「停止中…」**：点停止后按钮禁用直到该 worker 的 done/error 到达（按 sender 过滤迟到信号）才复位；`_on_convert` 不覆盖运行中的 worker；换片取消旧探测 worker 后挂 `_retired_workers` 集合保引用（内建 finished 自动摘除）防 GC 析构运行中 QThread。

## 转换流程

- 开工前扫描输出目录同前缀旧序列并询问是否清除（防新旧帧混杂），完成帧数只报 ffmpeg progress 的 frames_done；取消时提示已写入帧数。**改名 .bak 前若 .bak 已存在（上次进程崩溃的遗留、那轮旧序列的唯一副本），先按时间戳改名保底**（`.bak.<YYYYmmddHHMMSS>`，不进还原/清理名单、留用户手动处置）——无条件覆盖会把原始帧永久丢掉（2026-10 审查修复）。**保底改名失败必须 `continue` 跳过该 path**（2026-10 复审修复）：keep 比 bak 长 15 字符，深层目录逼近 MAX_PATH 时正是"保底失败、让位反而成功"的组合，继续 replace 会把唯一副本顶掉。
- **ffprobe 回退解析时 fps 与分辨率同款锚定 `Video:` 行**（2026-10 复审修复）：文件名里的 "24fps"（render_24fps.mov）先于流行出现，裸 search 命中它后 fps×duration 的帧数估算全错。
- **提取 worker 的 stdout 循环 + 进程收尾包在 try/finally 里**（2026-10 复审修复）：Popen 之后若有意外异常穿出 `_extract`，Popen 析构不杀子进程、ffmpeg 成孤儿继续写帧；finally 保证任何路径都 terminate/有界 wait 收掉。`cancel()` 读 `self._process` 先局部捕获再判空（worker 收尾置 None 与主线程读侧的竞态窗口）。**`_reset_video_state` 对转换中同样拒绝**（与 `_load_video` 换片拒绝同语义）：路径框被手动清空不再半重置面板、与仍在写盘的 ffmpeg 互不相认。
- closeEvent 对探测线程只 cancel 不 wait（子进程不可中断，wait 必然白等）；实现 `is_close_deferred()` 配合 window_manager（见 AGENTS.md「窗口单例」行），延迟关闭收尾自删；showEvent 撤销未决的延迟关闭。
- **转换不再重新探测**（窗口 `_video_info` 作为构造参数传入 `_ExtractWorker`，原 worker 从零重探最坏多等一次全片解码、且两次帧数可能不一致致进度条对不上）。

## 相机区

- 单相机自动选中、多相机走选择对话框，两条路都经 `_set_selected_camera` → `_refresh_camera_res` 同步分辨率行。
- **分辨率是宽/高两个独立输入框**（用户指定，不用"宽x高"合并框），显示所选相机 `resx`/`resy`（标准 cam 的分辨率就是这一对参数、无单独 `res` 参数，hython 实测），「确定」经 `parse_dimension` 逐框校验（1~5 位正整数）后写回。**写回捕 `hou.Error` 而非仅 OperationFailed**（2026-10 复审修复）：锁定参数（手动 Lock/锁定 HDA 实例）抛的 PermissionError 是兄弟类，漏捕会让异常穿出 Qt 槽、界面零反馈。
- **「预设」下拉按钮在「确定」前**（QPushButton 挂 QMenu，`RESOLUTION_PRESETS` 常量表：1280×720~4096×2160 共 7 档），点选只把值填进宽/高两框、写回仍走「确定」。
- **`showEvent` 里必须重读所选相机刷新分辨率行**——单例窗口关闭只是隐藏，隐藏期间分辨率可能已在 Houdini 里被改，只在选择相机时刷新会一直显示旧值（实测踩过）；showEvent 同时清掉上次会话遗留的状态栏提示（如"已设置 … 分辨率"，用户指定不带到重开后的面板）。
- 无相机/相机已删/参数缺失时输入框与按钮禁用，写回成功后状态栏绿色反馈。
- **相机查找走 `nodeType(objNodeTypeCategory, "cam").instances()`**（原 allSubChildren() 递归全场景且在窗口构造期调用，大场景卡顿数秒）。
- **相机选择对话框下拉直接列完整路径**（按 basename 反查 index 在重名相机下永远命中第一个、分辨率写错相机）。

## 完成后写相机背景

- 转换完成后写所选相机 `vm_background`（用开工快照的原始目录文本保留 $HIP 变量）**并确保 `vm_bgenable`=1**（启用背景，用户指定）。
- **该步骤只捕 ImportError 曾让锁定参数的 PermissionError 掀翻 `_on_extract_finished`、"完成"弹窗永不出现**——现补 except Exception + 状态栏黄字（帧已导出，装饰性步骤失败不绑架主流程）。
