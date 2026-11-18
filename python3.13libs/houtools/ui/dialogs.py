"""对话框按钮中文化 + 品牌主题。

QDialog 标准按钮文字跟平台主题走（英文系统显示 OK/Cancel/Yes/No），本插件
UI 一律中文，按 QDialogButtonBox 的 role 改写；弹窗样式统一经
``qss.dialog_qss()`` 按品牌 token 上色（parent 挂在已设 htWindow QSS 的
窗口下时同一张表本会级联、显式再设一遍保证 parent 为 Houdini 主窗口或
None 的独立弹窗也走主题）。无 hou 依赖（smoke test 可直接 import）。

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
from PySide6 import QtCore, QtWidgets

from houtools.ui import fonts, qss

_LABELS = None


def _rewrap(obj, py_type):
    """obj 的 Python 包装被粘连成别的类型（如 QWidget）时按 C++ 指针
    重包成 py_type；已是目标类型则原样返回。"""
    if isinstance(obj, py_type):
        return obj
    return shiboken6.wrapInstance(shiboken6.getCppPointer(obj)[0], py_type)


def _apply_theme(box, primary_roles=()):
    """给 QMessageBox 上品牌主题 QSS，并把确认类标准按钮标成主按钮。

    样式表与 htWindow 同一张 token 表（INK_950 底 + token 按钮），保证
    parent 为 Houdini 主窗口/None（无级联来源）的独立弹窗也走主题；
    primary_roles 里的按钮标动态属性 htRole=primary——品牌橙主按钮，
    与组件库 PrimaryButton 同语言。

    **属性必须先于 setStyleSheet 设置**：QSS 属性选择器在 polish 时
    解析并缓存，setStyleSheet 会 repolish 子控件——先上样式再设属性，
    解析结果已按"无属性"缓存、之后不再重读，按钮永远落回基础灰底
    （离屏探针实测：先属性渲出 ACCENT_FILL 暗橙，反序是 INK_850 灰底；
    或事后 style().unpolish/polish 强制重算也可救，这里直接用对序）。

    primary 按钮同时设 Qt.NoFocus：QMessageBox 的默认按钮生而持有
    焦点，``:focus`` 的 2px FOCUS_RING 焦点环从打开就常亮，静息态读成
    "一直被激活"（2026-11 用户反馈）。双键确认模态鼠标优先——NoFocus
    后静息态恒为暗橙底、无环；键盘语义不受影响（Enter 走 default
    button、Esc 走 RejectRole），Tab 在两键模态本无意义。

    fonts.apply 整树刷 UI 角色字（阿里妈妈数黑体 Bold）：弹窗不挂在
    工具窗口子树内，fonts 的 ChildAdded enforcer 管不到，不吃这套就是
    Houdini 应用级类字体（SideFX，非品牌脸且无粗细）——与工具面板
    同一套逐控件显式 setFont 手法压过它。
    """
    fonts.apply(box)
    for role in primary_roles:
        btn = box.button(role)
        if btn is None:
            continue
        btn = _rewrap(btn, QtWidgets.QAbstractButton)
        btn.setProperty("htRole", "primary")
        btn.setFocusPolicy(QtCore.Qt.NoFocus)
    box.setStyleSheet(qss.dialog_qss())


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
    _apply_theme(box, (QtWidgets.QMessageBox.Ok,))
    box.exec_()
    box.deleteLater()   # 不留隐藏对话框（可能带 parent 长期存活）


def info(parent, title, text):
    """QMessageBox.information 的中文化版本（单按钮「确认」）。"""
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    localize_buttons(box)
    _apply_theme(box, (QtWidgets.QMessageBox.Ok,))
    box.exec_()
    box.deleteLater()


def question(parent, title, text):
    """QMessageBox.question 的中文化版本。返回 True = 「确认」（Yes）。"""
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
    localize_buttons(box)
    _apply_theme(box, (QtWidgets.QMessageBox.Yes,))
    ret = box.exec_()
    box.deleteLater()
    return ret == QtWidgets.QMessageBox.Yes


def choice(parent, title, text, buttons):
    """N 按钮模态选择框（按钮文字由调用方给出，直接中文）。

    返回被按下按钮的下标；Esc / 关闭窗口映射到最后一个按钮（拒绝
    语义，如「取消」）。用于标准 Yes/No 表达不了的多选场景（如配置
    冲突的 覆盖/另存/取消）。"""
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    for i, label in enumerate(buttons):
        role = (QtWidgets.QMessageBox.RejectRole if i == len(buttons) - 1
                else QtWidgets.QMessageBox.AcceptRole)
        box.addButton(label, role)
    box.setDefaultButton(box.buttons()[0])
    _apply_theme(box)
    box.exec_()
    clicked = box.clickedButton()
    all_buttons = box.buttons()   # deleteLater 前取引用，防销毁后枚举
    box.deleteLater()
    for i, btn in enumerate(all_buttons):
        if btn is clicked:
            return i
    return len(buttons) - 1


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
    fonts.apply(dlg)                      # 独立弹窗也走品牌字体
    dlg.setStyleSheet(qss.dialog_qss())   # 独立弹窗也走品牌主题
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
