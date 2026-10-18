# 视频转序列图窗口（拖放/路径框/提取）

适用：`houtools/videoseq/window.py`（AGENTS.md「视频拖放/路径框」行的细节展开）。

## 拖放与入口

整窗 + `_VideoSourceGroup` 双层接收拖放；`_load_video` 是浏览/拖放/手输共用入口。

## 取消与子进程（踩坑重灾区）

- 提取取消为协作式：探测阶段（ffprobe 30s/ffmpeg 回退 120s 超时，期间点停止 ffmpeg 不得启动）与 Popen 前各查一次 `_cancelled`，收尾 terminate+`wait(15s)`+kill 兜底。
- **无人读 stdout 时 ffmpeg 会卡死在 progress 管道写上（缓冲区约 4KB 填满即停），裸 `wait()` 会永久挂起线程，而窗口 closeEvent 靠轮询 `isRunning()` 延迟关闭，线程挂起=隐藏的窗口永不关闭。**
- **stderr 用 DEVNULL**（stderr 管道无人读是同款挂死、换了个管道）。
- 所有子进程 `text=True` 一律 `encoding="utf-8", errors="replace"`（中文 Windows 默认 cp936 会解码失败）。
- **取消语义对齐 Automation「停止中…」**：点停止后按钮禁用直到该 worker 的 done/error 到达（按 sender 过滤迟到信号）才复位；`_on_convert` 不覆盖运行中的 worker；换片取消旧探测 worker 后挂 `_retired_workers` 集合保引用（内建 finished 自动摘除）防 GC 析构运行中 QThread。

## 转换流程

- 开工前扫描输出目录同前缀旧序列并询问是否清除（防新旧帧混杂），完成帧数只报 ffmpeg progress 的 frames_done；取消时提示已写入帧数。
- closeEvent 对探测线程只 cancel 不 wait（子进程不可中断，wait 必然白等）；实现 `is_close_deferred()` 配合 window_manager（见 AGENTS.md「窗口单例」行），延迟关闭收尾自删；showEvent 撤销未决的延迟关闭。
- **转换不再重新探测**（窗口 `_video_info` 作为构造参数传入 `_ExtractWorker`，原 worker 从零重探最坏多等一次全片解码、且两次帧数可能不一致致进度条对不上）。

## 相机区

- 单相机自动选中、多相机走选择对话框，两条路都经 `_set_selected_camera` → `_refresh_camera_res` 同步分辨率行。
- **分辨率是宽/高两个独立输入框**（用户指定，不用"宽x高"合并框），显示所选相机 `resx`/`resy`（标准 cam 的分辨率就是这一对参数、无单独 `res` 参数，hython 实测），「确定」经 `parse_dimension` 逐框校验（1~5 位正整数）后写回。
- **「预设」下拉按钮在「确定」前**（QPushButton 挂 QMenu，`RESOLUTION_PRESETS` 常量表：1280×720~4096×2160 共 7 档），点选只把值填进宽/高两框、写回仍走「确定」。
- **`showEvent` 里必须重读所选相机刷新分辨率行**——单例窗口关闭只是隐藏，隐藏期间分辨率可能已在 Houdini 里被改，只在选择相机时刷新会一直显示旧值（实测踩过）；showEvent 同时清掉上次会话遗留的状态栏提示（如"已设置 … 分辨率"，用户指定不带到重开后的面板）。
- 无相机/相机已删/参数缺失时输入框与按钮禁用，写回成功后状态栏绿色反馈。
- **相机查找走 `nodeType(objNodeTypeCategory, "cam").instances()`**（原 allSubChildren() 递归全场景且在窗口构造期调用，大场景卡顿数秒）。
- **相机选择对话框下拉直接列完整路径**（按 basename 反查 index 在重名相机下永远命中第一个、分辨率写错相机）。

## 完成后写相机背景

- 转换完成后写所选相机 `vm_background`（用开工快照的原始目录文本保留 $HIP 变量）**并确保 `vm_bgenable`=1**（启用背景，用户指定）。
- **该步骤只捕 ImportError 曾让锁定参数的 PermissionError 掀翻 `_on_extract_finished`、"完成"弹窗永不出现**——现补 except Exception + 状态栏黄字（帧已导出，装饰性步骤失败不绑架主流程）。
