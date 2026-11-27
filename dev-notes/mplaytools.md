# MPlay 专用工具集（MainMenuMPlay.xml / houtools/mplaytools/）

适用：插件根 `MainMenuMPlay.xml`、`python3.13libs/houtools/mplaytools/`。
这是**独立于 Houdini 主程序工具链**的第二套工具集，不复用
`houtools.tools.*`（那些依赖 .hip 场景、节点树、`hou.ui.paneTab*`）。

## 定位与边界（2026-11 用户拍板）

- 需求是「新增一个专门针对 MPlay 的工具集」，**不是把整个 HouTools 搬进
  MPlay**。菜单是独立顶层 `HouTools`（2026-11 用户指定，旧名
  `MPlay Tools` 已弃用；id 仍为 `houtools_mplay_tools_menu`），
  `insertBefore>windows_menu`——官方 `MainMenuMPlay.xml` 只有
  `file_menu / view_menu / render_menu / anim_menu / image_menu /
  windows_menu` 六个顶层 id，**没有 help_menu**，照抄主菜单的
  `insertBefore>help_menu` 会落到菜单栏末尾。
- 机制：官方 `Customize menus` 文档把 `MainMenuMPlay.xml` 列为主菜单文件，
  与 `MainMenuCommon.xml` 同一套 `<mainMenu><menuBar><subMenu>` schema；
  `HOUDINI_MENU_PATH` 默认 = `HOUDINI_PATH`（本机 `hconfig` 显示未单独
  定义），所以文件放插件根目录即可，`HouTools.json` 无需改动。
- 社区实证：`jamesrobinsonvfx/mplay_batch` 只放同名 overlay 就加出了
  「Batch」顶层菜单，`scriptItem/scriptCode` 里 `import hou` 可用，
  `kwargs["toolname"]` 区分条目。
- **坑（实机反馈修正）：MPlay 不会把 `HOUDINI_PATH/pythonX.Ylibs`
  加进 `sys.path`**，菜单点击实测报
  `ModuleNotFoundError: No module named 'houtools'`。
  `hython`（非 GUI）会自动扫描、`pythonrc.py` 也不在 MPlay 启动期执行
  （自制探针验证），**不能拿 hython 推断 MPlay**；老插件 mplay_batch
  显式追加 PYTHONPATH 是必需的，不是 H18.5 的保守做法。
  修复走两道，缺一不可：
  1. **菜单脚本自举**（主修）：`hou.findFiles("MainMenuMPlay.xml")`
     反查插件根目录 → 按当前 Python 版本取 `pythonX.Ylibs`，不存在时
     回退 `python3.13libs` → `sys.path.insert(0, ...)`。
     `hou.findFiles` 是官方 HOM 函数，实测返回插件根两份 + HFS 一份。
     这样即使 MPlay 是从**已运行的 Houdini** 里拉起、继承的是改包清单
     之前的旧环境，菜单也能直接 import；失败时抛带中文指引的
     RuntimeError，不再给裸 ModuleNotFoundError。
  2. **包清单兜底**：`HouTools.json` env 追加
     `{"PYTHONPATH": {"value": "<插件根>/python3.13libs", "method": "append"}}`
     （仓库副本用 `$HOUDINI_PACKAGE_PATH/HouTools/...`，**生效的那份在
     `Documents/houdiniXX.X/packages/HouTools.json` 用绝对路径**，两处
     必须同步；`hython` 实测 `os.environ["PYTHONPATH"]` 已带上该路径）。
     对 H21 同样适用：真身就在 python3.13libs，绕过 python3.11libs
     入口壳也能正常 import（业务代码本来就跑在 3.11 下）。
  改包清单后要**重启进程**：独立 MPlay 重启即可；从 Houdini 里
  Render to MPlay 拉起的子进程还要重启 Houdini。菜单自举只依赖
  HOUDINI_PATH 已含插件根，重启 MPlay 就生效。
- `tests/smoke_test.py` 会把 XML 里的自举代码抽出来，注入假 `hou`
  与临时插件根真实 `exec`，断言 `sys.path` 被挂载（不是只查字符串）。
