# Package Project（打包工程）

入口：`HouTools → Package Project`（主菜单 + 网络编辑器菜单各一条）。
代码：`houtools/package/paths.py`（纯逻辑：路径/分类/包名/默认值/场景指纹）、
`collect.py`（扫描 + 布局 + 统计 + 日志文本）、`window.py`（界面/等待遮罩）、
`houtools/ui/reveal.py`（资源管理器定位）、`houtools/tools/package_project.py`。

## 解决什么问题

Houdini 自带打包对"贴图散在 D 盘、缓存散在几个盘、绝对路径满地"的工程不够用；
渲染农场（Renderbus 这类）提交要求工程自带依赖、路径可移植。

本工具：**收文件 → 统一放进一个包目录 → 改相对路径 → 按选定方式保存 → 打 zip**。

## 扫描规则

- **只扫启用节点**：`isBypassed()` 为真的跳过（**网络容器没有这个方法，必须
  getattr 探测**，否则 `/obj`、`/out` 一抛异常整棵子树都收不到）；locked HDA 内部跳过。
- **收三类来源**（全部会复制、全部会改写）：
  - 包外绝对路径；
  - `$HIP` 内绝对路径；
  - `$HIP/…` 或相对路径（Houdini 自己写出的 `$HIP/geo/*.bgeo.sc` 属于这类）。
- **只收用户改过的参数**：`parm.isAtDefault()` 为真 → 是 Houdini 内置默认值
  （如 `karma/hoseksolarmap.rat`），跳过。filecache 的 `basedir` 例外——默认值
  也收，因为那代表用户实际写出的缓存目录。
- **输入引用：手动填了就收**（文件不存在也收，标"缺失"、打包时跳过不改写）。
- **filecache 缓存**：`file` 参数（`$HIP/geo/$HIPNAME.$OS.$F.bgeo.sc`）不是真实
  文件，真实路径由 `basedir`+`basename`+版本子目录拼出。用 `sopoutput` 求值拿
  首帧 → `_frames_from_path()` glob 整段帧 → 把**改写目标定为 `basedir`**。
  下游 `chs("…/sopoutput")` 引用会跟着新 basedir 自动走。
- **UDIM 贴图**（2026-11-19 修复）：`texture.<UDIM>.exr` 与 `$F` 分开处理——
  按 4 位 Tile 号（1001–1999）严格枚举同目录文件、复制全部 Tile，参数里
  保留 `<UDIM>` 模板（改写只换目录）。
- **两段式扫描**（2026-11-19 修复）：`gather()` 主线程只读 HOM（节点/参数/
  展开），`resolve()` 只做 isfile/glob/isdir 并跑在 `_ScanWorker` QThread——
  大工程/网络盘扫描不再阻塞界面；`collect()` 是两者串联（无窗口调用）。
- **其它输出参数**（`sopoutput` / `vm_picture` / `picture` / `outputimage`，以及
  `dcmfilename` / `productName` / `savepath` / `soho_diskfile` 这类语义输出）
  只收**已落盘**的（未落盘输出不复制也不改写，列出来只是缺失行——「含空输出路径」勾选已移除）。
- **挡住非文件值**（`_looks_like_file_path`）：空值、带空白/`%`/`*` 的菜单值与
  token、无扩展名的值、节点路径（`/obj/...`）、`shfs:`/`op:` 协议路径、
  目录值、`$HIP/galleries`、`$HIP/backup`。节点路径的判定是"`/` 开头**且末段
  无扩展名**"（`_looks_like_node_path`）——POSIX 形态绝对路径
  （`/mnt/proj/a.jpg`：Windows 上 `os.path.isabs` 返 False、Houdini 视为绝对）
  末段带扩展名，不能误杀（`is_absolute_like`/`needs_packaging` 同步兼容
  POSIX 形态，同 filecache_abs_path._is_abs）。
- **`$HFS` / `$HH` 引用保留在列表**（2026-11 用户要求"满足条件的都要出现"）：
  Houdini 安装目录里的贴图/示例文件也会列出，是否打包进包由用户勾选决定；
  缺失的 `$HFS` 外部路径同样以"缺失"呈现。
- **内置引用标注**（2026-11-20 用户实测反馈）：`$HH` = `$HFS/houdini`，
  例如 `$HH/geo/defgeo.bgeo` 是 Houdini 自带默认几何体，展开成
  `C:/PROGRA~1/SIDEEF~1/HOUDIN~1.466/...` 短路径名，被 `flatten_dir()` 展平后
  会生成 `C_PROGRA~1_SIDEEF~1_HOU…` 这种目录名。用户明确选择**保留现状**
  （仍列出、仍可打包），但状态列加「内置 · 」前缀、状态栏显示内置计数、
  tooltip 说明"非工程素材，取消勾选即可不打包"，避免误认成工程文件。
  判定用纯函数 `paths.is_builtin_ref()`（匹配 `$HH/`、`${HFS}` 等前缀），
  经 `Candidate.builtin` → `Ref.builtin` 传递。
