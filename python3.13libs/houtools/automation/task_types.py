"""
Automation — 任务类型定义
=============================
TaskType 枚举 + TaskParams 数据类 + TaskItem 容器（可序列化）。
"""

from enum import Enum
from dataclasses import dataclass
from typing import Union


class TaskType(Enum):
    """自动化任务类型枚举。"""
    BUTTON_CLICK = "BUTTON_CLICK"
    FLIPBOOK = "FLIPBOOK"
    HOME_ASSISTANT = "HOME_ASSISTANT"
    OPEN_DW = "OPEN_DW"


@dataclass
class ButtonClickParams:
    """按钮点击参数：指定节点路径和参数名。"""
    node_path: str
    parm_name: str


@dataclass
class FlipbookParams:
    """Flipbook 渲染参数：帧范围、输出路径、是否保存到磁盘。"""
    start_frame: str = "$RFSTART"
    end_frame: str = "$RFEND"
    output_path: str = "$HIP/FlipBook/$HIPNAME/$HIPNAME.$F4.jpg"
    save_to_disk: bool = True


@dataclass
class HomeAssistantParams:
    """Home Assistant 自动化参数：Webhook URL。"""
    webhook_url: str


@dataclass
class OpenDWParams:
    """打开DW 参数：软件路径来自应用级配置文件，任务本身无参数。"""


TaskParams = Union[ButtonClickParams, FlipbookParams, HomeAssistantParams, OpenDWParams]


def as_bool(value, default: bool) -> bool:
    """JSON 布尔归一：只认真 bool；字符串按内容判（"false"/"0"/"no" 一类
    写法 → False），数字 0 → False、其余真值 → True。

    不能直接 bool(value)：字符串 "false" 是 truthy，会把用户明确禁用的
    任务反转成启用（引擎照跑）、复选框被勾上、下次保存把"禁用"写回
    "启用"——"加载—保存"往返把错误固化。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return default
    return text not in ("false", "0", "no", "off", "none", "null")


@dataclass
class TaskItem:
    """任务项 —— 组合了类型、参数和启用状态。"""
    task_type: TaskType
    params: TaskParams
    enabled: bool

    def to_dict(self) -> dict:
        """序列化为 dict，frame_range 转为列表。"""
        base = {
            "type": self.task_type.value,
            "enabled": self.enabled,
        }

        if self.task_type == TaskType.BUTTON_CLICK:
            assert isinstance(self.params, ButtonClickParams)
            base["params"] = {
                "node_path": self.params.node_path,
                "parm_name": self.params.parm_name,
            }
        elif self.task_type == TaskType.FLIPBOOK:
            assert isinstance(self.params, FlipbookParams)
            base["params"] = {
                "start_frame": self.params.start_frame,
                "end_frame": self.params.end_frame,
                "output_path": self.params.output_path,
                "save_to_disk": self.params.save_to_disk,
            }
        elif self.task_type == TaskType.HOME_ASSISTANT:
            assert isinstance(self.params, HomeAssistantParams)
            base["params"] = {
                "webhook_url": self.params.webhook_url,
            }
        elif self.task_type == TaskType.OPEN_DW:
            assert isinstance(self.params, OpenDWParams)
            base["params"] = {}
        else:
            raise ValueError(f"Unknown task type: {self.task_type}")

        return base

    @staticmethod
    def from_dict(data: dict) -> "TaskItem":
        """从 dict 反序列化，frame_range 列表恢复为 tuple。

        容错语义与 data_manager.deserialize_params 对齐：params 缺失/
        多余字段走默认值，type 缺失或未知、params 不是对象（truthy 的
        列表/字符串等）才报错——公开 API load_tasks 直接调本方法，硬
        下标或 params_data.get() 打在非 dict 上，会把手改坏的配置文件
        变成未捕获异常。
        """
        if not isinstance(data, dict) or "type" not in data:
            raise ValueError(f"任务缺少 type 字段: {data!r}")
        type_str = data["type"]
        params_data = data.get("params") or {}
        if not isinstance(params_data, dict):
            # or {} 只兜得住 falsy 值；truthy 非字典穿透到 .get() 会抛
            # AttributeError——不在 _start_execution 的捕获元组里
            raise ValueError(f"params 必须是对象: {params_data!r}")

        if type_str == "BUTTON_CLICK":
            task_type = TaskType.BUTTON_CLICK
            params: TaskParams = ButtonClickParams(
                # 字符串字段 str() 归一：手改 JSON 写成数字/布尔时不能让
                # 非 str 进引擎（hou.node(1) 直接 TypeError）
                node_path=str(params_data.get("node_path") or ""),
                parm_name=str(params_data.get("parm_name") or ""),
            )
        elif type_str == "FLIPBOOK":
            task_type = TaskType.FLIPBOOK
            params = FlipbookParams(
                start_frame=str(params_data.get("start_frame") or "$RFSTART"),
                end_frame=str(params_data.get("end_frame") or "$RFEND"),
                output_path=str(params_data.get("output_path")
                                or "$HIP/FlipBook/$HIPNAME/$HIPNAME.$F4.jpg"),
                save_to_disk=as_bool(params_data.get("save_to_disk", True),
                                     True),
            )
        elif type_str == "HOME_ASSISTANT":
            task_type = TaskType.HOME_ASSISTANT
            params = HomeAssistantParams(
                webhook_url=str(params_data.get("webhook_url") or ""),
            )
        elif type_str == "OPEN_DW":
            task_type = TaskType.OPEN_DW
            params: TaskParams = OpenDWParams()
        else:
            raise ValueError(f"Unknown task type string: {type_str}")

        return TaskItem(
            task_type=task_type,
            params=params,
            # 布尔归一（as_bool）：原样透传会让 "false" 这类 truthy 字符串
            # 把禁用任务变成启用
            enabled=as_bool(data.get("enabled", True), True),
        )