- 官方文档 `seqls -f`：「带 -f 打印每条已加载序列的**完整路径 + 文件
  模式**，一行一条」。没有「当前序列名」的查询命令，`seqls`/`seqls -f`
  顺序即 Sequence List 顺序 → 取第一条有效行，多条给警告，不擅自全转。
- **坑（2026-11 实机反馈）：拍屏 / ip 渲染是内存序列，`seqls -f`
  只返回序列名**，实机输出 `'Flipbook Images\n'`，磁盘上根本没有源帧，
  「直接读原帧」这条走不通，会报 `seqls -f 未返回有效路径`。
  修复：`current_sequence` 解析不出任何磁盘序列时自动走
  `dump_current_sequence()` —— `imgsave -a <临时目录>/frame.\$\F.png`
  把当前序列落盘，再按同一套 concat 帧列表转码。
  * `\$` 是 hscript 转义：防止 `$F` 在 hscript 解析期被展开成当前帧号
    （官方文档明确要求）；路径必须转正斜杠且不用 `os.path.join`，
    否则 Windows 反斜杠会吃掉转义（mplay_batch 同款写法）。
  * 临时目录用 `tempfile.mkdtemp(prefix="houtools_mplay_flipbook.")`，
    `current_sequence` 返回第三项 `cleanup`；`tools/sequence_to_mp4.run()`
    用 `try/finally` 保证成功、失败、提前 return 都清理临时帧。
  * `imgsave` 一帧都没写出（空序列 / 渲染未完成）时抛带 hscript stderr
    的明确错误，不再误报「没有序列」。
  * 内存序列静默临时落盘并在 ``finally`` 自动清理，不额外发状态栏通知。
- MPlay 独立进程限制：`hou.ui.paneTab*`、`hou.node()`、`hou.qt.Icon`
  不可用；`uiready.py`（默认热键 / recipe bootstrap）不能假定执行；
  MPlay 的 hscript 命令表里**没有 `menurefresh`**，改 XML 要重启 MPlay。
  因此分发器每次点击自行按依赖序重载 `session → export → tools.<id>`，
  改工具代码免重启；菜单结构改动仍需重启。
- `-F` 全图模式 / Profile 关掉 Menubar 时自定义菜单不可见。
- 标签保持 ASCII（与 OPmenu 中文 label 不渲染的坑同口径）。
- **About 手册不收录 MPlay 工具**：`tests/test_about_sync.py` 的
  `MENU_FILES` 是三份主程序菜单，MPlay 是另一套工具集，塞进去会让
  「菜单项 ↔ 手册章节」覆盖断言失去语义。

## 会话读取（session.py）

- `current_sequence()` 返回 **`(spec, warnings, cleanup)` 三元组**：
  磁盘序列时 `cleanup is None`（无副本）；内存序列时 cleanup 必不为 None，
  调用方负责在 `finally` 里执行。`allow_dump=False` 可关闭落盘兜底
  （只想处理磁盘序列时用）。
- 所有函数 import 期不碰 hou / PySide6；`hou_module` 可注入，
  `NO_HOU` 明确禁用自动探测（无头测试用）。`_expand_with_hou` 只接受
  「像目录」的返回值（含 `/` 或 `\`、无 `$`），防假 hou 污染路径。
- `expand_houdini_path` 只展开**目录部分**：整串 `expandString` 会把
  `$F4` 展开成当前帧号，序列退化成单帧（videoseq 同款坑）。
- 模式串没有官方格式承诺，解析器同时认 `$F4` / `$F` / `####` /
  `@@@@` / `%04d` / `*` / `?` / 具体帧号 / 无编号单帧；`N:` 前缀与头部行自动跳过，
  最终以**扫盘得到的真实帧文件**为唯一事实来源，padding 仅作展示。
- `$HIP` / `$HIPNAME`：本机 `hython` 实测两者都在 `os.environ` 里
  （`HIPNAME=untitled`），Houdini 启动的 MPlay 会继承；独立启动没有。
  解析链 `HOUTOOLS_MPLAY_HIP` 覆盖 → `$HIP` → env `HIP` →
  `hou.hipFile.path()` 目录；名字链同理，且剥掉 `.hip/.hiplc/.hipnc`
  并过滤文件名非法字符。两者缺一即报可操作的错误，**不猜输出位置**。