- **反引号表达式不碰**：`` `chs("...")` `` 展开即冻结，改写等于破坏表达式。
- **表达式驱动参数拒绝**（2026-11 复审补充）：`parm.keyframes()` 非空 → 跳过
  （对齐 filecache_abs_path 的拒绝语义）——`parm.set()` 不覆盖已有表达式
  （paste_bbox 实测），对表达式参数写静态值只会虚报"改写成功"而表达式继续
  生效。filecache 的 `basedir` 同样检查（`sopoutput` 求值不受影响——那是读
  不是写）。filecache 的 `file`/`basename` 常是表达式驱动属正常形态，不做
  此检查（本来就不改写它们）。
- **跳过统计按原因分桶**：`collect()` 返回 (refs, skip_stats)，只统计"像文件
  路径但被排除"的参数——noise（目录/记账路径）、not_written（未落盘输出）、
  expr（表达式驱动）；不再把 label/comment 这类无关注算进"跳过"（此前数字
  虚大无意义）。结果面板按原因分列。

## 包目录与目标写法

**按文件类型分类（2026-11-19 用户要求）**：包内不再有统一 `assets/` 层，
顶层直接是 Houdini 官方 New Project 目录，解压后即可当工程用：

```
<包根>/<包名>/<类型>/<原始目录或展平目录>/<文件名>
类型 ∈ geo / hda / sim / abc / tex / render / flip / scripts / other
```

- `paths.category_for(path, kind)` 按扩展名 + 引用语义归类：几何 `.bgeo[.sc]`/
  `.vdb`/`.obj`/`.usd` → `geo`；`.abc` → `abc`；`.sim` → `sim`；HDA → `hda`；
  图片/序列按引用语义——输入贴图 → `tex`，渲染输出 → `render`；视频 → `flip`；
  脚本 → `scripts`；其余 → `other`。`.bgeo.sc` 双扩展名整链匹配，不误判成 `.sc`。
- `paths.category_subdir_for()` 生成 `<类型>[/<原始目录>]`：素材在 `$HIP` 内
  保留相对目录（`$HIP/geo/shot.bgeo.sc` → `geo/shot.bgeo.sc`，类型与首段同名
  不重复）；包外素材展平（`D:\proj\tex\x.jpg` → `tex/D_proj_tex/x.jpg`）。
- **兼容模式**：`collect.build_layout(..., by_category=False)` 保留旧的
  `assets/<展平目录>/` 布局供无窗口调用/测试；窗口固定用 `by_category=True`。
- **目标唯一性**（2026-11-19 修复）：`collect.build_layout()` 统一生成
  「每条引用的改写目标 + 复制计划」。不同源目录展平后重名（如
  `D:\proj\foo\bar` 与 `D:\proj_foo\bar` 都得到 `D_proj_foo_bar`）时，后来
  的自动加 `_2/_3`，复制目标和参数改写目标共用这份映射——不会再静默覆盖。

- **包根**：`$HIP`（默认）、`$JOB`、**自定义绝对路径**。
  **`$JOB` 未设置时回退 `$HIP`**（2026-11-20 用户实测反馈，详见下节），
  不再禁用选项、不再弹"仍要打到默认路径吗"的窗。
- **包名**：默认取 **工程名 + `-pack`**（如 `shot_v001-pack`，无 hou/未保存时
  回落 `ht_package`），可改；**用户输入必须过 `paths.sanitize_pkg_name`**——
  包名直接进 `os.path.join(base, pkg, ...)`，不净化的话 `..`、含 `/` 的输入会
  写出包根之外，Windows 禁用符与保留设备名让复制/zip 报错。非法字符换 `_`、
  剥首尾点、避开 CON/NUL/COM1…，空值回落默认名。
- **参数改写按「包根选项」区分**（2026-11-20 用户推翻旧规则后重写；
  纯函数在 `paths.target_prefix()`，窗口 `_target_prefix()` 调它）：

  | 包根选项 | 另存到包目录 | 覆盖当前工程 |
  |---|---|---|
  | `$HIP` | `$HIP/<类型>/…`（**不带包名**） | `$HIP/<包名>/<类型>/…` |
  | `$JOB` | `$JOB/<包名>/<类型>/…` | `$JOB/<包名>/<类型>/…` |
  | 自定义路径 | `<路径>/<包名>/<类型>/…` | `<路径>/<包名>/<类型>/…` |

  旧规则是「另存模式无论包根都写 `$HIP/…`」，用户实测判定为**错的**：包根
  选 `$JOB` 就应该写 `$JOB`，选自定义路径就写该路径。唯一保留的例外是
  `$HIP` + 另存——hip 存进 `<原 $HIP>/<包名>/` 后会话的 `$HIP` 变成包目录，
  再带一层包名会得到 `<包目录>/<包名>/…`（多一级、读取不到），所以必须省略
  包名。**副作用**：`$JOB`/自定义路径写法不再随包移植（自定义路径是绝对路径，
  不随包移动），已在打包确认弹窗里注明。

  帧模板 `$F4` 原样保留。

## 打包日志（2026-11-19 新增）

打包完成后（`_on_copy_done`，成功/失败都写）由 `collect.build_package_log()`
生成纯文本明细，写入 `<包根>/<包名>/package_log.txt`（UTF-8），随包目录一起
被 zip 归档。内容：生成时间、包目录/包名/包根前缀/生成方式、逐条引用
（节点:参数 → 源 → 目标）、复制计划、改名说明、失败项、汇总。写日志失败只在
结果面板记一条，不改变打包成功/失败判定。

