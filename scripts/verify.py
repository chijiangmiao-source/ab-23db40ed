#!/usr/bin/env python3
"""Compose verify 服务的验收入口，也可在本机直接运行。

执行顺序（任一步失败则最终退出码非 0）：
  1. 构建/静态检查：Python 字节码编译、JS 语法检查（node --check）；
  2. 等待 web 服务 /health 就绪；
  3. HTTP 冒烟：页面、静态资源、404、健康入口；
  4. 业务断言（真实接口）：
       a. 全局最优双路（共享桥反例，贪心必败，全局=17）；
       b. 并行光纤双路；
       c. 共享瓶颈无法双路：源侧节点集合 + 全部外出割边证据；
       d. 终点不可达；
       e. 负延迟 / 重复段标识 / 不存在端点 / 坏 JSON / NaN 定位报错；
  5. Python 单元+HTTP 测试（unittest）；
  6. 前端过期请求防护的 Node 测试（node --test）。

目标地址：
  - 环境变量 VERIFY_TARGET（Compose 中为 http://web:8000）；
  - 未设置时本机自举一个临时服务（端口取 PORT，默认 8000）。
"""

from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []  # (步骤, PASS/FAIL, 说明)


def record(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, PASS if ok else FAIL, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def check(name: str, cond: bool, detail: str = "") -> bool:
    if not cond:
        raise AssertionError(detail or name)
    return True


# ---------------------------------------------------------------- HTTP 工具

def http(method: str, url: str, payload=None, timeout: float = 5.0):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def api(base: str, payload):
    status, _h, raw = http("POST", base + "/api/solve", payload)
    return status, json.loads(raw.decode("utf-8"))


def wait_health(base: str, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            status, _h, raw = http("GET", base + "/health", timeout=2)
            if status == 200 and json.loads(raw) == {"status": "ok"}:
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- 各阶段

def stage_build_checks() -> bool:
    ok = True
    # Python 字节码编译（语法/构建检查）
    try:
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app")):
            for fn in files:
                if fn.endswith(".py"):
                    py_compile.compile(os.path.join(dirpath, fn), doraise=True)
        record("build: python 字节码编译", True)
    except py_compile.PyCompileError as exc:
        record("build: python 字节码编译", False, str(exc))
        ok = False

    # JS 语法检查
    node = shutil.which("node")
    if node:
        for js in ("app/static/app.js", "app/static/token-gate.js"):
            proc = subprocess.run([node, "--check", os.path.join(ROOT, js)],
                                  capture_output=True, text=True)
            if not record(f"build: node --check {js}", proc.returncode == 0,
                          proc.stderr.strip()):
                ok = False
    else:
        record("build: node --check（未安装 node，跳过）", True, "镜像内安装了 nodejs")
    return ok


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


def stage_http_smoke(base: str) -> bool:
    ok = True
    try:
        status, headers, raw = http("GET", base + "/")
        check("index", status == 200 and "双链路".encode() in raw, f"status={status}")
        check("index ctype", "text/html" in headers.get("Content-Type", ""))
        record("smoke: GET / 编排台页面", True)
    except AssertionError as exc:
        record("smoke: GET / 编排台页面", False, str(exc)); ok = False

    for path, needle in (("/static/app.js", b"/api/solve"),
                         ("/static/token-gate.js", b"createTokenGate"),
                         ("/static/styles.css", b".route-card")):
        try:
            status, _h, raw = http("GET", base + path)
            check(path, status == 200 and needle in raw, f"status={status}")
            record(f"smoke: GET {path}", True)
        except AssertionError as exc:
            record(f"smoke: GET {path}", False, str(exc)); ok = False

    try:
        status, _h, _raw = http("GET", base + "/no-such-path")
        check("404", status == 404, f"status={status}")
        record("smoke: 未知路径返回 404", True)
    except AssertionError as exc:
        record("smoke: 未知路径返回 404", False, str(exc)); ok = False
    return ok


def stage_business(base: str) -> bool:
    ok = True

    # a) 全局最优：共享桥反例
    try:
        status, data = api(base, GLOBAL_CASE)
        check("status 200", status == 200, f"status={status}")
        check("feasible", data["feasible"] is True)
        check("total=17", data["total_delay"] == 17, f"total={data['total_delay']}")
        check("两条路", len(data["routes"]) == 2)
        ids0, ids1 = (set(r["fiber_ids"]) for r in data["routes"])
        check("边不重复", ids0.isdisjoint(ids1), f"{ids0} vs {ids1}")
        # 段标识、各自延迟、可复算
        for r in data["routes"]:
            check("段数=延迟数", len(r["fiber_ids"]) == len(r["delays"]))
            check("小计可复算", r["delay"] == sum(r["delays"]),
                  f"{r['delay']} != {sum(r['delays'])}")
            check("起点终点完整", r["nodes"][0] == "S" and r["nodes"][-1] == "T")
        check("总延迟可复算",
              sum(r["delay"] for r in data["routes"]) == data["total_delay"])
        # 贪心对照必须失败，证明结论不是"最短路+删边"冒充
        g = data["greedy_compare"]
        check("贪心必败", g["feasible"] is False
              and g["reason"] == "no_edge_disjoint_second_path", json.dumps(g, ensure_ascii=False))
        check("贪心最短路独占共享桥", g["first_path"] == ["F1", "F3", "F5"])
        record("业务: 共享桥反例 — 全局最优双路=17 且贪心误败", True)
    except AssertionError as exc:
        record("业务: 共享桥反例 — 全局最优双路=17 且贪心误败", False, str(exc)); ok = False

    # b) 并行光纤
    try:
        status, data = api(base, {
            "source": "S", "sink": "T",
            "fibers": [
                {"id": "P1", "source": "S", "target": "T", "delay": 8},
                {"id": "P2", "source": "S", "target": "T", "delay": 3},
                {"id": "P3", "source": "S", "target": "T", "delay": 5},
            ],
        })
        check("status 200", status == 200, f"status={status}")
        check("parallel total 8", data["total_delay"] == 8, str(data.get("total_delay")))
        used = sorted(fid for r in data["routes"] for fid in r["fiber_ids"])
        check("选用 P2/P3", used == ["P2", "P3"], str(used))
        record("业务: 并行光纤双链路（取 3+5=8）", True)
    except AssertionError as exc:
        record("业务: 并行光纤双链路（取 3+5=8）", False, str(exc)); ok = False

    # c) 共享瓶颈：割证据
    try:
        status, data = api(base, {
            "source": "S", "sink": "T",
            "fibers": [
                {"id": "E1", "source": "S", "target": "A", "delay": 1},
                {"id": "E2", "source": "S", "target": "A", "delay": 4},
                {"id": "E3", "source": "A", "target": "T", "delay": 2},
            ],
        })
        check("422", status == 422, f"status={status}")
        check("cut reason", data["reason"] == "cut_bottleneck", data.get("reason"))
        check("max_flow=1", data["max_flow"] == 1)
        check("源侧集合", set(data["source_side_nodes"]) == {"S", "A"},
              str(data["source_side_nodes"]))
        cuts = data["cut_edges"]
        check("唯一外出割边 E3", [c["id"] for c in cuts] == ["E3"], str(cuts))
        # 割边定义自检：尾在源侧、头不在
        for c in cuts:
            check("割边跨集合", c["source"] in data["source_side_nodes"]
                  and c["target"] not in data["source_side_nodes"])
        check("割容量=最大流", len(cuts) == data["max_flow"])
        # 删除割边后确实不可达（证据有效性）
        cut_ids = {c["id"] for c in cuts}
        remain = [f for f in [
            ("S", "A", "E1"), ("S", "A", "E2"), ("A", "T", "E3")
        ] if f[2] not in cut_ids]
        adj: dict[str, list[str]] = {}
        for u, v, _ in remain:
            adj.setdefault(u, []).append(v)
        seen, stack = {"S"}, ["S"]
        while stack:
            u = stack.pop()
            for v in adj.get(u, []):
                if v not in seen:
                    seen.add(v); stack.append(v)
        check("割证据阻断全部路径", "T" not in seen)
        record("业务: 共享瓶颈 — 源侧集合+外出割边 E3，割容量=最大流", True)
    except AssertionError as exc:
        record("业务: 共享瓶颈 — 源侧集合+外出割边", False, str(exc)); ok = False

    # d) 不可达
    try:
        status, data = api(base, {
            "source": "S", "sink": "T",
            "fibers": [
                {"id": "G1", "source": "S", "target": "A", "delay": 3},
                {"id": "G2", "source": "A", "target": "S", "delay": 1},
                {"id": "G3", "source": "B", "target": "T", "delay": 2},
            ],
        })
        check("422", status == 422, f"status={status}")
        check("unreachable", data["reason"] == "unreachable")
        check("max_flow=0", data["max_flow"] == 0)
        check("源侧不含 T", "T" not in data["source_side_nodes"])
        record("业务: 终点不可达 — max_flow=0 且给出源侧集合", True)
    except AssertionError as exc:
        record("业务: 终点不可达", False, str(exc)); ok = False

    # e) 输入错误定位
    try:
        status, data = api(base, {
            "source": "S", "sink": "T",
            "fibers": [
                {"id": "a", "source": "S", "target": "T", "delay": -7},
                {"id": "a", "source": "S", "target": "T", "delay": 2},
            ],
        })
        check("400", status == 400, f"status={status}")
        loc = {(e["index"], e["field"]) for e in data["errors"]}
        check("负延迟定位到 (0,delay)", (0, "delay") in loc, str(loc))
        check("重复段标识定位到 (1,id)", (1, "id") in loc, str(loc))
        record("业务: 负延迟+重复段标识逐条定位 400", True)
    except AssertionError as exc:
        record("业务: 负延迟+重复段标识定位", False, str(exc)); ok = False

    try:
        status, data = api(base, {
            "source": "X", "sink": "T",
            "fibers": [{"id": "a", "source": "S", "target": "T", "delay": 1}],
        })
        check("400", status == 400, f"status={status}")
        fields = {e["field"] for e in data["errors"]}
        check("不存在端点定位 source", "source" in fields, str(fields))
        record("业务: 不存在端点 400 定位", True)
    except AssertionError as exc:
        record("业务: 不存在端点定位", False, str(exc)); ok = False

    try:
        req = urllib.request.Request(base + "/api/solve", data=b"{bad json",
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("坏 JSON 未拒绝")
        except urllib.error.HTTPError as e:
            check("坏JSON 400", e.code == 400, f"status={e.code}")
        record("业务: 坏 JSON 返回 400", True)
    except AssertionError as exc:
        record("业务: 坏 JSON", False, str(exc)); ok = False

    try:
        req = urllib.request.Request(
            base + "/api/solve", method="POST",
            data=('{"source":"S","sink":"T","fibers":'
                  '[{"id":"a","source":"S","target":"T","delay":NaN}]}').encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("NaN 未拒绝")
        except urllib.error.HTTPError as e:
            check("NaN 400", e.code == 400, f"status={e.code}")
        record("业务: NaN 延迟返回 400", True)
    except AssertionError as exc:
        record("业务: NaN 延迟", False, str(exc)); ok = False

    return ok


def stage_python_tests() -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT, capture_output=True, text=True,
    )
    ok = proc.returncode == 0
    record(f"测试: Python unittest（{'通过' if ok else '失败'}）", ok,
           proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "")
    if not ok:
        print(proc.stderr)
    return ok


def stage_node_tests() -> bool:
    node = shutil.which("node")
    if not node:
        record("测试: node --test（未安装 node，跳过）", True)
        return True
    import glob
    files = sorted(glob.glob(os.path.join(ROOT, "tests", "*.test.mjs")))
    if not files:
        record("测试: node --test（未找到测试文件）", False)
        return False
    proc = subprocess.run(
        [node, "--test", *files],
        cwd=ROOT, capture_output=True, text=True,
    )
    ok = proc.returncode == 0
    tail = [l for l in proc.stdout.splitlines() if "# tests" in l or "# pass" in l or "# fail" in l]
    record("测试: Node 过期请求防护（node --test）", ok, " / ".join(tail))
    if not ok:
        print(proc.stdout)
        print(proc.stderr)
    return ok


def spawn_local_server() -> tuple[subprocess.Popen, str]:
    port = os.environ.get("PORT", "8000")
    env = dict(os.environ, HOST="127.0.0.1", PORT=port)
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.server"], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc, f"http://127.0.0.1:{port}"


def main() -> int:
    target = os.environ.get("VERIFY_TARGET")
    proc = None
    if not target:
        proc, target = spawn_local_server()
        print(f"[verify] 本机自举服务: {target}")
    else:
        print(f"[verify] 校验目标: {target}")

    try:
        healthy = wait_health(target)
        record("就绪: /health 健康入口", healthy, target)
        if not healthy:
            return summarize(False)

        gates = [
            stage_build_checks(),
            stage_http_smoke(target),
            stage_business(target),
            stage_python_tests(),
            stage_node_tests(),
        ]
        return summarize(all(gates))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def summarize(ok: bool) -> int:
    print("\n================ 验收汇总 ================")
    for name, status, detail in results:
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    print(f"\n合计 {len(results)} 项，失败 {n_fail} 项。")
    print("验收结果：" + ("全部通过 ✅" if ok and n_fail == 0 else "存在失败 ❌"))
    return 0 if ok and n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