- 帧率取 `hou.fps()`，异常/越界落 24。

## 转码（export.py）

- ffmpeg 查找完全复用 `houtools.videoseq.ffmpeg.find_ffmpeg` +
  `get_startup_kwargs()`（`HOUTOOLS_FFMPEG` → PATH(hffmpeg 优先) →
  `$HFS/bin/hffmpeg`；stdin=DEVNULL、Windows 隐藏控制台）；沿用
  `text=True` 一律 `encoding="utf-8", errors="replace"`（cp936 会炸）。
- **SideFX hffmpeg 7.1.0 实测三条硬事实**：
  1. **没有 libx264**，只有 `libopenh264` / `h264_nvenc` / `h264_amf`
     （`-encoders` 输出实测）。故按 `-encoders` 实测挑
     `libx264 → libopenh264 → h264_nvenc → h264_amf`，探测失败落
     `libopenh264`；`-crf` 只给 libx264，其余走 `-b:v 10M`（实测
     libopenh264 收到 `-crf` 会报 "AVOption has not been used"）。
  2. **`-pattern_type glob` 不可用**：报 "globbing is not supported by
     this libavformat build"。
  3. image2 `-start_number` 序列模式**缺帧静默断流**：4 帧缺 1003
     只写出 2 帧且 rc=0（与 videoseq 笔记一致）。
- 因此统一走 **concat demuxer 显式帧列表**（`file '绝对路径' +
  duration 行`），缺帧、混合 padding、路径含字面 `%` 全部免疫；
  路径统一转正斜杠、单引号按 `'\''` 转义、`-safe 0`。
- **concat 默认 25fps**：只给 `duration` 行实测 `avg_frame_rate=25/1`；
  必须同时给 `-r <fps>`（放在 `-i` 前）才是 `24/1`。两个都给。
- EXR 输入在 `-i` 前加 `-apply_trc iec61966_2_1`（线性转 sRGB，
  videoseq 同款规则）；实测 concat + EXR 时该解码器选项生效，非 EXR
  不加（否则会刷 "AVOption has not been used" 警告）。
- `crop=trunc(iw/2)*2:trunc(ih/2)*2` 保证 H.264 偶数宽高（321×181
  实测输出 320×180）；`-pix_fmt yuv420p` + `-movflags +faststart`。
- 输出路径 `$HIP/Flipbook/YYYY-MM-DD/$HIPNAME.vNNN.mp4`：只扫目标
  日期目录里同名 `.vNNN.mp4`，取最大 +1，`v001→v002`、`v009→v010`
  自然进位，绝不覆盖已有成片；目录由 `run_export` 创建。
- 完成后 `explorer /select,<file>` 打开并选中成片，失败退
  `os.startfile(目录)`；macOS `open -R`、Linux `xdg-open 目录`。
- **v1 同步执行**：MPlay 的 `hou.ui` / `hdefereval` 没有官方线程承诺，
  后台线程回调 Qt 对话框有崩溃风险；所有 hou 调用留在菜单点击主线程，
  纯数据交给 export，转码期间 MPlay 短暂无响应。需要进度/取消时再引入
  线程 + 主线程派发（先确认 MPlay 是否有 `hdefereval`）。

## 反馈通道（feedback.py）

- **实机（H21 21.0.512 MPlay）确认：MPlay 的 ``hou`` 既没有 ``hou.qt``
  也没有可用的 ``hou.ui``**。旧版 ``report()`` 的降级链仍会调
  ``houtools.dev.status`` → ``ui.notify._main_window()`` →
  ``hou.qt.mainWindow()`` → AttributeError；notify 自己 catch 了不崩，
  但**每条状态都往日志刷一整段 traceback**（用户实机日志三条 WARNING）。
- 现按 ``_has_hou_qt()`` **分流**：有 ``hou.qt``（Houdini 主程序）才走
  项目统一 toast；没有（MPlay）绝不碰 toast，走
  ``hou.ui``（若有）→ 错误用 hscript ``message`` 弹可见框。
  MPlay 的成功反馈本来就是自动打开的成片文件夹，不需要弹框。
