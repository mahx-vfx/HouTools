"""PySide6 版本差异兼容：包装类型粘连（H21 / 6.5）的重包。

本机无头实测（H21.0.512 = PySide6 6.5.3；H22.0.429 = 6.8.3）：

* 6.5 的 shiboken 按「首次包装时请求的类型」缓存 C++ 指针的 Python
  包装。若某个 ``QHeaderView*`` 先被包成祖先类，之后
  ``QTableWidget.horizontalHeader()`` 一直返回那个旧包装 ——
  ``setSectionResizeMode`` 不可见，报
  ``AttributeError: 'PySide6.QtWidgets.QAbstractScrollArea' object has
  no attribute 'setSectionResizeMode'``（打包工程窗口在 H21 实崩）。
  继承链：QHeaderView → QAbstractItemView → QAbstractScrollArea → QFrame。
  ``ui/fonts`` 的 QApplication 级 ChildAdded 过滤器会在控件构造期触碰
  新控件，是 6.5 上已知的同类触发点（对话框标准按钮曾被包成 QWidget
  而实崩，见 ``ui/dialogs`` 模块头）；表头的具体粘连路径也可能来自
  Houdini 会话内的样式/字体包装——修复按「任何来源的降级包装」防御。
* 6.8 按 C++ 真实类型解析，连显式
  ``wrapInstance(ptr, QAbstractScrollArea)`` 都返回 QHeaderView，不触发。

解法：按 C++ 指针显式重包成目标类型（6.8 上 ``isinstance`` 短路，
零开销）。版本差异在真身里消化（AGENTS.md「多版本单一源码」），
``python3.11libs`` 入口壳不动。
"""

from __future__ import annotations

import shiboken6

from houtools.core.log import get_logger

log = get_logger("ui.qtcompat")


def rewrap(obj, py_type):
    """把 obj 重包成 py_type；已是目标类型则原样返回。

    ``rewrap(table.horizontalHeader(), QHeaderView)`` 是典型用法。重包
    失败时返回原对象并告警，由调用方用 ``hasattr`` 能力探测降级——
    窗口仍要能打开，不能为一个列宽策略把整个工具打挂。
    """
    if isinstance(obj, py_type):
        return obj
    try:
        return shiboken6.wrapInstance(
            shiboken6.getCppPointer(obj)[0], py_type)
    except (RuntimeError, TypeError, IndexError, AttributeError) as exc:
        log.warning("按 %s 重包控件失败：%s",
                    getattr(py_type, "__name__", py_type), exc)
        return obj