## 等待动画与场景切换（2026-11-19 新增）

- 扫描的文件系统检查阶段（`_ScanWorker`，后台线程）显示 `_ScanBusyOverlay`
  全窗口半透明遮罩 + marquee 进度条，避免"扫描中…"文字太小让人以为卡死。
- 窗口经 window_manager 跨工程复用；`_sync_scene()` 用
  `paths.scene_signature()` 比对场景指纹，new/open 不同工程后清空上一工程的
  **表格行**/日志/扫描结果/包名复位，防止残留（2026-11-20 用户反馈：
  切换工程后窗口仍保留原工程路径）。
- **触发时机**（旧版只在「点扫描 / 窗口显示」检查，切工程时窗口开着就漏了）：
  显示时、点扫描时、以及窗口内 `_scene_poll`（`QTimer`，1.2s）空闲轮询。
  用窗口内 QTimer 而非 `hou.hipFile.addEventCallback` 全局回调——后者在
  Reload / 窗口销毁后会残留对已删除 Qt 对象的引用（RuntimeError 崩溃），
  QTimer 的 parent 是本窗口、随之销毁，零生命周期风险。
- **坑：另存到包目录的 save-as 会改 hipFile.path()**，会被误判成"用户切换
  工程"，导致刚打包完列表被清空、`_pack_session` 作废、「生成 zip」不可用。
  `_save_scene()` 保存成功后立即把 `_scene_sig` 同步到新指纹来抑制。
- 切换后日志打印两条：旧工程 → 新工程路径、清空了多少条引用 + zip 已失效提示。
- 待复制文件数/体积由 `collect.selection_summary()` 按当前勾选实时重算，
  取消勾选某项后立即减少（用户反馈该项曾不更新）。
- `flatten_dir()` 只对包外素材使用（按源目录分文件夹，不丢可读性）。
- filecache 改的是**目录参数** `basedir`：`$HIP/geo` → `$HIP/geo/<类型>/…`
  （另存模式）或 `$HIP/<包名>/geo/<类型>/…`（覆盖模式），复制时保持
  `geo/<basename>/v1/...` 的相对结构，所以 `sopoutput` 拼接结果正好落在新位置。
- **参数现值展示**（2026-11-20 用户反馈）：`Ref.raw` 只用于表格「参数现值」
  列与打包日志「源」，filecache 必须填 `basedir` 的 `unexpandedString()`
  （`$HIP/geo` / `$JOB/...`），不能填 `sopoutput` 展开后的绝对路径——
  用户要能认出自己在 Houdini 里填的写法。扫描/复制一律走 `expanded` /
  `abs_path` / `basedir`，与 `raw` 解耦。打包日志对目录参数额外打一行
  「扫描: <绝对路径>」，保留帧序列 glob 命中的排查信息。
- 同一个文件被多个参数引用时只复制一次（`copy_plan` 以源绝对路径为键）。

## 生成方式

| 方式 | 行为 | 副作用 |
|---|---|---|
| `另存到包目录`（默认） | 另存 `<包名>/<hip>_packed.hip` | **会话切到该文件**，磁盘上的原 hip 不变 |
| `覆盖当前工程` | `hou.hipFile.save()` 回当前文件 | 原 hip 被改写（素材已进包目录） |

**包根与前缀的对应**（2026-11-20 起按包根选项，详见上节表格；旧版
「另存模式一律 `$HIP`」已废弃）：包根 `$JOB` → `$JOB/<包>/…`，
自定义路径 → `<路径>/<包>/…`，`$HIP` + 另存 → `$HIP/…`（不带包名）。

## $JOB 未设置时回退 $HIP（2026-11-20 用户实测反馈）

**现象**：包根选 `$JOB`、包名 `aaa`，包却打到了
`C:\Users\zhouhaibin\aaa`（C 盘用户名下），而不是当前工程所在文件夹。

**根因**：这台机器没有设置 `JOB` 环境变量（`houdini.env` 为空、用户/系统/进程
三级都没有）。Houdini 在 JOB 未设置时，`hou.text.expandString("$JOB")`
**不会**返回字面 `$JOB`，而是回退到 `$HOME`（`C:\Users\zhouhaibin`）。
旧 `paths.job_is_default()` 只判断「空 / 字面 `$JOB` / 等于 `$HIP`」三种，
`$JOB == $HOME` 既非空、非字面、也不等于 `$HIP`，所以**连默认提醒都没弹**，
`_base_dir()` 直接把用户主目录当成项目根。

**修复**（用户拍板「job 和 hip 都打包到当前工程所在文件夹」）：

- `paths.job_is_default(job, hip, home)` 增加 `$HOME` 判定；新增
  `resolve_job_root(job, hip, home)` 返回 `(实际包根, 是否回退, 回退原因)`。
- 窗口 `_job_state()` 集中取 `$JOB/$HIP/$HOME`；`_base_dir()` 在回退时用
  `$HIP`（**当前工程所在文件夹**）作实际包根。
