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
- 面板居中过滤器 `_PanelCenterFilter` 由 `_PANEL_FILTERS` 持引用保活到首次 Show 自摘，**自摘时同步 `discard(self)`**（2026-10 复审补，防无 parent QObject 连同其持有的 HOM FloatingPanel 包装滞留累积）。**默认位=主窗口正中再左移 1.25 个面板宽**（2026-11 用户三轮调定：半→一个半过头左缘贴屏→回拉四分之一身位；定位数学单点在 `_apply_default_position`，Show 过滤器/0ms/150ms 兜底三条路径共用；主窗较窄时面板左缘可能越出屏幕，属用户自担的可拖回状态）。
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
- **destroyed 兜底取消引擎（2026-11 复审修复）**：pane 直接销毁收不到 closeEvent，旧版 destroyed 只恢复 stdout——引擎被 `_LIVE_ENGINES` 持引用继续跑剩余队列且无法再取消。新增模块级 `_cancel_engine_on_destroy` 连接 destroyed（只碰 `owner._engine`，不触摸已销毁控件）；取消仍协作式（在途任务收尾后停，后续任务不再启动）。
- **flush 返回 bool（同批）**：`_flush_dirty_tasks` 失败时脏标记保持置位；`_on_config_changed`/`_on_new_config` 检查返回值，失败阻止切换/新建（下拉回弹+提示）——旧版失败仍继续重建槽，编辑既未保存又从界面消失。
- **Webhook URL 脱敏（同批）**：`_mask_webhook_url` 只留 scheme://host；日志头与 Timeout/Connection/HTTP 异常全部走脱敏版（HA 的 `/api/webhook/<id>` 路径即凭证，requests.HTTPError 的 str 带完整 URL 必须重抛）。
- **留底失败拒绝写入（同批）**：`AutomationDataManager._backup_corrupt` 返回 bool；留底失败（权限/占用）时 save/save_settings/ensure_dw_config 拒绝写入——原件还在原位，继续覆盖会把唯一可抢救副本冲掉。
- **上次使用配置按机器随工程持久化**（2026-11 用户拍板，原共享单文件翻案）：`{HIP}/HouTools_cfg/Automation_last.<主机名>.json`（`load_last_config/save_last_config`，tmp+os.replace 原子写；主机名经 `config_hostname` 净化）。标记放 `HouTools_cfg/` **顶层**而非 `Automation_json/` 内——后者 `*.json` 会被 `list_configs` 枚举进下拉，标记混进去污染下拉。窗口打开恢复（标记指向的配置须仍在 `list_configs`，被删回落默认=本机计算机名配置）；`_on_config_changed`/`_on_new_config`/`_save_data`（Start 改名）三处赋值点回写 `_persist_last_config`（失败只 debug——可再生缓存，失败不影响切换本身）。损坏标记只告警按未持久化处理，不做 .bak 迁移（每次切换重写）。**旧共享标记 `Automation_last.json` 只读兼容**：本机标记缺失时回读、永不回写（升级用户的上次选择不丢）。smoke 用 patch 类方法 `_hip_base` + `config_hostname`（打桩 `WS-TEST`）把配置根与机器名隔离验证。

## 多机器隔离与写盘冲突（2026-11 用户拍板）

