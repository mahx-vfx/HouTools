# HouTools UI 启动钩子（H21 / Python 3.11 入口壳）
# ================================================
# Houdini 官方机制（docs: Python script locations）：Houdini 会执行 Houdini
# 路径上**所有** pythonX.Ylibs/uiready.py（UI 就绪后，仅交互会话）——H21 只
# 扫 python3.11libs、H22 只扫 python3.13libs，各自恰好执行一份，互不双跑。
#
# 单一源码约定：真身是 ../python3.13libs/uiready.py，本文件只做转发；改
# 启动逻辑请改真身，严禁把业务代码写进本目录（会漂移成第二份维护）。

from pathlib import Path

# 经同目录入口壳拿根路径（import 体系必给 __file__，不依赖钩子脚本
# 自身有无 __file__）
import houtools as _ht

_root = Path(_ht.__file__).resolve().parents[2]
_real = _root / "python3.13libs" / "uiready.py"

# 真身兜底拿到自己的路径（现不使用 __file__，防未来真身用上自定位）
__file__ = str(_real)
exec(compile(_real.read_text(encoding="utf-8"), str(_real), "exec"),
     globals())