- **`_target_prefix()` 回退时同步按 `$HIP` 写**：否则参数写成
  `$JOB/<包名>/…` 会被 Houdini 解析到 `$HOME`（C 盘用户名下），而文件实际在
  工程目录里，打包后路径直接失效。两者必须同源。
- `_check_job()` 不再禁用 `$JOB` 选项（回退后仍可用），只在 tooltip 说明
  「未设置 $JOB，打包将回退到 $HIP：当前工程所在文件夹」。
- `_warn_job_if_default()` 不再弹「仍要打包到默认路径吗」——回退后是正确
  且安全的行为，没有理由打断用户；改由 `_on_apply` 在结果面板日志打印
  回退原因 + 实际前缀。
- **若用户后续真的设置了 JOB 环境变量**（`houdini.env` 里
  `JOB = D:/projects/main`），`resolve_job_root` 判定不回退，包根与参数前缀
  都按 `$JOB/<包名>/…` 走，行为与之前一致。

**坑：包根下拉项的 userData 不能被提示文字覆盖。** `setItemData(index, text)`
默认写 `Qt.UserRole`，会把 `addItem("$JOB", "JOB")` 存的 `"JOB"` 换成提示文字，
之后 `currentData()` 返回它、拼出 `$$JOB = D:/…` 这种假变量，`_base_dir()`
就判"不可用"（用户实测踩过）。提示一律写
`setItemData(index, text, Qt.ToolTipRole)`。

**坑：另存后 `$HIP` 会变成包目录。** 如果之后再用 `$HIP` 推包目录，会得到
`<包>/<包>` 这种嵌套路径。窗口在打包时把 `(base, pkg, package_dir)` 记进
`_last_base/_last_pkg/_last_package_dir`，zip 用这份快照，不重新展开。

## 线程模型（2026-11 复审改版）

复制/压缩是纯文件 IO、可能 GB 级，主线程同步执行会让 UI 冻结数分钟——
拆成两个 QThread worker：

- `_CopyWorker`：按 copy_plan 逐文件 `shutil.copy2`；`progress`（已复制/总数）
  每文件一发（窗口刷提示行），`done(已复制, [(源, 原因)…])` 收尾。
- `_ZipWorker`：包目录整体压 zip；**先写 `<zip>.part`、成功后 `os.replace`
  原子替换**——取消/失败不在正式路径留半截 zip 被下次误当成品。
- 参数改写（`hou.undos.group` + `parm.set`）与 `hipFile.save` 必须主线程
  （HOM 非线程安全）：由 worker 的 `done` 信号（Qt 队列连接自动回主线程）
  触发，不进 worker。
- **业务完成信号不可叫 `finished`**——会覆盖 QThread 内建的 finished
  （`_retire_worker` 依赖内建信号区分"线程收尾"与"业务完成"，同 videoseq）。

**取消是协作式**（对齐 Automation）：单个 copy2/zip 条目不可打断，只在文件
边界检查 `cancel()` 置的标志；取消后不再改写参数、不保存 hip（已复制的文件
留在包目录，属无害残留）。worker 引用挂模块级 `_LIVE_WORKERS`（NameError
守卫活过 Reload，同 videoseq/_LIVE_ENGINES）：Reload 关窗路径会 deleteLater
窗口，实例属性保不住引用，运行中的 QThread 被 GC 析构即 Qt6 qFatal。

**延迟关闭协议**（window_manager）：worker 运行中 `is_close_deferred()` 返回
True——closeEvent 先确认（Reload 触发的 close_all 不弹模态，直接取消），然后
`cancel()` + `hide()` + QTimer 轮询线程退出，最后窗口自行 close + deleteLater
（close_all 对延迟窗口只持引用不删，收尾必须自删，同 videoseq）。忙态锁界面
（`_set_busy`）：计划已固定，中途改勾选/包根/生成方式会与在跑的复制错位。

**失败反馈不打控制台**：复制/改写/保存失败只走结果面板 + notify（面板已含
节点路径与原因；houtools 日志器 DEBUG 级、任何 log 都刷控制台——同
copy_output_path 的用户拍板）。collect 的 log.debug 是扫描诊断，保留。

**样式**：品牌 token 体系——`qss.window_qss(_EXTRA_QSS)`，根
objectName=`htWindow`，主按钮走 components.PrimaryButton（htRole=primary）；
表格/单选钮/表头等基础表没有的控件在 `_EXTRA_QSS` 里按 token 补（`.format`
双花括号写法同 automation/styles）；行状态色走 `QColor(theme.color(...))`，
提示/状态标签用 `htTextRole` 动态属性 + repolish（不要用裸 hex 内联样式）。

## 行操作

- **双击行** → `ui.networknav.jump_to_node(node_path, prefix="Package Project")`。
- **跳转前无条件切 Manual 更新模式**（2026-11-20 用户要求，二版收紧）：
  Auto Update / OnMouseUp 下跳到 filecache/sim/karma 等重节点都会当场重烤
  卡死（用户实测 OnMouseUp 也卡，一版只切 AutoUpdate 不够）；`_jump_to_node`
  里 `updateModeSetting() != Manual` 即用 **`hou.setUpdateMode()`** 切并在
  结果面板记一条（已 Manual 不重复记；切失败显式记 [失败]，不再静默吞错），
  恢复由用户在播放条手动选回。
  **坑：`hou.updateModeSetting()` 是只读 getter，传参会 TypeError——一版
  误当 setter 且被 try/except 吞掉，真机才暴露**；hython 实测
  `setUpdateMode` 设 Manual 读回正确。
