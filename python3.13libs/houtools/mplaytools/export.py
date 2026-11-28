"""序列图 → MP4：输出路径、版本升级、ffmpeg 命令与执行。

复用 ``houtools.videoseq.ffmpeg`` 的 ffmpeg 查找链与子进程启动参数
（HOUTOOLS_FFMPEG → PATH(hffmpeg 优先) → $HFS/bin/hffmpeg；
Windows 隐藏控制台 + stdin=DEVNULL），并沿用 videoseq 的两条规则：
EXR 输入加 ``-apply_trc iec61966_2_1``（线性转 sRGB）、子进程文本一律
utf-8 + errors=replace。

与 videoseq 的**方向相反**，输入策略另定（本机 hffmpeg 7.1.0 实测）：

* hffmpeg 的 libavformat **不支持 ``-pattern_type glob``**（报
  "globbing is not supported by this libavformat build"）；
* image2 ``-start_number`` 模式遇到缺帧会**静默断流**（rc=0，只出到
  缺口前），实测 4 帧缺 1 帧只写出 2 帧；
* 因此统一走 concat demuxer 的**显式帧列表**，缺帧/混合 padding/路径含
  ``%`` 全部免疫。concat 默认按 25fps，必须同时给 ``duration`` 行和
  ``-r <fps>``（实测只给 duration 会得到 25/1，两个都给才是 24/1）。
* SideFX hffmpeg **没有 libx264**，只有 libopenh264 / h264_nvenc /
  h264_amf；按 ``-encoders`` 实测挑选，libx264 仅自定义 ffmpeg 时才用。
"""

import os
import re
import subprocess
import sys
import tempfile
import threading
import types
from datetime import date

from houtools.core.log import get_logger
from houtools.mplaytools.session import DEFAULT_FPS
from houtools.videoseq.ffmpeg import find_ffmpeg, get_startup_kwargs

logger = get_logger("mplaytools.export")

PREFERRED_CODECS = ("libx264", "libopenh264", "h264_nvenc", "h264_amf")
FALLBACK_CODEC = "libopenh264"          # hffmpeg 自带，保证兜底可用
OUTPUT_FOLDER_NAME = "Flipbook"
OUTPUT_EXTENSION = ".mp4"


class MPlayExportError(RuntimeError):
    """输出路径、编码器探测或 ffmpeg 执行失败。"""


class MPlayExportCancelled(MPlayExportError):
    """用户取消转换；调用方不应作为错误提示。"""


def versioned_output_path(hip, hipname, day=None, listdir=None):
    """返回 ``$HIP/Flipbook/YYYY-MM-DD/$HIPNAME.vNNN.mp4`` 的下一个版本。

    只扫描目标日期目录里同名的 ``.vNNN.mp4``，取最大版本号 +1；
    ``v009 → v010``、``v999 → v1000`` 自然进位，不会覆盖已有成片。
    目录尚不存在时从 v001 开始（目录由 run_export 创建）。
    """
    if not hip or not hipname:
        raise MPlayExportError("输出路径需要 $HIP 与 $HIPNAME")
    day = day or date.today()
    folder = os.path.join(hip, OUTPUT_FOLDER_NAME, day.strftime("%Y-%m-%d"))
    pattern = re.compile(
        r"^" + re.escape(hipname) + r"\.v(\d+)" + re.escape(OUTPUT_EXTENSION) + r"$",
        re.IGNORECASE)
    try:
        names = listdir(folder) if listdir is not None else os.listdir(folder)
    except OSError:
        names = []
    highest = 0
    for name in names:
        matched = pattern.match(name)
        if matched:
            highest = max(highest, int(matched.group(1)))
    return os.path.join(folder, "%s.v%03d%s" % (hipname, highest + 1,
                                                OUTPUT_EXTENSION))


def concat_quote(path):
    """concat 文件的单引号转义（ffmpeg 约定字面单引号写成 '\\''）。"""
    return "'" + path.replace("'", "'\\''") + "'"


def concat_text(files, fps):
    """生成 concat demuxer 帧列表；``duration`` 与 ``-r`` 必须同时给。"""
    duration = "%.9f" % (1.0 / fps)
    lines = []
    for path in files:
        normalized = path.replace("\\", "/")
        lines.append("file " + concat_quote(normalized))
        lines.append("duration " + duration)
    return "\n".join(lines) + "\n"


