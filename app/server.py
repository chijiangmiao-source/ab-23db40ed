"""零依赖 HTTP 服务（Python 标准库）。

路由：
- GET  /health         健康检查
- GET  /               编排台页面
- GET  /static/*       静态资源
- POST /api/solve      提交网络，返回双路最优解或割证据

端口由环境变量 PORT 配置（容器内默认 8000），宿主机映射端口由
Compose 的 HOST_PORT 配置（默认 8080）。

状态码约定：
- 200 成功求得边不重复双路；
- 422 网络结构合法但无法形成双路（不可达 / 单路割瓶颈），
  响应体携带源侧节点集合与全部外出割边作为证据；
- 400 输入校验失败（负延迟、重复段标识、端点不存在等），
  响应体 errors 数组逐条定位；
- 4xx 其他请求错误。
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .network_model import ValidationError, parse_network
from .solver import solve

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_BODY = 2 * 1024 * 1024  # 2 MiB

_SOLVE_LOCK = threading.Lock()  # 求解纯计算；锁仅用于串行化极端并发下的自检


def _strict_constant(value: str):
    # NaN / Infinity / -Infinity 一律按非法 JSON 拒绝
    raise json.JSONDecodeError(f"非法数值常量: {value}", value, 0)


def route_to_json(result) -> dict:
    if result.feasible:
        return {
            "feasible": True,
            "source": result.source,
            "sink": result.sink,
            "total_delay": result.total_delay,
            "routes": [
                {
                    "index": r.index,
                    "fiber_ids": r.fiber_ids,
                    "nodes": r.nodes,
                    "delays": r.delays,
                    "delay": r.delay,
                    "delay_expression": r.delay_expression,
                }
                for r in result.routes
            ],
            "greedy_compare": result.greedy,
        }
    if result.max_flow == 0:
        reason = "unreachable"
        message = f"起点 {result.source!r} 无法到达终点 {result.sink!r}：0 条路径，保护链路无从建立"
    else:
        reason = "cut_bottleneck"
        names = "、".join(e.id for e in result.cut_edges)
        message = (f"仅存在 1 条边不重复通路：共享割边（外出瓶颈）{names} 同时横断所有路线，"
                   f"无法形成两条边互不重复的链路")
    return {
        "feasible": False,
        "reason": reason,
        "message": message,
        "source": result.source,
        "sink": result.sink,
        "max_flow": result.max_flow,
        "source_side_nodes": result.source_side_nodes,
        "cut_edges": [
            {"id": e.id, "source": e.source, "target": e.target, "delay": e.delay}
            for e in result.cut_edges
        ],
        "greedy_compare": result.greedy,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "FiberProtect/1.0"

    def log_message(self, fmt, *args):  # 安静日志
        pass

    # ---- 工具 ----
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel: str, ctype: str):
        path = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not path.startswith(STATIC_DIR + os.sep) or not os.path.isfile(path):
            self._send_json(404, {"message": "资源不存在"})
            return
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if path in ("/", "/index.html"):
            self._send_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/static/app.js":
            self._send_static("app.js", "application/javascript; charset=utf-8")
            return
        if path == "/static/token-gate.js":
            self._send_static("token-gate.js", "application/javascript; charset=utf-8")
            return
        if path == "/static/styles.css":
            self._send_static("styles.css", "text/css; charset=utf-8")
            return
        self._send_json(404, {"message": f"未知路径 {path}"})

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/solve":
            self._send_json(404, {"message": f"未知路径 {path}"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"message": "Content-Length 非法"})
            return
        if length <= 0:
            self._send_json(400, {"message": "请求体为空"})
            return
        if length > MAX_BODY:
            self._send_json(413, {"message": "请求体过大"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"), parse_constant=_strict_constant)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"message": f"JSON 解析失败：{exc}"})
            return

        request_id = payload.get("requestId") if isinstance(payload, dict) else None
        try:
            network = parse_network(payload)
        except ValidationError as exc:
            self._send_json(400, {"ok": False, "requestId": request_id,
                                  "kind": "validation", "errors": exc.errors})
            return
        except Exception as exc:  # 防御性：任何结构意外都给出明确报错
            self._send_json(400, {"ok": False, "requestId": request_id,
                                  "kind": "validation",
                                  "errors": [{"index": -1, "field": "_root",
                                              "message": f"输入结构无法解析：{exc}"}]})
            return

        with _SOLVE_LOCK:
            result = solve(network)
        body = route_to_json(result)
        body["ok"] = True
        body["requestId"] = request_id
        self._send_json(200 if result.feasible else 422, body)


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    httpd = create_server(host, port)
    print(f"[fiber-protect] listening on http://{host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
