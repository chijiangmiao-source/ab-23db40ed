"""域模型与输入校验：有向光纤段。

段标识 (id) 全局唯一；每条段为有向边 u -> v，延迟为非负整数。
节点可重合，允许并行光纤（同一对端点之间多条边，容量均为 1，
因此求解的是 *边不相交* 双路径，并行光纤可分别被两条路径使用）。
"""

from __future__ import annotations

from dataclasses import dataclass


class ValidationError(Exception):
    """所有输入错误都汇总抛出，每条错误精确定位到具体段/字段。"""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("; ".join(e["message"] for e in errors))


@dataclass(frozen=True)
class Fiber:
    id: str
    source: str
    target: str
    delay: int


@dataclass(frozen=True)
class Network:
    fibers: tuple[Fiber, ...]
    source: str
    sink: str
    nodes: frozenset[str]

    def outgoing(self, node: str) -> list[Fiber]:
        return [f for f in self.fibers if f.source == node]


def _safe_repr(value) -> str:
    try:
        return repr(value)
    except Exception:  # pragma: no cover - 极端输入
        return "<unreprable>"


def _err(index: int, field: str, message: str, value) -> dict:
    return {"index": index, "field": field, "message": message, "value": _safe_repr(value)}


def _coerce_str(value, field: str, index: int, errors: list[dict]) -> str | None:
    """字段必须是字符串（bool 不算字符串）；返回 None 表示已记录错误。"""
    if isinstance(value, str):
        return value
    if "value" not in ():  # 恒真，仅为结构清晰
        errors.append(_err(index, field, f"{field} 必须是字符串", value))
    return None


def parse_network(payload: dict) -> Network:
    """解析并校验请求负载，失败抛 ValidationError（含全部定位错误）。

    校验规则：
    - fibers 必须是非空数组，元素为对象；
    - id/source/target 为非空字符串，id 全局唯一；
    - delay 为非负整数（拒绝负数、小数、NaN/Infinity、bool、非数值类型）；
    - source / sink 必须是在边中出现过的节点（"不存在端点"）；
    - source 到 sink 的可达性、双路可行性由求解器给出结论。
    """
    errors: list[dict] = []

    if not isinstance(payload, dict):
        raise ValidationError(
            [_err(-1, "_root", "请求体必须是 JSON 对象", payload)]
        )

    source = _coerce_str(payload.get("source"), "source", -1, errors)
    sink = _coerce_str(payload.get("sink"), "sink", -1, errors)

    raw_fibers = payload.get("fibers")
    if not isinstance(raw_fibers, list):
        if "fibers" not in payload:
            errors.append(_err(-1, "fibers", "fibers 字段缺失", None))
        else:
            errors.append(_err(-1, "fibers", "fibers 必须是数组", raw_fibers))
        raw_fibers = []

    fibers: list[Fiber] = []
    seen_ids: set[str] = set()
    nodes: set[str] = set()

    for i, item in enumerate(raw_fibers):
        if not isinstance(item, dict):
            errors.append(_err(i, "_item", "光纤段必须是对象", item))
            continue

        fid = _coerce_str(item.get("id"), "id", i, errors)
        u = _coerce_str(item.get("source"), "source", i, errors)
        v = _coerce_str(item.get("target"), "target", i, errors)

        if fid is not None:
            if not fid:
                errors.append(_err(i, "id", "id 不能为空字符串", fid))
            elif fid in seen_ids:
                errors.append(_err(i, "id", f"重复的段标识 {fid!r}", fid))
            else:
                seen_ids.add(fid)
        if u is not None:
            if not u:
                errors.append(_err(i, "source", "source 不能为空字符串", u))
            nodes.add(u)
        if v is not None:
            if not v:
                errors.append(_err(i, "target", "target 不能为空字符串", v))
            nodes.add(v)

        delay = _parse_delay(item.get("delay"), i, errors)

        # 仅当本行字段齐备且 delay 合法时才构建 Fiber，避免脏数据进入求解器。
        if fid and u and v and delay is not None:
            fibers.append(Fiber(fid, u, v, delay))

    if source is not None and source:
        if source not in nodes:
            errors.append(_err(-1, "source",
                               f"source 节点 {source!r} 不存在于任何光纤段端点中", source))
    if sink is not None and sink:
        if sink not in nodes:
            errors.append(_err(-1, "sink",
                               f"sink 节点 {sink!r} 不存在于任何光纤段端点中", sink))
    if source and sink and source == sink:
        errors.append(_err(-1, "sink", "起点与终点不能相同（需两条完整链路）", sink))

    if not raw_fibers:
        # fibers 缺失/非数组时已记录过；这里仅在确实为空数组时补充。
        if isinstance(payload.get("fibers"), list):
            errors.append(_err(-1, "fibers", "fibers 不能为空（至少需要一条光纤段）", raw_fibers))

    if errors:
        raise ValidationError(errors)
    return Network(tuple(fibers), source, sink, frozenset(nodes))


def _parse_delay(raw, index: int, errors: list[dict]) -> int | None:
    """非负整数校验：拒绝 bool、浮点（含 NaN/Infinity）、负数、缺失与其他类型。"""
    if isinstance(raw, bool) or not isinstance(raw, int):
        errors.append(_err(index, "delay", "delay 必须是非负整数（不允许负延迟/小数）", raw))
        return None
    if raw < 0:
        errors.append(_err(index, "delay", "delay 必须是非负整数（不允许负延迟）", raw))
        return None
    return raw