def pick_encoder(encoders_text, preferred=PREFERRED_CODECS,
                 fallback=FALLBACK_CODEC):
    """从 ``ffmpeg -encoders`` 输出里挑第一个可用编码器（纯函数）。"""
    for name in preferred:
        if re.search(r"(?<!\S)" + re.escape(name) + r"(?!\S)",
                     encoders_text or ""):
            return name
    return fallback


def detect_encoder(ffmpeg, runner=None):
    """实测可用编码器；探测失败静默落 hffmpeg 自带的 libopenh264。"""
    def _default_run(cmd):
        return subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=20,
            **get_startup_kwargs())
    run = runner or _default_run
    try:
        done = run([ffmpeg, "-hide_banner", "-encoders"])
        return pick_encoder(getattr(done, "stdout", "") or "")
    except Exception as exc:
        logger.warning("编码器探测失败，回退 %s：%s", FALLBACK_CODEC, exc)
        return FALLBACK_CODEC


def build_ffmpeg_command(ffmpeg, concat_path, output, fps, codec, exr=False,
                         progress=False, resolution=None):
    """拼装序列图 → mp4 命令（纯函数，供冒烟测试固化）。"""
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if progress:
        cmd += ["-progress", "pipe:1"]
    if exr:
        # EXR 是线性编码，默认直通会全片偏暗；解码器选项必须放在 -i 前
        cmd += ["-apply_trc", "iec61966_2_1"]
    cmd += ["-r", "%g" % fps, "-f", "concat", "-safe", "0",
            "-i", concat_path]
    # H.264 要求偶数宽高；指定分辨率先缩放，奇数尺寸再裁掉最后一列/行
    filters = []
    if resolution:
        width, height = int(resolution[0]), int(resolution[1])
        if width != 0 and height != 0:
            filters.append("scale=%d:%d:flags=lanczos" % (width, height))
    filters.append("crop=trunc(iw/2)*2:trunc(ih/2)*2")
    cmd += ["-vf", ",".join(filters)]
    cmd += ["-c:v", codec, "-pix_fmt", "yuv420p"]
    if codec == "libx264":
        cmd += ["-crf", "20", "-preset", "veryfast"]
    else:
        # libopenh264 / nvenc / amf 不认 -crf，走固定码率
        cmd += ["-b:v", "10M"]
    cmd += ["-movflags", "+faststart", "-y", output]
    return cmd


def _run_with_progress(command, total_frames, fps, callback, cancel_event=None):
    """流式读取 FFmpeg 进度；stderr 落临时文件，避免管道写满死锁。"""
    if cancel_event is not None and cancel_event.is_set():
        raise MPlayExportCancelled("转换已取消")
    last_percent = -1
    total_us = max(1.0, total_frames / fps * 1000000.0)
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errors:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=errors,
            text=True, encoding="utf-8", errors="replace",
            **get_startup_kwargs())
        stop_watcher = threading.Event()
        watcher = None
        if cancel_event is not None:
            def _watch_cancel():
                while not stop_watcher.wait(0.1):
                    if cancel_event.is_set():
                        if process.poll() is None:
                            process.terminate()
                        return

            watcher = threading.Thread(target=_watch_cancel, daemon=True)
            watcher.start()
        try:
            frame = None
            elapsed_us = None
            for raw_line in process.stdout:
                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise MPlayExportCancelled("转换已取消")
                line = raw_line.strip()
                key, separator, value = line.partition("=")
                if not separator:
                    continue
                try:
                    if key == "frame":
                        frame = int(value)
                    elif key in ("out_time_us", "out_time_ms"):
                        divisor = 1 if key == "out_time_us" else 1000
                        elapsed_us = int(value) / divisor
                    elif key == "progress" and value in ("end", "continue"):
                        continue
                except ValueError:
                    continue
                if frame is not None and total_frames > 0:
                    percent = int(frame * 100 / total_frames)
                elif elapsed_us is not None:
                    percent = int(elapsed_us * 100 / total_us)
                else:
                    continue
                percent = max(0, min(100, percent))
                if percent != last_percent:
                    callback(percent)
                    last_percent = percent
            returncode = process.wait()
            if cancel_event is not None and cancel_event.is_set():
                raise MPlayExportCancelled("转换已取消")
            errors.seek(0)
            detail = errors.read().strip()[-1500:]
            return returncode, detail
        finally:
            stop_watcher.set()
            if watcher is not None:
                watcher.join(timeout=1)
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()


