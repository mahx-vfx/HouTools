"""对话框按钮中文化 helper。

Qt 标准按钮文字跟平台主题走（英文系统显示 OK/Cancel/Yes/No），本插件
UI 一律中文，按 QDialogButtonBox 的 role 改写。无 hou 依赖（smoke test
可直接 import）。

注意：QColorDialog / QFileDialog 在 Windows 上默认用原生对话框，按钮
不受此控制——需要改按钮文字的对话框须实例化调用并加
DontUseNativeDialog 选项（静态便利函数拿不到内部按钮对象）。

⚠ 包装类型粘连（2026-11 H21 会话实测）：工具字体 enforcer（ui/fonts，
QApplication 级 ChildAdded 过滤器）会在对话框构造期触碰新控件，PySide6
6.5（H21）由此把标准按钮先包成 QWidget——此后 QDialogButtonBox.button()
一直返回该旧包装、没有 setText（PySide6 6.8/H22 正确解析派生类型，
不触发， H21 复现见 _clear_thumb 清缩略图确认框 AttributeError）。
所以 button() 返回值不能信 Python 类型：一律先按 C++ 指针重包成
QAbstractButton（_rewrap）再改文字。
"""

import shiboken6
from PySide6 import QtWidgets

_LABELS = None


def _rewrap(obj, py_type):
    """obj 的 Python 包装被粘连成别的类型（如 QWidget）时按 C++ 指针
    重包成 py_type；已是目标类型则原样返回。"""
    if isinstance(obj, py_type):
        return obj
    return shiboken6.wrapInstance(shiboken6.getCppPointer(obj)[0], py_type)


def localize_buttons(dialog):
    """把 dialog 内所有 QDialogButtonBox 的标准按钮换成中文。"""
    global _LABELS
    if _LABELS is None:
        bb = QtWidgets.QDialogButtonBox
        _LABELS = {
            bb.Ok: "确认",
            bb.Yes: "确认",
            bb.Save: "确认",
            bb.Cancel: "取消",
            bb.No: "取消",
            bb.Close: "关闭",
        }
    for bbox in dialog.findChildren(QtWidgets.QDialogButtonBox):
        for role, text in _LABELS.items():
            btn = bbox.button(role)
            if btn is not None:
                _rewrap(btn, QtWidgets.QAbstractButton).setText(text)


def warn(parent, title, text):
    """QMessageBox.warning 的中文化版本（单按钮「确认」）。"""
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    localize_buttons(box)
    box.exec_()
    box.deleteLater()   # 不留隐藏对话框（可能带 parent 长期存活）


def info(parent, title, text):
    """QMessageBox.information 的中文化版本（单按钮「确认」）。"""
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    localize_buttons(box)
    box.exec_()
    box.deleteLater()


def question(parent, title, text):
    """QMessageBox.question 的中文化版本。返回 True = 「确认」（Yes）。"""
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
    localize_buttons(box)
    ret = box.exec_()
    box.deleteLater()
    return ret == QtWidgets.QMessageBox.Yes


def prompt_text(parent, title, label, text=""):
    """单行文本输入（自建 QInputDialog 实例 + 按钮中文化）。

    返回 (text, ok)。静态便利函数 QInputDialog.getText 拿不到内部按钮
    （英文系统出英文按钮），必须实例化调用。
    """
    dlg = QtWidgets.QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setTextValue(text)
    localize_buttons(dlg)
    ok = dlg.exec_() == QtWidgets.QDialog.Accepted
    value = dlg.textValue()
    dlg.deleteLater()
    return value, ok


# QColorDialog（DontUseNativeDialog）内部的英文部件文本 → 中文。
# 按原文匹配改写（去助记符 & 后精确匹配，冒号保留），找不到的部件
# 原样保留（Qt 版本间文案可能变化）。
_COLOR_TEXTS = {
    "Pick Screen Color": "选取屏幕颜色",
    "Basic colors": "基本颜色",
    "Custom colors": "自定义颜色",
    "Add to Custom Colors": "添加到自定义颜色",
    "Hue:": "色调:",
    "Sat:": "饱和:",
    "Val:": "明度:",
    "Red:": "红:",
    "Green:": "绿:",
    "Blue:": "蓝:",
    "Alpha channel:": "透明通道:",
}


def localize_color_dialog(dlg):
    """把非原生 QColorDialog 里的英文部件标签/按钮改成中文。"""
    for w in dlg.findChildren(QtWidgets.QWidget):
        if not isinstance(w, (QtWidgets.QLabel, QtWidgets.QPushButton,
                              QtWidgets.QToolButton)):
            continue
        text = w.text().replace("&", "")
        cn = _COLOR_TEXTS.get(text)
        if cn:
            w.setText(cn)
