# Automation 执行引擎与面板接入

适用：`houtools/automation/execution_engine.py`、`houtools/automation/window.py`（AGENTS.md「自动化执行引擎」「界面接入（Python Panel）」两行的细节展开）。

## 执行引擎（execution_engine.py）

- QThread；Houdini API 经 `hdefereval.executeDeferred` + Event 同步派发主线程。**无超时同步等待是有意设计、禁止加超时**（原因与取消语义见 AGENTS.md Conventions「Automation 的同步等待」）。
- **任务循环包在 try/except BaseException/finally 里**（2026-10 审查修复）：按钮回调脚本抛 SystemExit 等非 Exception 的 BaseException 会穿透逐任务的 except Exception，漏了 finally 会让 `all_completed` 永不发——UI 永久停在「停止中…」、stdout 重定向挂到面板销毁。现在记账（fail+1）+ log.error 后吞掉，finally 必发 `all_completed`；在途任务按 cur_idx/cur_start **补发一条失败的 `task_completed`**（逐任务 except 接不住它，日志该任务有始有终），任务收账后 cur_idx 置 None 防重复补发。
- `dl_Submit` 特例：工程从未保存过直接报错跳过（否则 hipFile.save 弹"保存工程"对话框阻塞无人值守队列），点击前强制 `hipFile.save()`，失败则跳过点击。**"从未保存"判据必须走 `_hip_never_saved` 能力探测链（hasNeverSaved > isNewFile > untitled 基名）**——`hou.hipFile.hasNeverSaved` 在 H22.0.429 不存在（2026-11 真机 AttributeError，`isNull` 也没有；该版本实际 API 是 `isNewFile`，未保存过为 True），写死任何单个 API 都会在别的版本翻车。
- Flipbook 帧范围解析失败 warning 后沿用场景原范围。
- **flipbook 设置必须 `flipbookSettings().stash()` 复制后再改**（与 capture_viewport_embed 同一手法——直接改活对象会把用户的输出路径/帧范围永久留脏，save_to_disk=False 还会把用户输出路径抹成空串）。
- 失败任务的 task_completed 耗时上报真实耗时（曾硬编码 0）。
- `_execute_open_dw` 的 Popen 带 CREATE_NO_WINDOW（防配置成控制台程序弹黑框）。

## 界面接入（window.py，Python Panel）

- `create_panel_widget` / `open_floating_panel`（`hou.pypanel.installFile` + `createFloatingPanel`）；节点拖放为 Houdini 原生投递（MIME text 是逗号分隔的节点路径）。
- **运行态收尾单点归 `_on_all_completed`**：closeEvent 不复位 `_running`（面板是 pane 子部件收不到 close，复位会造成"假空闲"）。
- **「新建配置」入口与下拉切换同款运行中守卫**（2026-10 审查修复）：运行中/停止中新建同样会重建全部槽 + `_load_settings` 覆写运行设置（日志中途静默停写），此前唯独这个入口漏了。
- **stdout tee 带属主 + 收尾沿链 splice（`_strip_owned_tees`）**（2026-10 审查修复）：重定向是进程级单槽，复制停靠页签可造出第二个 Automation 面板——无属主时 W1 收尾会把 W2 的 tee 顶掉、W2 开工会剥掉 W1 的。摘除不要求自家层在栈顶：沿 `_original` 链把属主是自己（含无属主遗留）的层 splice 掉（prev._original 跳过），别的存活面板的层不动——只剥栈顶的旧写法在"W1、W2 同时执行、W1 先收尾"时会把自己的 tee 永久埋在链里、再被 W2 收尾用捕获值顶回栈顶。Start 时不再另存 `_orig_stdout`（嵌套时捕获值可能是别人的 tee，用它还原反而互踩）。
- **去重浮动面板 close 前查 `_any_panel_running`**：重复实例里有引擎在跑时本轮跳过去重（close = 协作式放弃数小时任务），空闲后再收。
- 任务槽小图标（jump/folder）模块级缓存：每槽重建 = 重复读盘 + QSvgRenderer 解析，100 槽配置每次载入付 100 倍代价。
- **跳转/取景实现已迁 `houtools/ui/networknav.py`**（2026-11，Video to Sequence 的媒体来源跳转按钮复用同款）：`_jump_to_node` 方法是委托薄壳（空路径文案经 `empty_message` 参数保持原文"该任务未填写参数路径"），模块级 `_frame_node_in_editor` 保留别名防旧引用；图标缓存仍在本模块未迁。
- stdout 重定向恢复不看日志开关现值（开关可能运行中被改），Start 前预还原防 `_LogTee` 嵌套。
- 未知任务类型**与缺失 type 字段**都渲染为禁用占位槽并**原样透传保存**（不落默认 BUTTON_CLICK——缺 type 走默认会在首次 Start 保存时把原始 params 形状永久改坏，2026-10 复审补齐）。
- **stdout 重定向的生命周期必须不依赖面板存活**：tee 是进程级替换，面板执行中被销毁时（pane 可关、收不到 closeEvent）实例的 `_restore_stdout` 不可达——`destroyed` 连模块级 `_force_restore_stdout`（沿链 splice 本属主层）+ `_close_log_file` 兜底。
- `_LogTee` 实现 `__getattr__` 转发原始流（fileno/isatty/encoding 等被第三方库访问时不炸）。
- 执行日志句柄在 Start 时惰性打开、`_write_log` 全程持 `threading.Lock`（tee 挂在 sys.stdout 上任意线程的 print 都会进来，无锁时两线程同首调 `_get_log_path` 会拿到不同文件、日志分裂）。**Start 在主线程先预热 `_get_log_path` 缓存再置 `_log_active`**（2026-10 复审补）：置位后到首次 `_write_log` 缓存路径前的窗口期里，任意线程的 print 会代调 `_get_log_path`——其内含 `hou.getenv`，HOM 跨线程无官方承诺（对齐引擎"预读后传入"约定）。
- 面板居中过滤器 `_PanelCenterFilter` 由 `_PANEL_FILTERS` 持引用保活到首次 Show 自摘，**自摘时同步 `discard(self)`**（2026-10 复审补，防无 parent QObject 连同其持有的 HOM FloatingPanel 包装滞留累积）。
- **应用级过滤器的 obj 类型守卫（2026-10-28 H21 真机实测）**：Houdini 21 会把原生 `QWindow` 的 Show 投进应用级事件过滤器（QtGui 层对象，没有 `windowTitle`；H22 未投过此类事件）——`_PanelCenterFilter` 曾直接 `obj.windowTitle()` 被 AttributeError 打崩。凡应用级过滤器的回调：先 `isinstance(obj, QWidget)` 再调部件方法，或只用 QObject/身份比对 API（Automation 的 Delete 过滤器拿 obj 比 `windowHandle()` 同一性、fonts 的 ChildAdded 走 `property()/parent()` 均属安全写法，已逐一核查）。