- **背景**：多台机器共用同一工程目录时，原默认配置 `Automation.json` 是所有人共写的单一文件，「改动即存」会静默覆盖对方任务（`save()` 整文件替换 tasks、跨进程无合并基检查）。执行本身安全（引擎持 Start 内存快照，运行中外部改文件不影响本次 run），风险在写盘侧。
- **身份选材**：**计算机名（hostname）**，不是用户名（公司内网重名常见）也不是 IP（DHCP 会漂移、多网卡多 IP、无外网内网取不准）。`config_hostname()` 在 data_manager（window 不能反向 import data_manager 的下游——`_sanitize_config_name` 在 window.py，hostname 净化必须自含在 data_manager）。
- **rev 乐观并发**（不用 mtime——NAS 跨机器时钟偏差不可靠，hdrlight 有教训）：配置 JSON 顶层 `rev` 每次写盘递增（`save`/`save_settings` 都推进）；`save(..., expect_rev=)` 与盘上不符抛 `ConfigConflictError`（**不写盘、不留底**）；损坏文件留底恢复后按调用方期望放行（内容已保底 .bak，不属于"他人修改"）；`read_config_rev` 对损坏文件按 0 只 debug——留底流程那边已有 warning，rev 读点高频不刷屏。**save 的 except 链必须先 `except ConfigConflictError: raise`**——否则冲突落进通用失败分支被伪装成"写盘失败 return False"。
- **窗口侧冲突三选 `_resolve_conflict`**：`dialogs.choice`（ui/dialogs 新增的 N 按钮模态，Esc/关闭映射最后一键）弹 覆盖/另存/取消——覆盖=不检查 rev 强制写（rev 按盘上现状递增）；另存=`_prompt_save_as_name` 输入循环（sanitize 校验 + 已存在确认覆盖）落新配置并切换+回写标记+`_load_settings`；取消=清脏恢复磁盘镜像（`_load_data` 刷新 rev 基线，**不会再触发弹窗**直到对方下次改动——比"编辑留在面板下次再试"好：不留每次写盘再撞冲突的尾巴）。三结局都算 flush 已解决返回 True；False=弹窗异常/另存被放弃。`_save_data`（Start 路径）冲突解决后统一返回磁盘现状——取消结局不能拿面板旧内容起跑，那正是被放弃的草稿。弹窗触发时点在自动保存（600ms 防抖写盘）上——计算机名隔离后只剩故意共用配置才走到，频率极低可接受。
- **rev 基线跟踪**：`_config_rev` 在 `_load_data` 加载后统一刷新（切配置/新建/删除回落都经它，单一刷新点）；每次成功写盘（flush/`_save_data`/`_save_settings`/冲突覆盖/另存）后从盘上重读。同机复制页签造出的第二面板编辑同一配置 → 对方写盘后本面板下次 flush 撞冲突弹窗——属"故意并发"，三选可解。
- **「删除配置」`_on_delete_config`**（`configDeleteBtn`，新增入口——此前无删除）：目标=下拉所选（sanitize）；**确认前置覆盖幽灵项分支**（2026-11 用户反馈：删除回落后的默认配置与当前同名=幻影项、盘上无文件，恰逢防抖窗口内二次点击曾走幽灵分支**无确认直接重置面板内容**——用户刚编辑的任务瞬间消失；现任何删除路径一律先确认，幽灵项确认文案=「尚未落盘，将清空面板并切回本机默认配置」）；**确认后停防抖清脏**（删除意图优先于自动保存，防 flush 把文件原样重建；幽灵分支同样需要——挂起的定时器会把回落前的旧草稿写进默认配置文件）；`_switch_to_default_config` 入口也停防抖清脏（双保险）；文件存在的确认亮**配置名/任务数/文件路径**；`delete_config` 失败（占用）warn 保文件；标记指向被删名 → `remove_last_config`；删当前配置 → 回落本机计算机名配置（标记随后重指向默认——终态标记=WS-TEST 类，不是 None）；运行中/停止中守卫（删掉的是下一轮草稿）。**按钮点击态亮红**（`_DELETE_BTN_STYLE`，pressed `#d1283e`——破坏性操作颜色语义，2026-11 用户指定；hover 边框同步红）。
- **关闭/隐藏收尾 flush 失败 toast `_warn_flush_failed`**：closeEvent 与 hideEvent 都检查 flush 返回值，失败 toast「配置 X 保存失败，未落盘的修改可能丢失」。**不拦关窗**——停靠页签关闭无否决通道（收不到可否决的 closeEvent），且盘满/权限坏掉时把用户锁在关不掉的窗口里更糟；提示作用是让用户明白"为什么改动没了"。**去重 `_flush_warned`**：closeEvent 后紧随 hideEvent 双触发，一次可见周期只告警一次，showEvent 重置。编辑期 flush 失败维持只记日志不打断（会随下次编辑自动重试）。
- **工具栏两排布局**（2026-11 用户指定）：第一排=配置下拉/新建/删除/设置（右对齐），第二排=数量控件 + 〈20px 固定间距〉+ **执行/自动填充/清空**（单排过挤；间距作呼吸感——紧贴加减号太挤，用户指定）。**窄面板防挤压**：工具栏总最小宽超出面板宽时 QBoxLayout 会把各项压到**最小提示以下**（此前 combo 的 `setMinimumWidth(160)` 也守不住，实测被压到 143，文字与相邻按钮挤在一起视觉重叠）。修法：六个工具栏按钮 `setMinimumWidth(sizeHint().width())` 锁定文字宽度，下拉框改为唯一让位项（minW=36 仅箭头，自身文字超宽在自己框内裁剪）；注意 `sizeHint` 要在字体应用后取（`tool_fonts.apply` 在 `_build_ui` 前）。
- smoke 新增：按机器标记断言/旧标记兼容/rev 递增与冲突不写盘/损坏放行/窗口级三选（patch `dialogs.choice`/`prompt_text`，**注意 `_flush_dirty_tasks` 前必须 `_mark_dirty`**——无脏标记 flush 空跑返回 True 根本不进冲突路径，曾漏）/删除三态/关闭 toast 去重（`call_count == 1`）。gen_about_shots 的 `shot_automation` 补 patch `config_hostname → "Automation"`（默认配置名才能对上演示数据文件）。
- **日志默认保存**：`log_to_disk` 缺省翻为 True（`__init__` 初值 + `_load_settings` 双处），配置文件显式 false 才关闭——用户既有配置里已写 false 的保持关闭（持久化语义优先于默认值）。设置对话框复选框、tooltip 不变。
- **完成摘要固定四段**（2026-11 用户拍板）：`... — 总共 N，成功 X，失败 Y，取消 Z`，总共 = 成功+失败+取消（本次运行的启用任务数，禁用任务不计），取消 0 也显示。引擎协作式取消生效点（循环顶部 `_cancelled` 分支）统计 `sum(enabled for tasks[idx:])` 记入 `_cancelled_count`（在途任务已完成收尾、计入各自结果，不计入取消数）；`_on_all_completed` 在 `finally` 复位 `_engine` **之前**读取——print 经 tee 同落日志与控制台（两条通道同一条输出，无需单独 logger 行）。