- **右键**只有两项：跳转节点 / 在资源管理器中打开原路径（复制路径两项已按用户
  要求移除）。
- 原路径取 `ref.files[0]`（序列取第一帧），不是 `abs_path`——序列的 `abs_path`
  是带 `$F4` 的模板，磁盘上不存在这个文件；缺失引用会走 `reveal` 的 OSError 分支提示。

## 资源管理器定位（`ui/reveal.py`）

**不要**用 `subprocess.Popen(["explorer", "/select,<path>"])`：explorer 不按标准
argv 解析，Python 给整段加引号后它看到的是 `"/select,C:\x y\a.jpg"` 一个整体，
结果什么都不选中（用户反馈"跳转失败"就是这个）。正确顺序：

1. Shell API `SHOpenFolderAndSelectItems`（给文件路径，shell 自己打开父目录并选中，
   空格/中文都稳）——ctypes 里指针参数一律 `c_void_p`，否则 64 位句柄会被截断；
2. 退路：命令行字符串 `explorer /select,"<path>"`（引号自己拼，不走 argv 列表）；
3. 再退：`os.startfile(dirname)`。

## 执行结果面板

（扫描指示灯 √/! 已按用户要求移除，结果只走文本。）打包按顺序做三件事，
每一步单独收集失败，最后汇总：

```
[完成] 扫描到 12 个引用（输入 + 已落盘输出），15 个文件   ← 扫描阶段
[开始] 包目录：...
[信息] 复制完成 3/4
[失败] 复制 <源路径>：<原因>
[失败] 改写 <节点>:<参数>（<原因>）
[完成/失败] 复制 a/b 个文件，改写 c/d 个参数，跳过 e 个
```

- 复制失败的源文件 → 引用它的参数**跳过不改写**（避免"路径改了但文件没到"）。
- 有任一失败 → 状态显示 **失败**（红），并 `notify.error`；全成功显示 **完成**（绿）；
  打包/压缩进行中显示「打包中…/压缩中…」并锁界面，取消显示「已取消」。
- 保存失败单独记一条，不影响已复制的文件与已改写的参数（undo 组仍可整体撤销）。
- 扫描排除统计按原因分列（目录/记账路径、未落盘输出、表达式驱动）。

## zip

`<包根>/<包名>.zip`，内容 = 包目录整体（`assets/**` + `另存` 模式下的 packed hip）。
`覆盖当前工程` 模式下 hip 不在包目录里，额外按文件名附上。
**先写 `.part` 再原子替换**：zip 在 `_ZipWorker` 后台线程写 `<zip>.part`，
成功后 `os.replace` 到正式名——中断（取消/失败）不留半截 zip（见线程模型）。

**边界检查**（2026-11-19 修复）：归档前对每个文件做 realpath 判断，落在
包目录之外（包内符号链接/junction 指向外面）的直接跳过并计数，结果面板写
「跳过 N 个指向包外的链接」——不会把本机私有文件卷进提交农场的 zip。

**会话校验**（2026-11-19 修复）：zip 只认「上一次完整成功打包」的会话
（base/pkg/package_dir/源 hip/保存后 hip）；换 hip 后 `_on_scan` 清掉旧会话，
`_on_zip` 再校验当前 hip 是否属于该会话，不属于就拒绝并提示重新打包。

## 已知边界

- 只收被场景**引用**的文件；`$HIP` 里没被引用的散落缓存不会自动带上。
- 不解析表达式依赖（`chs()` / Python 表达式）——表达式驱动参数直接跳过
  （见扫描规则）；不处理 `op:` 协议路径。
- 不做贴图转码/降分辨率；不处理 unlocked HDA 内部参数的导出问题。
- `$F` 之外的帧写法（`%04d`）不识别；UDIM 只识别 `<UDIM>`（4 位 Tile），
  `<UVTILE>`（u1_v1）暂不支持。
- `$JOB` 必须在会话环境里存在（`houdini.env` 或系统环境变量），否则包根选项禁用。

## 测试

- `tests/smoke_test.py`：`paths` 纯函数（帧模板 / needs_packaging / POSIX
  绝对形态 / flatten_dir / 包名净化）、分类 `category_for` / `_ext_chain` /
  `category_subdir_for`、默认包名 `default_package_name`、`job_is_default`、
  `scene_signature`、`selection_summary`（取消勾选联动）、`build_package_log`、
  分类布局 `build_layout(by_category=True)`；`target_for` 的 $HIP/$JOB 两种写法、
  `copy_plan` 去重、`_is_noise` 节点路径 vs POSIX 文件、菜单接线、窗口可
  实例化（htWindow / is_close_deferred / worker 可构造）、`reveal` 缺失路径
  必须抛 OSError；另跑 `test_theme_tokens.py`（窗口不得写裸 hex）。