- **控制台契约（2026-11 用户拍板「成功只输出文件路径」）**：
  ``report(msg)``（info）**完全静默**；
  ``report(msg, error=True)`` 一条 ``logger.error`` + 可见框；
  ``output_path(path)`` 是成功的唯一出口，只 ``print`` 一行**纯路径**。
  ``export.run_export`` 也不再打印完整 ffmpeg 命令。
  ``core/log.py`` 的 StreamHandler 未设 level（DEBUG 也会进控制台），
  所以中间状态必须真静默，不能靠 ``logger.debug`` 假装隐藏。
- smoke 注入「无 qt / 无 ui」的假 hou，断言 info 无 stdout、无 toast、
  无弹框；error 恰好一条 ERROR + 走 ``message``；
  ``output_path`` 的 stdout 恰好等于 ``路径 + "\n"``；
  「有 qt」时主程序分支仍走 toast。
  另按 H21 实机形态完整 exec 菜单脚本，日志只剩 4 行 INFO，零
  ``ui.notify`` / ``AttributeError`` / ``toast 创建失败``。

## 实机验收（H21 21.0.512，2026-11-25）

- 用户实机日志确认：`HOUDIN~1.512` = Houdini 21.0.512（Python 3.11），
  ``python3.11libs`` 入口壳把 ``__path__`` 扩展到 python3.13libs 后整套
  代码正常跑；菜单自举挂 sys.path 生效（H21 的 packages 副本**没有**
  加 PYTHONPATH，功能照样正常）；``imgsave -a ...\$\F.png`` 转义写法
  实机有效、50 帧全部落盘；hffmpeg 编码器探测选中 ``libopenh264``；
  concat + ``-r`` + libopenh264 成功产出 mp4。
- 该次 ``$HIP=C:/Users/<user>`` / ``$HIPNAME=untitled`` —— **工程未保存时
  Houdini 把 $HIP 指到 HOME**，于是成片落到
  ``C:/Users/<user>/Flipbook/2026-11-25/untitled.vNNN.mp4``。这是 Houdini
  自身语义、不是路径规则 bug；用户指定的规则不改，工具在
  ``hipname == untitled`` 或 ``$HIP == HOME`` 时加一条可见提示，
  说明成片会输出到该目录（避免「怎么跑这儿去了」）。

## 四项实机反馈修复（2026-11-25 晚）

用户回传截图：MPlay 里是蓝灰背景 + 浅灰细网格 + 白色物体，导出的 mp4
却是**纯白背景 + 粗黑网格 + 白色物体轮廓**，与所见不符；同时要求控制台
精简、按用户设定范围导出、路径落到真实工程。

1. **控制台**：见上节「控制台契约」，成功只有一行成片路径。
2. **范围**：新增 ``playback_range()`` —— 官方 ``frange`` 已由
   ``hou.playbar.frameRange()`` 取代，优先 HOM，失败回退
   ``hscript("frange")`` 解析 ``"Frame range: 1 to 50"``。
   磁盘序列用 ``filter_sequence_range()`` 按帧号取交集；
   内存序列把范围交给 ``imgsave -f start end``（不再落盘全部帧）。
   范围与磁盘帧无交集时退回全部帧，不产出空视频。
3. **画面一致性**：``imgsave`` 显式 ``-g auto -p C``。
   - ``-p C`` 只写颜色平面：ip flipbook 带多 plane，不指定时可能写出
     alpha/其它平面（SideFX/odforce 论坛记载 MPlay 保存的 C/A 平面问题）。
   - ``-g auto`` 让 Houdini 做显示空间色彩转换；SideFX 论坛明确记载
     MPlay 导出颜色不匹配是 gamma / color space 问题。
   - 格式优先 **8-bit jpg**（mplay_batch 的默认；原先选的无损 png 可能是
     16-bit/线性，转 yuv420p 时容易对比溢出 → 白底粗线），png 回退。
   - 参数不被接受时自动降级到裸 ``imgsave -a/-f``，最多 4 种组合，
     任一组合写出帧即采用。
   - **诊断开关**：``HOUTOOLS_MPLAY_KEEP_FIRST_FRAME=1`` 时把 imgsave
     导出的首帧复制到成片目录 ``_mplay_source_first.<ext>``，
     用于区分「导出就不对」还是「ffmpeg 编码不对」。默认关闭、无残留。
