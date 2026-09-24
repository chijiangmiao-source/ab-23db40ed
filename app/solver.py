"""双链路求解器。

在有向图（每条光纤容量 1、费用=非负整数延迟）上求 source -> sink
的两条 *边不相交* 路径，使总延迟精确最小。

算法：最小费用最大流（Successive Shortest Path + 结点势能 Dijkstra）。
- 每轮增广 1 单位流，共需 2 单位；
- 费用非负，势能 Dijkstra 保证正确且多项式复杂度；
- 第二轮失败时，已得最大流即当前最大流值（0=不可达，1=仅单路），
  由残余网络沿残量>0 的弧求得源侧可达集合，其外出弧即最小割证据
  （最大流最小割定理）。

这不是"先求一条最短路再删边"的贪心：增广过程允许沿反向弧重路由，
得到的是全局最优。结果中附带 greedy 字段透明展示贪心结论以作对照。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .network_model import Fiber, Network


@dataclass
class Route:
    index: int
    fiber_ids: list[str]
    nodes: list[str]
    delays: list[int]
    delay: int
    delay_expression: str


@dataclass
class CutEdge:
    id: str
    source: str
    target: str
    delay: int


@dataclass
class SolveResult:
    feasible: bool
    max_flow: int
    source: str
    sink: str
    # feasible=True 时：
    total_delay: int | None = None
    routes: list[Route] = field(default_factory=list)
    # feasible=False 时（割证据）：
    source_side_nodes: list[str] = field(default_factory=list)
    cut_edges: list[CutEdge] = field(default_factory=list)
    # 贪心（最短路+删边）对照，证明本结果不是贪心冒充：
    greedy: dict | None = None


# ---- 内部最小费用流 -----------------------------------------------------

class _MCMF:
    def __init__(self, network: Network):
        self.net = network
        self.nodes = sorted(network.nodes)
        self.idx = {n: i for i, n in enumerate(self.nodes)}
        self.s = self.idx[network.source]
        self.t = self.idx[network.sink]
        # arc: [to, rev, cap, cost, fiber_index]
        self.arc: list[list] = []
        self.g: list[list[int]] = [[] for _ in self.nodes]
        # fiber 的正向弧 id
        self.fiber_arc: list[int] = []

    def _add_arc(self, u: int, v: int, cap: int, cost: int, fi: int) -> int:
        i = len(self.arc)
        self.arc.append([v, i + 1, cap, cost, fi])      # 正向
        self.arc.append([u, i, 0, -cost, -1])           # 反向（不对应光纤）
        self.g[u].append(i)
        self.g[v].append(i + 1)
        return i

    def build(self) -> None:
        for fi, f in enumerate(self.net.fibers):
            a = self._add_arc(self.idx[f.source], self.idx[f.target], 1, f.delay, fi)
            self.fiber_arc.append(a)

    def solve(self, need: int = 2) -> tuple[int, int, list[list[int]]]:
        """返回 (flow, cost, augmentations)；augmentations 为每轮经过的弧 id。"""
        n = len(self.nodes)
        pot = [0] * n
        flow = cost = 0
        augmentations: list[list[int]] = []
        INF = 10**18

        while flow < need:
            dist = [INF] * n
            prev_arc = [-1] * n
            dist[self.s] = 0
            pq = [(0, self.s)]
            while pq:
                d, u = heapq.heappop(pq)
                if d != dist[u]:
                    continue
                for ai in self.g[u]:
                    a = self.arc[ai]
                    if a[2] <= 0:
                        continue
                    nd = d + a[3] + pot[u] - pot[a[0]]
                    if nd < dist[a[0]]:
                        dist[a[0]] = nd
                        prev_arc[a[0]] = ai
                        heapq.heappush(pq, (nd, a[0]))
            if dist[self.t] == INF:
                break
            for v in range(n):
                if dist[v] < INF:
                    pot[v] += dist[v]
            # 回溯增广路（可能含反向弧，体现重路由）
            path_arcs: list[int] = []
            v = self.t
            while v != self.s:
                ai = prev_arc[v]
                path_arcs.append(ai)
                v = self.arc[self.arc[ai][1]][0]  # 反向弧的 to 即本弧起点
            path_arcs.reverse()
            for ai in path_arcs:
                a = self.arc[ai]
                a[2] -= 1
                self.arc[a[1]][2] += 1
                cost += a[3]
            augmentations.append(path_arcs)
            flow += 1
        return flow, cost, augmentations

    def reachable_from_source(self) -> set[int]:
        """残余网络中沿残量>0 的弧从 s 可达的结点集合。"""
        seen = {self.s}
        stack = [self.s]
        while stack:
            u = stack.pop()
            for ai in self.g[u]:
                a = self.arc[ai]
                if a[2] > 0 and a[0] not in seen:
                    seen.add(a[0])
                    stack.append(a[0])
        return seen

    def used_fibers(self) -> set[int]:
        """最终流值为 1 的光纤下标（正向弧残量为 0）。"""
        return {fi for fi, ai in enumerate(self.fiber_arc)
                if self.arc[ai][2] == 0}


# ---- 贪心对照：最短路 + 删边 -------------------------------------------

def _greedy(network: Network) -> dict:
    """复现错误做法：Dijkstra 求一条最短路，删除其全部边后再求最短路。"""
    adj: dict[str, list[tuple[str, int, str]]] = {n: [] for n in network.nodes}
    for f in network.fibers:
        adj[f.source].append((f.target, f.delay, f.id))

    def dijkstra(banned: set[str]):
        dist = {n: 10**18 for n in network.nodes}
        pu: dict[str, tuple[str, str] | None] = {n: None for n in network.nodes}
        dist[network.source] = 0
        pq = [(0, network.source)]
        while pq:
            d, u = heapq.heappop(pq)
            if d != dist[u]:
                continue
            for v, w, fid in adj[u]:
                if fid in banned:
                    continue
                nd = d + w
                if nd < dist[v]:
                    dist[v] = nd
                    pu[v] = (u, fid)
                    heapq.heappush(pq, (nd, v))
        if dist[network.sink] >= 10**18:
            return None
        edges: list[str] = []
        v = network.sink
        while v != network.source:
            u, fid = pu[v]
            edges.append(fid)
            v = u
        edges.reverse()
        return dist[network.sink], edges

    first = dijkstra(set())
    if first is None:
        return {"feasible": False, "reason": "unreachable",
                "first_path": None, "second_path": None, "total_delay": None}
    d1, p1 = first
    second = dijkstra(set(p1))
    if second is None:
        return {"feasible": False, "reason": "no_edge_disjoint_second_path",
                "first_path": p1, "first_delay": d1,
                "second_path": None, "total_delay": None}
    d2, p2 = second
    return {"feasible": True, "first_path": p1, "first_delay": d1,
            "second_path": p2, "second_delay": d2, "total_delay": d1 + d2}


# ---- 路径分解 -----------------------------------------------------------

def _strip_zero_cycles(network: Network, out_used: dict[str, list[int]]) -> None:
    """就地移除已用弧上的有向环流（颜色传播法）。

    把"被使用"的正向光纤视作子图。若其中存在环，则环上每个结点至少
    有一条入弧属于环。算法：统计各结点剩余入弧数，把入弧为 0 的结点
    不断剥离；剥离结束后仍有入弧的部分恰为环，逐条摘除环弧。
    环上费用和必为 0（最优性），故不影响总延迟。
    """
    fibers = network.fibers
    # 收集当前所有 used 弧的多重集
    arcs: list[int] = [fi for lst in out_used.values() for fi in lst]
    indeg: dict[str, int] = {n: 0 for n in network.nodes}
    in_by_node: dict[str, list[int]] = {}
    for fi in arcs:
        indeg[fibers[fi].target] += 1
        in_by_node.setdefault(fibers[fi].target, []).append(fi)

    queue = [n for n, d in indeg.items() if d == 0]
    removed: set[int] = set()
    head = 0
    while head < len(queue):
        u = queue[head]
        head += 1
        for fi in out_used.get(u, []):
            if fi in removed:
                continue
            v = fibers[fi].target
            indeg[v] -= 1
            removed.add(fi)
            if indeg[v] == 0:
                queue.append(v)

    # Kahn 剥离结束后，removed 为非环弧；其余弧属于环，应予摘除
    cycle_arcs = {fi for fi in arcs if fi not in removed}
    if cycle_arcs:
        for u in list(out_used.keys()):
            kept = [fi for fi in out_used[u] if fi not in cycle_arcs]
            if kept:
                out_used[u] = kept
            else:
                del out_used[u]


def _decompose_routes(network: Network, used: set[int]) -> list[Route]:
    """把最终 0/1 整数流分解为两条 source->sink 路径。

    容量均为 1，流守恒保证从 source 沿"已用"光纤可走到 sink。
    最小费用流可能附带零费用有向环流（非负费用下最优流中任何环流
    费用必为 0，否则取消环流会更优），先将其剔除再分解，保证两条
    轨迹均为简单路径。
    """
    fibers = network.fibers
    out_used: dict[str, list[int]] = {}
    for fi in used:
        out_used.setdefault(fibers[fi].source, []).append(fi)
    for lst in out_used.values():
        lst.sort()

    _strip_zero_cycles(network, out_used)

    routes: list[Route] = []
    for k in range(2):
        fiber_ids: list[str] = []
        delays: list[int] = []
        nodes = [network.source]
        seen_nodes = {network.source}
        cur = network.source
        while cur != network.sink:
            lst = out_used.get(cur)
            if not lst:  # 理论上不可能：流守恒
                raise RuntimeError("流分解失败：流不守恒")
            fi = lst.pop(0)
            f = fibers[fi]
            if f.target in seen_nodes:
                raise RuntimeError("流分解失败：出现非零费用环流")
            fiber_ids.append(f.id)
            delays.append(f.delay)
            seen_nodes.add(f.target)
            cur = f.target
            nodes.append(cur)
        routes.append(Route(
            index=k,
            fiber_ids=fiber_ids,
            nodes=nodes,
            delays=delays,
            delay=sum(delays),
            delay_expression=" + ".join(str(d) for d in delays) + f" = {sum(delays)}",
        ))
    # 确定性：先按延迟、再按首段标识排序
    routes.sort(key=lambda r: (r.delay, r.fiber_ids[0] if r.fiber_ids else ""))
    for i, r in enumerate(routes):
        r.index = i
    return routes


# ---- 对外入口 -----------------------------------------------------------

def solve(network: Network) -> SolveResult:
    m = _MCMF(network)
    m.build()
    flow, cost, _aug = m.solve(need=2)
    greedy = _greedy(network)

    if flow == 2:
        used = m.used_fibers()
        routes = _decompose_routes(network, used)
        total = sum(r.delay for r in routes)
        # 最小费用流的费用与路径分解之和必须一致（自检）
        assert total == cost, f"费用不一致: {total} != {cost}"
        return SolveResult(
            feasible=True, max_flow=2,
            source=network.source, sink=network.sink,
            total_delay=total, routes=routes, greedy=greedy,
        )

    # flow < 2：残余网络给出最小割
    reachable = m.reachable_from_source()
    reachable_nodes = {m.nodes[i] for i in reachable}
    cut: list[CutEdge] = []
    for f in network.fibers:
        if f.source in reachable_nodes and f.target not in reachable_nodes:
            cut.append(CutEdge(f.id, f.source, f.target, f.delay))
    # 自检：割容量必须等于最大流（每条光纤容量 1）
    assert len(cut) == flow, f"割边数 {len(cut)} != 最大流 {flow}"
    cut.sort(key=lambda c: (c.source, c.target, c.id))
    return SolveResult(
        feasible=False, max_flow=flow,
        source=network.source, sink=network.sink,
        source_side_nodes=sorted(reachable_nodes),
        cut_edges=cut, greedy=greedy,
    )
