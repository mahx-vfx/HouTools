# HDR 库浏览器

适用：`houtools/hdrlight/browser.py` + `tools/hdr_library.py`（AGENTS.md「HDR 库浏览器」行的细节展开）。

## 库与侧栏

- 菜单 Hdr Library；递归扫描 `.hdr/.hdri/.exr`，第一级子文件夹即分类（左侧侧栏切换 全部/收藏/各分类，侧栏右键可新建/打开分类文件夹，空分类也列出）。
- 侧栏行样式与 Recipe Library 分类板块同款（共享 `houtools/ui/sidebar.py` 的 SidebarDelegate：行首图标 grid/star/folder + 名称 + 右对齐计数、选中蓝底圆角，计数/图标经 ItemRole 携带，行底色/视口同色 #26262b）。
- 右键收藏 HDR（存配置目录 `hdr_library.json` 的 `favorites`，normcase 路径），侧栏"★ 收藏"过滤。
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
- 调 `$HFS/bin/hoiiotool` 生成（缓存 256px 存库目录 `.thumb_cache/`，按相对路径+扩展名命名避免同名/同名不同扩展冲突，有效性按**状态等值**判定——见「扫描快照与打开秒开」节，先写 `~tmp_<线程id>_*.jpg` 临时文件（线程标识防刷新时新旧两池并发覆写同一临时文件）再原子改名（临时名必须保留 `.jpg` 后缀——OIIO 靠扩展名选输出写入器，`.part` 之类未知后缀会让 hoiiotool 直接失败；坑：`--resize` 必须写全 WxH，`--colorconvert` 需 OCIO 配置、缺失时自动注入 `$HFS/packages/ocio/`，glob 结果须 sorted 再取 [0]）。
- **扫描不完整 ⇒ 不做破坏性清理、不覆写快照**（2026-11 复审修复）：`_scan_library` 返回第 4 个值 `complete`——根目录 stat 失败、任一子树列表失败、任一文件 stat 失败都置 False（SMB 抖动/权限瞬变/盘符瞬断）。不设闸时失败子树的缩略图不在 expected_names 里，会被 clean_stale_thumbs 当"无主"**整批物理删除**（根目录失败时清空整个 .thumb_cache），快照也被覆写成残缺版——NAS 上重生成是小时级成本。reload 在不完整时保留现有列表、压回 `_need_reload` 让下次显示自动重试；`clean_stale_thumbs` 自扫兜底路径同样过闸。
- **刷新先 `_stop_worker` 打断旧线程再 `clean_stale_thumbs`**（顺序反了清理会把在途的 `~tmp_` 临时文件当无主缩略图删掉，旧线程 os.replace 失败——单张异常有兜底但那张白生成）。
- **运行中的线程由模块级 `_LIVE_THREADS` 留引用**（NameError 守卫活过 Reload，同 automation `_LIVE_ENGINES`；线程无 parent 只保证"父窗口死不掉它"，不保证"没人引用就不被 GC 析构"——Qt6 下析构运行中的 QThread 直接 qFatal）。
- **hoiiotool 路径/OCIO 环境只在主线程解析**（`_oiiotool_path`/`_ocio_env` 会调 `hou.text.expandString`，启动线程前解析好作构造参数传入——工作线程内零 HOM 调用，线程结构上保证）。
- **缓存名对含 `__` 的相对路径追加 md5 前 8 位后缀**（`__` 连接非单射，`a__b/x.hdr` 与 `a/b/x.hdr` 曾互相覆盖；不含 `__` 的命名不变，旧缓存不受影响）。
- **`clean_stale_thumbs` 对 `~tmp_` 前缀在途临时文件一律跳过**（2026-10 复审修复）：reload 先 `_stop_worker` 打断旧线程，但中断按 1s 粒度生效、子进程 kill+收尾可超 1s，`wait(1000)` 未必等到线程真正退出——"先停再清"的顺序防护升级为内容防护，旧线程随后的 os.replace 才不会对着被删的临时名失败。`.part` 是历史版本残片照删。
- **`_image_size` 的失败告警过中断守卫**（同 make_thumbnail）：停止/关窗/刷新 kill 掉的 `--info`（rc=-1）属有意行为，不记假告警。
- **「打开所在文件夹」的 `/select,` 与路径并成单 token**（2026-10 复审修复）：拆两个参数时 list2cmdline 产出 `explorer "/select," "C:\..."`，部分 Windows 版本不选中文件。
- **新建分类校验追加结尾点/空格与"保留名+扩展名"**（2026-10 复审修复，与 automation `_sanitize_config_name` 同步）：Win32 CreateDirectory 剥掉结尾点/空格，实际建出 `abc` 而 `_category_key` 记 `abc.`，重建侧栏匹配不到。