- 2026-11-19 审查修复新增回归：目标唯一性（build_layout 碰撞加 `_2`）、
  UDIM 枚举（只收 1001/1002，不收 backup）、严格帧 glob（不收
  `sim.backup.bgeo.sc`）、Windows 保留包名（CON → pkg_CON）、非法 UTF-8
  配置留底 `.bak` 并回退默认。
- hython 探针（开发时用）：
  - 真实工程（`D:\VFX\HoutoolsTest\packagrTerst\untitled.hip`）：扫描出 6 个引用
    （3 张贴图含 1 张 `$HFS` 内置 hdri、filecache 缓存 50 帧、2 个缺失路径），
    缓存目标是 `$HIP/assets/geo`，复制计划 53 个文件；
  - 合成工程：filecache 缓存 3 帧，另存模式（basedir → `$HIP/assets/geo`、
    `sopoutput` 保存后能解析到包内文件、zip 含缓存）、覆盖模式
    （basedir → `$HIP/ht_package/assets/geo`、hip 不另存）、失败路径
    （模拟复制抛错 → 状态"失败"、日志含源路径与原因）。

## 2026-11-19 代码审查修复记录

针对第三方审查报告（9 项）的处理，全部有代码 + 冒烟/hython 验证：

| 编号 | 问题 | 处理 |
|---|---|---|
| P1-01 | 包外目录展平碰撞、静默覆盖 | `collect.build_layout()` 统一目标/复制映射，重名目录自动 `_2/_3`；窗口表格、预览、打包共用 |
| P1-02 | 换 hip 后沿用旧打包快照 | 改为“打包会话”记录（base/pkg/package_dir/源 hip/保存 hip），只有复制+改写+保存全成功才登记；`_on_scan` 清理不匹配会话，`_on_zip` 二次校验并拒绝 |
| P2-01 | 不支持 UDIM | `paths.has_udim_token` + `collect._udim_files`（严格 4 位 Tile），目标保留 `<UDIM>` 模板 |
| P2-02 | ZIP 未过滤符号链接/junction | `_ZipWorker` 用 realpath 判边界，包外链接跳过并计数 |
| P2-03 | 非法 UTF-8 绕过 `.bak` 恢复 | `JsonStore.load()` / `_read_disk_locked()` 捕获 `UnicodeError`：留底 `.bak` 后回退默认；留底失败拒绝写入 |
| P2-04 | 扫描阻塞 GUI | 拆 `gather()`（主线程 HOM）+ `resolve()`（`_ScanWorker` QThread 文件系统）；关闭窗口沿用协作式取消 |
| P3-01 | filecache glob 过宽 | `_digit_files()` 严格 `^前缀\d+后缀$` 二次过滤 |
| P3-02 | 保留设备名未处理 | `sanitize_pkg_name()` 检测 CON/PRN/AUX/NUL/COM1-9/LPT1-9，加 `pkg_` 前缀 |
| P3-03 | COM 初始化未释放 | `reveal._shell_select()` 用 try/finally 配对 `CoUninitialize`（S_OK/S_FALSE 都算成功） |

## 2026-11-19 合并后复审修复（PR #6 已合并，另起本地修复）

PR #6 合并进 `main`（`5417fec`）后按第三方审核结论继续收紧，改动只落在
package 模块与其冒烟测试：

| 级别 | 问题 | 处理 |
|---|---|---|
| 高 | `collect()` 串联入口把**未展开**的 `hip_dir` 交给 `resolve()`，`gather()` 内展开的 `$HIP` 是死变量——无窗口调用时 `$HIP/galleries` 排除失效、`in_hip` 全 False、`$HIP` 内素材被展平 | `collect()` 先展开 `$HIP` 再同时传给 `gather`/`resolve`；`gather()` 去掉死代码，`hip_dir` 仅保留签名兼容 |
| 高 | `_session_matches` 第三条「当前 hip 在包目录内即放行」过宽，包目录里另存/打开别的 hip 仍会把旧工程打进 zip | 抽纯函数 `collect.session_matches_hip()`，只认 `source_hip`/`saved_hip` 精确匹配；窗口包装它，删掉宽松分支 |
| 中 | `build_layout` 把 `relpath` 结果直接拼进 `file_sub`，`sopoutput` 指到 `basedir` 之外时 `normpath` 会把复制目标写出包目录 | 显式拒绝带 `..` 的 `rel_dir`，并对每个目标做 `is_within(target, <包根>/<包名>)` 兜底 |
| 中 | `plan` 字典对「同一源文件被映射到两个不同目标」静默覆盖 | 冲突不再覆盖，统一收进 `conflicts`；`build_layout()` 改返回 4 元组，窗口发现冲突即中止打包并在结果面板逐条列出 |
| 中 | UDIM 只校验 `\d{4}`，`0001/2001/9999` 会被当 Tile；`<UDIM>` 与 `$F` 同现会把 `__HTFRAME__` 写进目标 | `_udim_files()` 补 1001–1999 数值判定；`_expand()` 对混合模板返回 None（拒绝） |
| 中 | zip 只统计文件链接、目录链接不计数；空包仍报「完成」 | 抽纯函数 `collect.archivable()` 供文件/目录共用；目录链接计数并从 `dirs` 摘掉；`count==0` 改「空包」告警态 |
| 低 | 重名 note 对同目录每条引用重复计数；UNC `//` 路径被 gather 提前判 noise；`_drop_stale_session(hip_dir)` 死参数 | note 按源目录去重、node-path 判定补 `not startswith("//")`、去掉死参数 |

