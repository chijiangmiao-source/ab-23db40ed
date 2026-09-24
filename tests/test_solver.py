"""求解器与校验的单元测试。运行：python -m unittest discover -s tests -v"""

import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.network_model import ValidationError, parse_network
from app.solver import solve


def mk(payload):
    return parse_network(payload)


def brute_force_two_paths(network):
    """枚举全部简单 s->t 路径对，返回边不相交对的最小总延迟，无则 None。

    测试基准（小规模图上穷举，独立于 MCMF 实现）。
    """
    adj = {}
    for f in network.fibers:
        adj.setdefault(f.source, []).append(f)

    def all_simple_paths():
        paths = []

        def dfs(u, acc, seen_edges, seen_nodes):
            if u == network.sink:
                paths.append((tuple(acc), sum(network.fibers[i].delay for i in acc)))
                return
            for f in adj.get(u, []):
                fi = network.fibers.index(f)
                if fi in seen_edges or f.target in seen_nodes:
                    continue
                seen_edges.add(fi)
                seen_nodes.add(f.target)
                acc.append(fi)
                dfs(f.target, acc, seen_edges, seen_nodes)
                acc.pop()
                seen_nodes.discard(f.target)
                seen_edges.discard(fi)

        dfs(network.source, [], set(), {network.source})
        return paths

    paths = all_simple_paths()
    best = None
    for (p1, c1), (p2, c2) in itertools.combinations(paths, 2):
        if set(p1).isdisjoint(set(p2)):
            total = c1 + c2
            if best is None or total < best:
                best = total
    return best


class SolverTests(unittest.TestCase):
    GLOBAL_CASE = {
        "source": "S", "sink": "T",
        "fibers": [
            {"id": "F1", "source": "S", "target": "A", "delay": 0},
            {"id": "F2", "source": "S", "target": "B", "delay": 5},
            {"id": "F3", "source": "A", "target": "B", "delay": 3},
            {"id": "F4", "source": "A", "target": "T", "delay": 10},
            {"id": "F5", "source": "B", "target": "T", "delay": 2},
        ],
    }

    def test_global_optimum_beats_greedy_shared_bridge(self):
        """招牌场景：最短路独占共享桥，贪心误报无双路；全局最优=17。"""
        res = solve(mk(self.GLOBAL_CASE))
        self.assertTrue(res.feasible)
        self.assertEqual(res.total_delay, 17)
        # 两条路径边不重复
        ids = [set(r.fiber_ids) for r in res.routes]
        self.assertEqual(2, len(ids))
        self.assertTrue(ids[0].isdisjoint(ids[1]))
        # 每条都从 S 到 T
        for r in res.routes:
            self.assertEqual(r.nodes[0], "S")
            self.assertEqual(r.nodes[-1], "T")
            self.assertEqual(r.delay, sum(r.delays))
        # 贪心对照必须显示失败（不可冒充）
        self.assertFalse(res.greedy["feasible"])
        self.assertEqual(res.greedy["reason"], "no_edge_disjoint_second_path")
        # 贪心第一条最短路正是吃掉共享桥 F5 的 S-A-B-T
        self.assertEqual(res.greedy["first_path"], ["F1", "F3", "F5"])

    def test_parallel_fibers_two_routes(self):
        """同一对端点的并行光纤可分别承载两条路。"""
        payload = {"source": "S", "sink": "T", "fibers": [
            {"id": "P1", "source": "S", "target": "T", "delay": 8},
            {"id": "P2", "source": "S", "target": "T", "delay": 3},
            {"id": "P3", "source": "S", "target": "T", "delay": 5},
        ]}
        res = solve(mk(payload))
        self.assertTrue(res.feasible)
        self.assertEqual(res.total_delay, 3 + 5)
        self.assertEqual(sorted(fid for r in res.routes for fid in r.fiber_ids),
                         ["P2", "P3"])

    def test_zero_delays(self):
        payload = {"source": "S", "sink": "T", "fibers": [
            {"id": "a", "source": "S", "target": "T", "delay": 0},
            {"id": "b", "source": "S", "target": "T", "delay": 0},
        ]}
        res = solve(mk(payload))
        self.assertTrue(res.feasible)
        self.assertEqual(res.total_delay, 0)

    def test_single_bridge_cut_evidence(self):
        """仅一条桥：max_flow=1，割证据含该桥，割容量=最大流。"""
        payload = {"source": "S", "sink": "T", "fibers": [
            {"id": "E1", "source": "S", "target": "A", "delay": 1},
            {"id": "E2", "source": "S", "target": "A", "delay": 4},
            {"id": "E3", "source": "A", "target": "T", "delay": 2},
        ]}
        res = solve(mk(payload))
        self.assertFalse(res.feasible)
        self.assertEqual(res.max_flow, 1)
        self.assertEqual([e.id for e in res.cut_edges], ["E3"])
        self.assertIn("S", res.source_side_nodes)
        self.assertIn("A", res.source_side_nodes)
        self.assertNotIn("T", res.source_side_nodes)
        for e in res.cut_edges:
            self.assertIn(e.source, res.source_side_nodes)
            self.assertNotIn(e.target, res.source_side_nodes)

    def test_unreachable(self):
        payload = {"source": "S", "sink": "T", "fibers": [
            {"id": "G1", "source": "S", "target": "A", "delay": 3},
            {"id": "G2", "source": "A", "target": "S", "delay": 1},
            {"id": "G3", "source": "B", "target": "T", "delay": 2},
        ]}
        res = solve(mk(payload))
        self.assertFalse(res.feasible)
        self.assertEqual(res.max_flow, 0)
        self.assertEqual(res.cut_edges, [])
        self.assertEqual(set(res.source_side_nodes), {"S", "A"})

    def test_exhaustive_random_graphs(self):
        """对全部小图结构穷举路径对，与 MCMF 结果逐一比对。"""
        import random
        rng = random.Random(20260924)
        checked = 0
        for trial in range(400):
            n = rng.randint(2, 5)
            nodes = [f"v{i}" for i in range(n)]
            s, t = nodes[0], nodes[-1]
            fibers = []
            fid = 0
            for u in nodes:
                for v in nodes:
                    if u != v and rng.random() < 0.3:
                        fibers.append({"id": f"e{fid}", "source": u, "target": v,
                                       "delay": rng.randint(0, 9)})
                        fid += 1
            if len(fibers) < 2:
                continue
            # 端点未出现属合法的校验错误，跳过此类随机构图
            endpoints = {x for f in fibers for x in (f["source"], f["target"])}
            if s not in endpoints or t not in endpoints:
                continue
            payload = {"source": s, "sink": t, "fibers": fibers}
            net = mk(payload)
            res = solve(net)
            expected = brute_force_two_paths(net)
            if expected is None:
                self.assertFalse(res.feasible, f"trial {trial} 误报可行: {payload}")
                # 割边数必须等于最大流；且割边确实阻断全部 s-t 路径
                self.assertEqual(len(res.cut_edges), res.max_flow)
                cut_ids = {e.id for e in res.cut_edges}
                for (p, _c) in []:
                    pass
                # 每条简单 s-t 路径必经过至少一条割边
                self._assert_cut_blocks_all_paths(net, cut_ids)
            else:
                self.assertTrue(res.feasible, f"trial {trial} 漏报可行: {payload}")
                self.assertEqual(res.total_delay, expected, f"trial {trial}: {payload}")
            checked += 1
        self.assertGreater(checked, 100)

    def _assert_cut_blocks_all_paths(self, network, cut_ids):
        adj = {}
        for f in network.fibers:
            if f.id not in cut_ids:
                adj.setdefault(f.source, []).append(f.target)
        seen = {network.source}
        stack = [network.source]
        while stack:
            u = stack.pop()
            for v in adj.get(u, []):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        self.assertNotIn(network.sink, seen)