def run_export(sequence, output, fps=DEFAULT_FPS, ffmpeg=None, runner=None,
               progress=None, resolution=None, cancel_event=None):
    """执行转码，返回 ``(output_path, codec)``；失败抛 MPlayExportError。

    ``runner`` 可注入（默认 subprocess.run），用于无 ffmpeg 环境里的
    纯逻辑测试。concat 列表放临时目录，退出即清理。
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        raise MPlayExportError(
            "未找到 ffmpeg：请把 ffmpeg.exe 放到 HOUTOOLS_FFMPEG 指向的 "
            "HouTools 配置目录，或确保 hffmpeg 在 PATH / $HFS/bin。")
    if not sequence.files:
        raise MPlayExportError("序列没有可编码的帧")
    if "%" in output:
        raise MPlayExportError("输出路径含 %%，ffmpeg 会误当 printf 模板：%r"
                               % output)

    folder = os.path.dirname(output)
    # 失败时只回滚「本次新建」的东西：output 由 versioned_output_path 保证
    # 是全新的名字，半成品 mp4 可以安全删；日期目录只在本次创建且为空时
    # 才删（rmdir 对非空目录会失败，天然不会碰别人的成片）。
    created_folder = not os.path.isdir(folder)
    os.makedirs(folder, exist_ok=True)

    def _rollback():
        try:
            if os.path.isfile(output):
                os.remove(output)
        except OSError as exc:
            logger.warning("清理失败产物 %s 失败：%s", output, exc)
        if created_folder:
            try:
                os.rmdir(folder)
            except OSError:
                pass    # 目录非空 / 被占用，保留即可

    try:
        codec = detect_encoder(ffmpeg, runner=runner)
        if cancel_event is not None and cancel_event.is_set():
            raise MPlayExportCancelled("转换已取消")

        def _default_run(cmd):
            return subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                **get_startup_kwargs())
        run = runner or _default_run

        with tempfile.TemporaryDirectory(prefix="houtools_mplay_") as tmp:
            concat_path = os.path.join(tmp, "frames.txt")
            with open(concat_path, "w", encoding="utf-8") as handle:
                handle.write(concat_text(sequence.files, fps))
            command = build_ffmpeg_command(ffmpeg, concat_path, output, fps,
                                           codec, exr=sequence.is_exr,
                                           progress=progress is not None,
                                           resolution=resolution)
            if progress is not None:
                returncode, detail = _run_with_progress(
                    command, sequence.frame_count, fps, progress,
                    cancel_event=cancel_event)
                done = types.SimpleNamespace(returncode=returncode,
                                             stderr=detail)
            else:
                # 成功路径不打印命令；调用方已输出实际编码帧范围。
                # 失败时 stderr 已足够定位，完整命令不进控制台。
                done = run(command)
            if getattr(done, "returncode", 0) != 0:
                detail = (getattr(done, "stderr", "") or "").strip()[-1500:]
                raise MPlayExportError(
                    "ffmpeg 转码失败（退出码 %s）：%s"
                    % (getattr(done, "returncode", "?"),
                       detail or "无错误输出"))

        if not os.path.isfile(output) or os.path.getsize(output) == 0:
            raise MPlayExportError("ffmpeg 未生成有效视频：%s" % output)
    except Exception:
        _rollback()
        raise
    return output, codec


def reveal_file(path):
    """打开**成片所在文件夹**；失败只记日志。

    2026-11 实机反馈「打开的文件夹不对」：``explorer /select,<path>``
    在 MPlay 启动的进程里常常打开默认位置（Popen 不抛异常、无法察觉
    失败）。改为直接对目录做 ShellExecute，路径就是成片的 dirname，
    结果确定。三平台一律打开目录，不再尝试选中文件。
    """
    folder = os.path.dirname(path)
    if os.name == "nt":
        try:
            os.startfile(folder)
            return
        except Exception as exc:
            logger.debug("os.startfile 失败：%s", exc)
        try:
            subprocess.Popen(["explorer", folder])
            return
        except Exception as exc:
            logger.warning("打开文件夹失败：%s", exc)
            return
    elif sys.platform == "darwin":
        try:
            subprocess.Popen(["open", folder])
            return
        except Exception:
            pass
    try:
        subprocess.Popen(["xdg-open", folder])
    except Exception as exc:
        logger.warning("打开文件夹失败：%s", exc)