**已知边界补充**：`gather()` 仍在主线程全量遍历 HOM 并逐参数 `expandString`，
两段式扫描只把文件系统 IO 移出主线程，超大场景仍可能有可感知停顿；
`<UDIM>` 与 `$F` 同时出现的路径模板不支持（直接跳过，不再写坏参数）；
`<UVTILE>` 仍不支持。

**回归**：`tests/smoke_test.py` 新增 `collect()` hip_dir 串联、会话精确匹配、
UDIM 范围/混合模板、布局越界与重复目标冲突、zip 归档边界纯函数等断言；
非法 UTF-8 用例补 `try/finally` 清理，不再在配置目录累积 `.bak`。

## 2026-11-19 H21 窗口构造崩溃（PySide6 6.5 包装类型粘连）

**现象**：H21 上打开 Package Project 直接崩，`_build_ui()` 里
`header.setSectionResizeMode(...)` 报
`AttributeError: 'PySide6.QtWidgets.QAbstractScrollArea' object has no
attribute 'setSectionResizeMode'`。traceback 显示 `python3.13libs/...`
是假象——`python3.11libs` 只是入口壳，`__path__` 指向真身，H21 加载的
业务模块 `__file__` 同样落在 `python3.13libs`，不能据此判断 H22。

**根因**：H21 自带 PySide6 **6.5.3** 的 shiboken 按「首次包装时请求的
类型」缓存 C++ 指针的 Python 包装；`QHeaderView*` 若先被包成祖先类
`QAbstractScrollArea`，之后 `horizontalHeader()` 一直返回该旧包装，
派生 API 全不可见。H22 的 **6.8.3** 按 C++ 真实类型解析（连显式
`wrapInstance(ptr, QAbstractScrollArea)` 都返回 QHeaderView），故不复现。
本机双版本无头探针已验证：6.5.3 降级后报的正是上面那条
AttributeError，重包后 `setSectionResizeMode` 恢复可用。

**处理**：新增 `houtools/ui/qtcompat.py` 提供 `rewrap(obj, py_type)`
（按 `shiboken6.getCppPointer` 重包，已是目标类型则短路；失败告警后
返回原对象）。`package/window.py` 与 `recipelib/browser.py` 取表头后
统一重包成 `QHeaderView`，并用 `hasattr` 能力探测守卫——最坏情况只是
少几列自适应策略，窗口仍可打开。`ui/dialogs._rewrap` 改为委托共享层
（对话框标准按钮的同款坑），名字保留。

**回归**：`tests/smoke_test.py` 固化降级包装→重包恢复派生 API、
H22 短路零开销、重包分支指针传递，以及窗口实例化后列 0 Fixed/34px、
列 4 Stretch 确实生效。H21 6.5.3 端到端实测窗口构造通过
（`col0=Fixed / col4=Stretch / width0=34`）。
## 2026-11-19 接手审查（ZCode，b7dda05 复核）

WorkBuddy 的「类型分类 + 8 项体验改进」提交整体复核通过：

- refs/targets 索引对齐正确（`_on_package` 里 `targets = [all_targets[i] for i in selected]`
  与所选 refs 一一对应，日志逐条目标不串行）；
- `package_log.txt` 在 `_on_copy_done` 落盘，zip 是独立按钮、走
  `_pack_session` 校验，日志必然先于归档写入、随 zip 带走；
- 分类去重（raw 首段 == category 不重复前缀）、UDIM 与 `$F` 混合模板拒绝、
  `selection_summary` normcase 匹配等均与既有回归断言一致。

**修正一处**：打包确认弹窗此前对覆盖模式 + 自定义包根也说「改写为相对路径」，
实际该组合只能写绝对路径（不随包移植）——现按 生成方式 × 包根 区分措辞
（另存 / $HIP / $JOB → 相对路径；覆盖 + 自定义 → 绝对路径并注明不移植）。

## 2026-11-20 用户实测反馈（H22 真机，pkg-enh 首轮）

### ① `$HIP/...` 正斜杠序列全部误判「缺失」（高，已修）

**现象**：karma `picture` = `$HIP/render/thrusterfx/thrusterfx.$F4.exr`，磁盘
18 帧都在，扫描却标缺失（旧截图里同参数还只显示过 1 帧）。

**根因**：`$HIP` 展开成 `D:/...` **正斜杠**，Windows 上 `glob.glob` 返回的
最后一层分隔符是 **反斜杠**（`os.path.join` 拼 basename 所致）——
`_digit_files`/`_udim_files` 的严格正则按原样用 prefix 拼，混合分隔符
一条都不中 → files 空 → `Ref.missing`。filecache 没中招是因为 basedir
过了 `os.path.normpath`（统一反斜杠）。冒烟此前全用 `os.path.join`
（反斜杠）造数据，测不出正斜杠形态。

**修复**：`collect._norm_slash()` 把 pattern 与 glob 结果统一成正斜杠再
匹配；`_digit_files`、`_udim_files`（UDIM + 正斜杠参数同样会 0 Tile）
都走它。hython 对真实工程复现：修复前 0 帧，修复后 18 帧。
冒烟新增「mixed separator glob」组（正斜杠 prefix + 磁盘反斜杠文件）。