class ValidationTests(unittest.TestCase):
    def _expect(self, payload, needles=()):
        with self.assertRaises(ValidationError) as cm:
            parse_network(payload)
        msgs = " | ".join(e["message"] + e["field"] for e in cm.exception.errors)
        for needle in needles:
            self.assertIn(needle, msgs)
        return cm.exception.errors

    def test_negative_delay(self):
        errs = self._expect({"source": "S", "sink": "T", "fibers": [
            {"id": "a", "source": "S", "target": "T", "delay": -1},
        ]}, ["delay"])
        self.assertEqual(errs[0]["index"], 0)
        self.assertEqual(errs[0]["field"], "delay")

    def test_float_delay_rejected(self):
        self._expect({"source": "S", "sink": "T", "fibers": [
            {"id": "a", "source": "S", "target": "T", "delay": 1.5},
        ]}, ["delay"])

    def test_duplicate_ids(self):
        errs = self._expect({"source": "S", "sink": "T", "fibers": [
            {"id": "a", "source": "S", "target": "x", "delay": 1},
            {"id": "a", "source": "x", "target": "T", "delay": 1},
            {"id": "a", "source": "S", "target": "T", "delay": 1},
        ]}, ["重复"])
        dup = [e for e in errs if e["field"] == "id"]
        self.assertEqual(len(dup), 2)
        self.assertEqual(sorted(e["index"] for e in dup), [1, 2])

    def test_unknown_endpoints(self):
        self._expect({"source": "X", "sink": "T", "fibers": [
            {"id": "a", "source": "S", "target": "T", "delay": 1},
        ]}, ["source"])

    def test_missing_fields_and_types(self):
        errs = self._expect({
            "source": 123, "sink": None,
            "fibers": [
                "not-an-object",
                {"id": "", "source": "S", "target": "T", "delay": "yes"},
            ],
        })
        fields = {(e["index"], e["field"]) for e in errs}
        self.assertIn((-1, "source"), fields)
        self.assertIn((-1, "sink"), fields)
        self.assertIn((0, "_item"), fields)
        self.assertIn((1, "id"), fields)
        self.assertIn((1, "delay"), fields)

    def test_empty_fibers(self):
        self._expect({"source": "S", "sink": "T", "fibers": []}, ["fibers"])

    def test_same_source_sink(self):
        self._expect({"source": "S", "sink": "S", "fibers": [
            {"id": "a", "source": "S", "target": "T", "delay": 1},
        ]}, ["起点与终点"])

    def test_multiple_errors_collected(self):
        """一次提交里的多处问题必须全部定位，而不是只报第一个。"""
        errs = self._expect({"source": "S", "sink": "T", "fibers": [
            {"id": "a", "source": "S", "target": "T", "delay": -3},
            {"id": "a", "source": "S", "target": "T", "delay": 2},
        ]})
        self.assertGreaterEqual(len(errs), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