## 杂项（2026-11 复审修复）

- **「设置」对话框 exec 后必须 deleteLater**（全仓 exec 站点成文约定的最后一处漏网）：`QDialog(self)` 有 C++ parent，Python 局部变量出作用域不析构——每开一次往面板挂一个隐藏对话框。复选框值先取到局部变量再删，不挂 self 属性。
- **`_write_log` 的 `_log_active` 检查必须在 `_ensure_log_lock()` 之内**：它防的正是与 `_restore_stdout`（持同锁清 `_log_active`/`_current_log_path`）的竞争——检查放锁外会在竞争窗口"检查通过后路径被清空"，惰性创建重算路径、同分钟撞出 `.2` 分裂日志文件。

## 自动保存（window.py，2026-11）

- **语义**（2026-11 用户拍板，翻掉原"仅 Start 落盘/关窗不保存"）：任务 JSON **改动即存**。编辑入口统一汇 `_mark_dirty`（置脏 + 600ms 单发 QTimer 防抖——连续打字每秒至多 1-2 次写，几十 KB 的原子写本地/SMB 均无感，性能不是该改动的约束）。
- **装载守卫是第一设计点**：`_dirty_loading` 计数器（可嵌套）在程序化填充期间抑制脏标记——`_load_data` 重建槽、`_create_slot_widget` 内 populate 恢复值/类型都会触发 textChanged/currentIndexChanged，不守卫就会"打开面板即写盘"甚至把空槽覆盖回配置文件。叶子控件（parmPath/webhookUrl/flipbook 三框/saveToDisk/slotEnabled）的接线放在 populate **之后**，装载期信号天然不进脏标记；combo 的 `_on_type_changed` 走守卫（populate 恢复类型会触发它）。
- **flush 四点**：Start（取消挂起防抖后 `_save_data` 全量写）、`_on_config_changed`/`_on_new_config` 切走前、`closeEvent` 开头。**自动保存只写 `_current_config_name`，绝不走 `_save_data`**——那会按下拉当前文本决定落盘名（可能创建新文件），输入到一半的名字会被当文件名。失败只记 warning 不弹窗（后台动作不打断编辑，下次改动再试）。
- **容器级编辑显式标记**：增/删槽（非批量分支）、右键插入、数量调整、拖动实际换位（`_on_handle_moved` 里 mark——仅点击不换位不写盘）；批量重建由调用方统一标记。槽参数值编辑走创建期接线，无需逐点调用。
- **运行中编辑照常落盘**：引擎持有的是 Start 时的 TaskItem 列表，运行中的编辑不影响当前 run，落盘内容即下一轮草稿。运行中切配置/新建配置仍被既有守卫拒绝（与自动保存无关）。
