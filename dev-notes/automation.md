# Automation 执行引擎与面板接入

适用：`houtools/automation/execution_engine.py`、`houtools/automation/window.py`（AGENTS.md「自动化执行引擎」「界面接入（Python Panel）」两行的细节展开）。

## 执行引擎（execution_engine.py）

- QThread；Houdini API 经 `hdefereval.executeDeferred` + Event 同步派发主线程。**无超时同步等待是有意设计、禁止加超时**（原因与取消语义见 AGENTS.md Conventions「Automation 的同步等待」）。
- `dl_Submit` 特例：工程从未保存过（hasNeverSaved）直接报错跳过（否则 hipFile.save 弹"保存工程"对话框阻塞无人值守队列），点击前强制 `hipFile.save()`，失败则跳过点击。
- Flipbook 帧范围解析失败 warning 后沿用场景原范围。
- **flipbook 设置必须 `flipbookSettings().stash()` 复制后再改**（与 capture_viewport_embed 同一手法——直接改活对象会把用户的输出路径/帧范围永久留脏，save_to_disk=False 还会把用户输出路径抹成空串）。
- 失败任务的 task_completed 耗时上报真实耗时（曾硬编码 0）。
- `_execute_open_dw` 的 Popen 带 CREATE_NO_WINDOW（防配置成控制台程序弹黑框）。

## 界面接入（window.py，Python Panel）

- `create_panel_widget` / `open_floating_panel`（`hou.pypanel.installFile` + `createFloatingPanel`）；节点拖放为 Houdini 原生投递（MIME text 是逗号分隔的节点路径）。
- **运行态收尾单点归 `_on_all_completed`**：closeEvent 不复位 `_running`（面板是 pane 子部件收不到 close，复位会造成"假空闲"）。
- stdout 重定向恢复只判 `_orig_stdout` 是否存在（不看日志开关现值），Start 前预还原防 `_LogTee` 嵌套。
- 未知任务类型渲染为禁用占位槽并**原样透传保存**（不落默认 BUTTON_CLICK）。
- **stdout 重定向的生命周期必须不依赖面板存活**：tee 是进程级替换，面板执行中被销毁时（pane 可关、收不到 closeEvent）实例的 `_restore_stdout` 不可达——`destroyed` 连模块级 `_force_restore_stdout`（沿 `_original` 链解包）+ `_close_log_file` 兜底。
- `_LogTee` 实现 `__getattr__` 转发原始流（fileno/isatty/encoding 等被第三方库访问时不炸）。
- 执行日志句柄在 Start 时惰性打开、`_write_log` 全程持 `threading.Lock`（tee 挂在 sys.stdout 上任意线程的 print 都会进来，无锁时两线程同首调 `_get_log_path` 会拿到不同文件、日志分裂）。
