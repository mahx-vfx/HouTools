"""MPlay → 序列图转 MP4（工具集第一个工具）。

点击 MPlay 的 HouTools ▸ Sequence to MP4 后：

1. 读 MPlay playbar 上**用户设定的播放范围**（hou.playbar.frameRange /
   hscript frange），只导出该范围，不是全部帧；
2. ``seqls -f`` 取磁盘序列真实帧；拍屏/ip 渲染是内存序列（只返回
   ``'Flipbook Images'``），走 ``imgsave -g auto -p C`` 临时落盘，
   并在 ``finally`` 清理；
3. 输出到 ``<工程目录>/Flipbook/YYYY-MM-DD/$HIPNAME.vNNN.mp4``，
   同名自动升版本；工程目录优先从已保存的 .hip 反推；
4. ffmpeg 编码（复用 videoseq 的 ffmpeg 查找链与 EXR 色彩规则），
   完成后打开成片所在文件夹。

**控制台**：成功只输出一行成片路径，中间状态静默，失败才输出原因
（2026-11 用户拍板）。**v1 同步执行**：MPlay 的 hou.ui / hdefereval
能力面没有官方承诺，后台线程回调 Qt 有崩溃风险；转码期间 MPlay 会
短暂无响应。见 dev-notes/mplaytools.md。
"""

import os
import shutil

from houtools.core.log import get_logger
from houtools.mplaytools import export, feedback, session
from houtools.videoseq.ffmpeg import find_ffmpeg

logger = get_logger("mplaytools.sequence_to_mp4")

_HIP_HINT = (
    "Sequence to MP4 无法定位输出目录（$HIP=%r / $HIPNAME=%r）。"
    "可设置 HOUTOOLS_MPLAY_HIP 与 HOUTOOLS_MPLAY_HIPNAME 指定输出位置。")

_FFMPEG_HINT = (
    "Sequence to MP4 未找到 ffmpeg：请把 ffmpeg.exe 放到 HOUTOOLS_FFMPEG "
    "指向的 HouTools 配置目录，或确保 hffmpeg 在 PATH / $HFS/bin。")


def _usable_hip(hip, hipname):
    """只要有可写目录与名字就继续。

    2026-11 实机回退：曾在这里拒绝「HOME + untitled」，但用户拍屏时
    工程常常就是没保存的状态，硬门禁会让工具完全不可用。路径不完美
    时仍按用户指定的 ``$HIP/Flipbook/日期/`` 输出，成功时那一行路径
    会明确告诉用户落在哪；想换位置用 HOUTOOLS_MPLAY_HIP 覆盖。
    """
    return bool(hip and hipname)


def run(**_kwargs):
    # 路径诊断（默认关闭）：MPlay 的变量表来源多，列出来一次定位
    if os.environ.get("HOUTOOLS_MPLAY_DEBUG_PATH"):
        print(session.diagnose_hip())
    cleanup = None
    try:
        # 所有 hou 调用都在菜单点击的主线程里完成，纯数据再交给 export
        try:
            frame_range = session.playback_range()
            sequence, _warnings, cleanup = session.current_sequence(
                frame_range=frame_range)
            hip = session.resolve_hip()
            hipname = session.resolve_hip_name()
            fps = session.session_fps()
            ffmpeg = find_ffmpeg()
        except Exception as exc:
            feedback.report("Sequence to MP4：%s" % exc, error=True)
            return

        if not _usable_hip(hip, hipname):
            feedback.report(_HIP_HINT % (hip, hipname), error=True)
            return
        if not ffmpeg:
            feedback.report(_FFMPEG_HINT, error=True)
            return

        # 左下角 toast：同步转码会冻结 MPlay，先给出「开始/请稍候」提示。
        # start_notice 内部 processEvents 保证阻塞前已经画出来；
        # 转码结束事件循环恢复后 toast 自然淡出。
        feedback.start_notice(
            "Sequence to MP4：正在转换 %d 帧，请稍候…"
            % sequence.frame_count)
        try:
            output = export.versioned_output_path(hip, hipname)
            result, _codec = export.run_export(sequence, output, fps, ffmpeg)
        except Exception as exc:
            feedback.report("Sequence to MP4 失败：%s" % exc, error=True)
            return

        # 诊断开关（默认关闭）：保留 imgsave 导出的首帧到成片目录，
        # 用于区分「MPlay 导出就不对」还是「ffmpeg 编码不对」。
        if (os.environ.get("HOUTOOLS_MPLAY_KEEP_FIRST_FRAME")
                and sequence.files):
            try:
                source = sequence.files[0]
                target = os.path.join(
                    os.path.dirname(result),
                    "_mplay_source_first" + os.path.splitext(source)[1])
                shutil.copyfile(source, target)
            except Exception as exc:
                logger.warning("保留诊断首帧失败：%s", exc)

        # 成功：控制台唯一一行输出
        feedback.output_path(result)
        try:
            export.reveal_file(result)
        except Exception:
            pass    # 打开文件夹失败不破坏「只输出路径」的约定
    finally:
        # 内存序列的临时帧必须清掉，成功/失败/提前 return 都要走到
        if cleanup is not None:
            try:
                cleanup()
            except Exception as exc:
                logger.warning("清理 imgsave 临时帧失败：%s", exc)