### ② 打包日志瘦身（复制计划按目录聚合）

`collect.compact_copy_plan(plan)`：同一对（源目录 → 目标目录）的搬运合并
成一行 + 文件数（`D:\...\v1 -> <包>\geo\pyro_thruster_plasma\v1（50 个文件）`），
散文件保持逐条；`build_package_log` 的复制计划段改用它。150 帧序列不再
刷几百行。纯函数，冒烟断言「序列帧不逐文件列」。

### ③ 表格字号可调（Ctrl+滚轮；右键菜单按用户要求已去掉）

- **Ctrl+滚轮** 在表格上缩放字号（7–24pt 夹紧），行高
  `resizeRowsToContents()` 跟随；`_fill_table` 末尾也调一次，重扫后
  新行保持当前字号。
- 右键「表格字号」菜单 2026-11-20 按用户要求移除——缩放只留 Ctrl+滚轮。
  右键菜单后来又加了批量勾选项（见 ⑦），现在是「勾选 / 取消 / 反选所选」
  + 「跳转节点 / 打开原路径」。
- 基线字号 `_table_default_pt` 在 tool_fonts.apply 之后取一次；只影响
  本窗口会话，不持久化（窗口复用重建即回默认）。

### ④ 列宽直接拖动（2026-11-20 用户要求）

- 数据列（节点/参数/参数现值/打包后参数值/状态，2026-11-20 表头由 原路径/目标 改名）全部 `Interactive`——之前
  `ResizeToContents`/`Stretch` 模式 Qt 不响应拖动，用户拖不动；勾选列
  仍 `Fixed` 34px；`setStretchLastSection(True)` 让状态列吸收余宽，
  其余列宽保持用户拖过的值。
- 初始宽度 200/90/300/300，用户可自行调整；冒烟断言已同步
  （6 列、0=Fixed/34px、1–5=Interactive、stretchLastSection、col4=300）。

### ⑤ 「含空输出路径」勾选移除（2026-11-20 用户拍板）

- 未落盘的输出既不会被复制也不会被改写（缺失引用打包时跳过），勾了
  只多一行「缺失」、无可操作价值——移除，扫描固定只收已落盘输出。
- `collect.gather/resolve/_ScanWorker` 的 `include_output` 参数保留
  （无窗口调用/测试用），窗口固定传 False；跳过统计 not_written
  照常记，结果面板仍会显示「未落盘输出 N 个」。

### ⑥ 「类型」列并入「状态」列（2026-11-20 用户拍板）

- 表格 7 列 → 6 列：`_COL_KIND` 删除、后续列号前移（节点/参数/参数现值/
  目标/状态），`Ref.label` 属性保留（日志/调试用）但表格不再展示。
- 合并后的状态文案吸收两列信息：缺失 → 「缓存缺失/引用缺失」（kind
  前缀保留）；filecache → 「缓存目录 · N 帧」；UDIM → 「`$HIP` 内|包外 ·
  UDIM N 块」（此前状态列把 UDIM 误写成「序列 N 帧」，顺手修正）；
  序列 → 「`$HIP` 内|包外 · 序列 N 帧」；散文件 → 「`$HIP` 内|包外 ·
  N 个文件」。
- 行色逻辑不变：缺失红、缓存目录全亮、`$HIP` 内弱化（TEXT_3）。

### ⑦ 勾选改为 Windows 文件夹式多选（2026-11-20 用户拍板）

用户反馈「多选后不可能一起勾选」。调研结论：**全库没有可复用的同类实现**
（唯一带复选框的表格就是本工具自己；其它列表都是默认 `SingleSelection`，
Automation 甚至是手写单选），因此按用户要求参考 Windows 资源管理器交互：

- `QAbstractItemView.ExtendedSelection` + `SelectRows`：Ctrl 加选 /
  Shift 连选 / 拖拽框选；`Ctrl+A` 全选行。
- **选中多行后点任一行的勾选框 → 整批一起切换**（Windows 语义）。
  坑：Qt 在点勾选框时会把 selection 收到当前行，等 `itemChanged` 触发时
  选区已经没了——所以在 `eventFilter` 的 `MouseButtonPress` 里先把选区
  快照存进 `_check_batch_rows`，`_on_item_changed` 再用它做批量。
- **空格键**切换选区：全勾则整批取消，否则整批勾选。
- **右键菜单**：勾选 / 取消勾选 / 反选所选（N 项）+ 原有跳转节点 /
  资源管理器打开原路径；右键点在未选中的行时先选中该行。
- **点表头勾选列** = 全选/全不选（对应 Windows 详细信息视图表头复选框）。
- 批量改 `setCheckState` 一律用 `_filling` 标志包住，避免 `itemChanged`
  逐行回灌；复用 `_set_rows_checked()` / `_invert_rows()`。
- 原有「全选 / 清空」按钮保留（作用于全部行，与表头等价）。
- 表格级 tooltip 仍不设（用户反馈空白区弹 tooltip 干扰视线），交互说明
  挂在「全选」按钮 tooltip 上。