## 库目录

存配置目录 `hdr_library.json`（**打开自动扫描**：`_need_reload` 生命周期同 Recipe Library——构造置 True，showEvent 消费、hideEvent 置位，每次从隐藏到显示都触发扫描，但 2026-11 起扫描走**快照跳扫**：库没动就不重扫，详见「扫描快照与打开秒开」节；扫描经 `QTimer.singleShot(0)` 排队，让窗口先画出来再进主线程扫描，排队期间窗口被 Reload 销毁由 `_auto_reload` 捕 RuntimeError 兜底；未配置 lib_dir 时 reload 空态早退只提示，绝不扫文件系统；手动「刷新」语义不变）。**无默认库目录**（2026-11 移除 `default_lib_dir`/`HDR_TOOL_LIB`——HDR 库动辄数 GB，必须用户显式指定）；`list_categories`/`_iter_hdrs` 对空 lib_dir **早退返回空**（否则 reload 空态守卫里 `_rebuild_sidebar` 仍会 `os.listdir("")`，控制台每次刷 cannot list 警告）；「更换目录」对话框未配置时的起始位置建议 = `_suggested_lib_dir()`（配置目录同级的 `HouTools/hdri`，纯 QFileDialog 起点不产生扫描）。

## 扫描快照与打开秒开（2026-11）

问题：打开面板 = 主线程全量 os.walk + 每文件 2 次 getmtime（缩略图+HDR 各一）——2N 次 SMB 往返，数千张/共享盘上数秒冻结（原「已知代价备忘」）。解法三件套：**scandir 单趟扫描 + 目录 mtime 快照跳扫 + 快照命中跳过重建**，架构上零新增线程。

