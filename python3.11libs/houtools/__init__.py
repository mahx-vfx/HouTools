"""HouTools 多版本入口壳（H21 / Python 3.11）。

Houdini 只把 python3.<自身版本>libs 加入 sys.path：H21 (Python 3.11) 扫
python3.11libs、H22 (Python 3.13) 扫 python3.13libs。真身唯一——本包把
真身目录追加进 __path__，所有子模块从真身解析：单一源码、两版共用，
用户拿发行包直装即用。真身 __init__ 的内容（__version__ 等）在此执行，
保证版本号等单一来源。

业务代码严禁写进本目录（会漂移成第二份维护）；版本差异在真身里用
能力探测（getattr 等）消化。
"""

from pathlib import Path

_real_pkg = Path(__file__).resolve().parents[2] / "python3.13libs" / "houtools"
if not _real_pkg.is_dir():
    raise ImportError(
        "HouTools 真身缺失：{} 不存在。python3.11libs 是入口壳，必须与 "
        "python3.13libs 同级随包分发，不得单独复制。".format(_real_pkg))
if str(_real_pkg) not in __path__:
    __path__.append(str(_real_pkg))

_real_init = _real_pkg / "__init__.py"
exec(compile(_real_init.read_text(encoding="utf-8"), str(_real_init),
             "exec"), globals())