4. **路径（两轮回传后定稿）**：用户明确「工程已保存，且 MPlay 自己的
   保存功能支持 ``$HIP``」。所以 **``$HIP`` 本身是对的**，错在我的读取
   来源：只看了 ``os.environ['HIP']``（MPlay 启动时继承的**旧快照** =
   HOME）和 HOM 的 ``hou.text.expandString``（MPlay 的 hou 很窄，可能没有
   ``text`` 子模块），完全漏了 **MPlay 自己的 hscript 变量表** ——
   ``hou.hscript('echo $HIP')``，这才是 File ▸ Save Sequence As 展开
   ``$HIP`` 用的那张表。
   ``hip_sources()`` 现在按优先级列全部来源：显式覆盖 →
   **``echo $HIP``** → ``hou.getenv('HIP')`` → HOM ``expandString`` →
   已保存 ``$HIPFILE`` 的目录（跳过 untitled）→ OS env ``HIP``；
   ``hip_name_sources()`` 同法读 ``$HIPNAME`` 并优先跳过 ``untitled``。
   ``resolve_hip()`` 先挑非 HOME 的真实工程目录；**全是 HOME 也返回
   可写目录**，不再硬报错（上一版门禁让未同步变量时工具完全不可用）。
   ``HOUTOOLS_MPLAY_DEBUG_PATH=1`` 时 ``diagnose_hip()`` 打印每个来源的
   实际值，一次点击即可定位 MPlay 变量表。默认关闭、不影响「成功只输出
   一行路径」。

## 三条实机反馈修复（通知 / 范围 / 文件夹）

1. **打开的文件夹不对**：``explorer /select,<path>`` 在 MPlay 拉起的进程
   里常打开默认位置，而 ``Popen`` 不抛异常、无法察觉失败。改为直接对
   ``dirname(path)`` 做 ``os.startfile()``（ShellExecute），结果确定；
   三平台一律打开目录，不再尝试选中文件。smoke 用 mock 断言传入的
   参数就是成片所在目录。
2. **没按用户设定范围**：官方文档明确 ``hou.playbar.frameRange()`` 是
   **global time range**（默认 1-240），用户拖的 in/out 是
   ``hou.playbar.playbackRange()`` / hscript ``frange``。旧版优先读
   frameRange → ``-f 1 240`` 等于导出全部帧。优先级改为
   ``playbackRange`` → ``frange`` → ``frameRange``（仅兜底）。
   另修「``-f`` 失败回退 ``-a`` 时静默全量」：现在先试完所有 ``-f``
   组合，确需 ``-a`` 时也必须 ``filter_sequence_range()`` 裁剪；帧号
   体系与 playbar 不同（无交集）时按范围长度截前 N 帧，绝不静默全量。
3. **点击没有开始通知**：MPlay 无 ``hou.qt``，项目 toast 链路在
   ``ui/notify.py::_main_window()`` 抛 AttributeError。给该函数加
   **Qt 回退**（``QApplication.topLevelWidgets()`` 挑最大可见主窗），
   但**只在「有 hou 但缺 hou.qt」时生效** —— 完全无 hou 的无头测试
   仍抛错，保持 smoke 里 ``ht_notify.success(...) is None`` 的既有
   断言（第一版没加这个限制，全量 smoke 直接挂了）。
   ``feedback.start_notice()`` 调 ``notify.show`` + **``processEvents()``**：
   同步转码阻塞 Qt 事件循环，不强制刷一次 toast 根本画不出来；转码
   结束后事件循环恢复，toast 按自己的定时器淡出。失败静默跳过，
   不进控制台、不打断工具。
   Qt 模拟实测：toast 可见、位于主窗左下（y=684 / 主窗高 700）。