- **scandir 免费 stat**：Windows（含 SMB）目录枚举数据自带 mtime/size，`entry.stat(follow_symlinks=False)` 直接取缓存值、不产生额外系统调用——整库成本 = 每目录一次列表往返。旧 os.walk 丢掉 find data 再逐文件 getmtime 是 2N 次往返，是卡顿大头。`_scan_library` 一趟带回 records（rel/category/mtime_ns/size，按分类+文件名排序与旧 `_iter_hdrs` 输出序一致）+ dir_mtimes（**含根 ""——根目录文件的增删只改根 mtime，不记漏检**）+ thumb_names（`.thumb_cache` 单独一趟列表；它在目录走查里按隐藏目录跳过，必须单列）。
- **快照**：`hdr_scan_index.json`（配置目录 JsonStore，**本机盘，绝不写网络库目录**）——`{lib_dir(normcase), dirs{rel: mtime_ns}, files{rel: [mtime_ns, size]}, thumbs{rel: [mtime_ns, size]}}`；路径键统一 '/' 分隔的库内相对路径（盘符/大小写在机器间与重挂载间漂移）；损坏由 JsonStore 留底 .bak 回退空值 = 无快照全量重扫自愈，换库目录同理。mtime 用 **st_mtime_ns int**（JSON 精确往返；st_mtime float 有精度隐患）。
- **跳扫判定是等值比较**：本轮 dir_mtimes == 快照 dirs（键集与值都等）。双方都来自库盘的 stat 返回，**与本机时钟无关**——NAS 时钟快几十分钟不影响判定。设计纪律：全链路禁止出现"本机 now 减库盘 mtime"式混时钟比较（TTL 之类）。
- **三条路径**：① 会话内重开（单例窗口隐藏过、`_scan_dirs` 非 None）+ 目录**与文件状态**全等 → 复用内存连重建都跳过（字面秒开，日常主路径；文件状态比对防"原地覆盖后重开看到旧缩略图"）；② 重启后首开 + 目录全等 → 从快照重建网格（零额外文件系统访问）；③ 不等 / 无快照 / force → 真重扫（stop 旧生成线程 → clean_stale_thumbs → `_resolve_thumbs` → 存快照 → 重建 → 缺图进生成队列）。
- **缩略图有效性 = 状态等值**（替换旧"thumb mtime >= hdr mtime"比大小）：生成时记下 HDR 的 mtime+size 进快照 thumbs，当前状态 == 记录即有效。旧比大小混时钟源——缩略图由本机经 SMB 写入盖本机钟、HDR mtime 来自各自写入方，NAS 时钟偏差下刚生成的图会被永久判过期 → 每轮刷新无限重生成（存量风险顺手消除）。判定用的是**本轮扫描的新鲜文件状态**（scandir 免费带回）：HDR 被原地同名覆盖（目录 mtime 对此不敏感）在三条路径上都会现形 → 失效重生成。`_on_thumbs_done` 把最终状态回写快照（批量收尾一次原子写）。
- **file_states 恒用新鲜值的硬约束**：thumbReady 记档若取快照旧值，原地覆盖过的文件会"记录状态永远追不上新鲜状态"而被无限重生成（实现时踩过，测试固化：原地覆盖 → 失效 → 记档 = 新鲜状态）。
- **迁移采用**：无记录的历史缓存（存量库首次升级 / 快照丢失）按旧启发式沿用——缓存不旧于 HDR 即采用并立即记档，此后走状态等值；**存量库升级零重生成**。时钟差库上判不准的个别图重生成一次后记档稳定。
- **force 语义**：「刷新」按钮 = force=True 无条件全量重扫 + 回写快照——手动逃生口，对一切可疑情形（快照损坏 / NAS 时间戳异常 / 就是不信了）兜底；建分类 / 换目录走 force=False 但目录必变自然重扫（clicked 自带的 checked 参数不能传给 reload，force 在 connect 处用 lambda 显式给）。
- **坑表**：
  - **NTFS 父目录 mtime 对"新建条目"有毫秒级延迟更新**：建完文件立刻两次 stat 可能差 1ms 或变更未物化——偏移只会造成"多做一次重扫"（自愈）或"加完文件瞬间重开面板漏检一次"（下次打开必抓到），永远不会把已物化的变更假判成"未变化"；测试里 touch 后必须 os.utime 定桩，否则比对测试确定性抖动。
  - **`.thumb_cache` 首次创建会改根目录 mtime**：首轮生成完的那次打开会多做一次重扫（快照里的根 mtime 是建缓存前的），自愈一次后稳定。不做精细调和——要区分"根变化是建缓存还是用户加了文件"必须重新枚举根，mtime 无法区分，不值得为一次冗余重扫加复杂度。
  - 生成线程回写内存 thumb_states 用**本轮扫描的 file_states**（生成队列就是按它排的）；路径不在本轮记录 = 旧线程余量，只刷图标不记档（thumbReady 无 sender 过滤，靠"rel in file_states"守卫兜住）。
  - 侧栏分类改从目录表推导（`_rebuild_sidebar`），`list_categories`（listdir+isdir 逐项探测，网络盘 K 次往返）仅剩独立辅助用途。
  - 跳扫路径不做 clean_stale_thumbs（维护动作只在真重扫时发生）；外部整个删掉 `.thumb_cache` → 根 mtime 变 → 重扫 → 重生成，自然成立。
  - **盲区清单**（会话内重开的复用路径感知不到，「刷新」兜底）：外部手动删/换单张缩略图（图标滞旧/空占位直到下次真重扫）；favorites 被外部编辑（hdr_library.json）。HDR 原地同名覆盖**不在盲区**——文件级状态比对三条路径都检出（见上）。
  - 测试：`_SCAN_INDEX` 用临时 JsonStore patch（同 `_SETTINGS` 手法）；smoke 新增「HdrLibrary scan snapshot OK」（跳扫三路径 + 新增触发重扫 + 原地覆盖检出）与「HdrLibrary thumb state semantics OK」（`_resolve_thumbs` 等值/失效/迁移纯函数单元测试）。

## 已知代价备忘（2026-11 复审）

- ~~「刷新」的全量 os.walk + 逐文件 stat 在主线程~~ **已解决**（2026-11）：扫描换 scandir 单趟（免费 stat）+ 快照跳扫后，真重扫只剩每目录一次列表往返；跳扫路径连网格重建都跳过。见「扫描快照与打开秒开」节。
- 共享盘 SMB 短暂失联时 scandir 在主线程阻塞（与旧 os.walk 同暴露面，未引入新风险）；库目录数极多（上千分类）时跳扫检查本身可达秒级——用户实际结构为两级浅层、几十个分类，不处理。
- 重建网格（N 个 QListWidgetItem 构造）在数千张时约百 ms 级，重启后首开仍有此成本；会话内重开已完全跳过。暂不 model/view 化（改动大收益小，与刚体网格实现冲突）。
