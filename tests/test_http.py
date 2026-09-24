"""HTTP API 冒烟/业务测试：启动真实 HTTP 服务，走真实 socket 请求。"""

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.server import create_server


class ServerTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        # 健康入口就绪探测
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                cls.get("/health")
                break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    @classmethod
    def get(cls, path):
        with urllib.request.urlopen(cls.base + path, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)

    @classmethod
    def post(cls, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(cls.base + "/api/solve", data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))


class HttpBusinessTests(ServerTestBase):
    def test_health(self):
        status, body, _ = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_index_served(self):
        status, body, headers = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("双链路", body.decode("utf-8"))
        status2, body2, _ = self.get("/static/app.js")
        self.assertEqual(status2, 200)
        self.assertIn("tokenGate", body2.decode("utf-8"))
        status3, body3, _ = self.get("/static/token-gate.js")
        self.assertEqual(status3, 200)
        self.assertIn("createTokenGate", body3.decode("utf-8"))

    def test_solve_global_optimum(self):
        status, data = self.post({
            "requestId": "r1", "source": "S", "sink": "T",
            "fibers": [
                {"id": "F1", "source": "S", "target": "A", "delay": 0},
                {"id": "F2", "source": "S", "target": "B", "delay": 5},
                {"id": "F3", "source": "A", "target": "B", "delay": 3},
                {"id": "F4", "source": "A", "target": "T", "delay": 10},
                {"id": "F5", "source": "B", "target": "T", "delay": 2},
            ],
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["feasible"])
        self.assertEqual(data["total_delay"], 17)
        self.assertEqual(len(data["routes"]), 2)
        self.assertEqual(data["requestId"], "r1")
        # 段标识与各自延迟齐备且可复算
        for r in data["routes"]:
            self.assertEqual(len(r["fiber_ids"]), len(r["delays"]))
            self.assertEqual(r["delay"], sum(r["delays"]))
        # 边不重复
        all_ids = [fid for r in data["routes"] for fid in r["fiber_ids"]]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        # 贪心对照：失败
        self.assertFalse(data["greedy_compare"]["feasible"])

    def test_solve_cut_bottleneck(self):
        status, data = self.post({
            "requestId": "r2", "source": "S", "sink": "T",
            "fibers": [
                {"id": "E1", "source": "S", "target": "A", "delay": 1},
                {"id": "E2", "source": "S", "target": "A", "delay": 4},
                {"id": "E3", "source": "A", "target": "T", "delay": 2},
            ],
        })
        self.assertEqual(status, 422)
        self.assertFalse(data["feasible"])
        self.assertEqual(data["reason"], "cut_bottleneck")
        self.assertEqual([e["id"] for e in data["cut_edges"]], ["E3"])
        self.assertEqual(set(data["source_side_nodes"]), {"S", "A"})

    def test_unreachable_422(self):
        status, data = self.post({
            "requestId": "r3", "source": "S", "sink": "T",
            "fibers": [
                {"id": "G1", "source": "S", "target": "A", "delay": 3},
                {"id": "G2", "source": "A", "target": "S", "delay": 1},
                {"id": "G3", "source": "B", "target": "T", "delay": 2},
            ],
        })
        self.assertEqual(status, 422)
        self.assertEqual(data["reason"], "unreachable")
        self.assertEqual(data["max_flow"], 0)

    def test_validation_error_locates(self):
        status, data = self.post({
            "source": "S", "sink": "T",
            "fibers": [
                {"id": "a", "source": "S", "target": "T", "delay": -1},
                {"id": "a", "source": "S", "target": "T", "delay": 2},
            ],
        })
        self.assertEqual(status, 400)
        self.assertEqual(data["kind"], "validation")
        fields = {(e["index"], e["field"]) for e in data["errors"]}
        self.assertIn((0, "delay"), fields)
        self.assertIn((1, "id"), fields)

    def test_bad_json(self):
        req = urllib.request.Request(self.base + "/api/solve",
                                     data=b"{not json", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("应返回 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_nan_constant_rejected(self):
        req = urllib.request.Request(self.base + "/api/solve",
                                     data=b'{"source":"S","sink":"T","fibers":'
                                          b'[{"id":"a","source":"S","target":"T","delay":NaN}]}',
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("NaN 应被拒绝")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_unknown_route_404(self):
        try:
            self.get("/nope")
            self.fail()
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
