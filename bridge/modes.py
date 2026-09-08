"""Bridge 运行模式定义。"""

from enum import Enum


class BridgeMode(str, Enum):
    LEARN = "learn"
    VERIFY = "verify"
    AUTO = "auto"


def normalize_mode(value: str | None) -> BridgeMode:
    if isinstance(value, BridgeMode):
        return value
    try:
        return BridgeMode(str(value or BridgeMode.VERIFY.value).lower())
    except ValueError as exc:
        raise ValueError(f"未知 Bridge 模式: {value}; 可选 learn/verify/auto") from exc