## 临时文件与残留（2026-11 用户问询）

两处都在系统临时目录 `tempfile.gettempdir()`（Windows 实测为
`C:/Users/<user>/AppData/Local/Temp`），**都不是** `$HIP` 里的产物：

| 目录 | 内容 | 生命周期 |
|---|---|---|
| `houtools_mplay_flipbook.XXXXXXXX/` | 内存序列经 `imgsave -a` 落盘的 `frame.N.png`（或 `.jpg`） | `dump_current_sequence` 用 `mkdtemp` 建；返回 `cleanup`，由 `tools/sequence_to_mp4.run()` 的 **`try/finally`** 调 `shutil.rmtree(ignore_errors=True)`。成功/失败/提前 return 全走到；dump 自身失败时内部先 cleanup 再抛 |
| `houtools_mplay_XXXXXXXX/` | concat demuxer 的 `frames.txt` | `tempfile.TemporaryDirectory` 的 `with` 块，异常也自动清理 |

- 复查方式：跑完整链路后扫 `TEMP` 下 `houtools_mplay_*`，应为 0 个
  （2026-11-25 实测为 0）。`tests/smoke_test.py` 另断言内存序列
  `cleanup()` 后临时目录确实消失。
- **不是临时文件、会长期保留**：正式成片
  `$HIP/Flipbook/YYYY-MM-DD/$HIPNAME.vNNN.mp4` 及其日期目录。
- **失败回滚**（2026-11 补）：`run_export` 开工前 `os.makedirs` 会先建
  日期目录，ffmpeg 失败可能留下半成品 mp4；旧行为会让下次
  `versioned_output_path` 把这个半成品当成已有版本而**跳号**。
  现用 `try/except` + `_rollback()`：删除本次新建的 output（`versioned_
  output_path` 保证它是全新名字，删它不会碰别人成片），并且**只在本次
  新建且为空时** `os.rmdir(folder)`（非空时 rmdir 自然失败，静默保留）。
  smoke 用假 runner 固化「失败后无半成品、无空目录、目录里既有文件存活」。
- 边界：MPlay 被强杀 / 进程崩溃 / 断电时 `finally` 不会执行，可能留下
  一个 `houtools_mplay_flipbook.*` 目录。这类目录可以安全手动删除，
  Windows 磁盘清理也会回收 Temp；插件不主动扫描清理历史目录（避免
  误删别的进程正在写的同名前缀目录）。

## 验证

- `tests/smoke_test.py`：菜单 XML 结构/ASCII 标签/标签为 `HouTools`/
  sys.path 自举、`parse_sequence_line` 六种模式 + 缺帧 + 单帧、
  HIP/HIPNAME 解析链、版本升级、concat 转义、编码器挑选、命令拼装，
  **内存序列兜底**（假 hou 收到 `imgsave -a` 后写真帧，断言落盘 3 帧
  且 `cleanup()` 后临时目录消失）、  **失败回滚**（半成品 mp4 / 空日期
  目录不残留、既有文件存活）、**反馈分流**（开始通知直调 notify 并借助
  Qt 主窗回退；``report`` 错误在无 hou.qt 时不走 dev.status/toast、
  退 hscript message），以及**真实 ffmpeg 端到端**
  （生成 PNG 帧 → 编码 → hffprobe 断言 nb_frames / 24fps / 偶数分辨率；
  找不到 ffmpeg 时跳过）。
- 打包：`.agents/skills/houtools-release/scripts/pack_zip.py` 的
  `FILES` 必须含 `MainMenuMPlay.xml`；`python3.13libs` 整目录已收，
  新模块自动进包，但**所有新文件须 git 已跟踪**（`git ls-files`
  白名单会拒绝未跟踪文件）。
- 从 Git Bash 启 MPlay 做手工验证前要 `env -u HOME`：Git Bash 的
  `HOME=/c/Users/<user>` 会让 Houdini 把 `HOUDINI_USER_PREF_DIR` 解析成
  `C:/Users/<user>/houdini22.0`，读不到 `Documents/houdini22.0/packages`
  下的 `HouTools.json`，菜单与 ffmpeg env 都不会生效。
