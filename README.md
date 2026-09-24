# 束线保护信号 · 双链路编排台

在有向光纤网络上，为给定**起点 → 终点**求两条**边（光纤段）互不重复**的完整链路，
使两路延迟之和**全局最小**；无法形成双路时，给出阻断保护链路的**源侧节点集合**与
**全部外出割边**作为证据。

## 业务语义

- 输入：有向光纤段列表，每段含唯一 `id`、`source`、`target`、非负整数 `delay`；
  节点可重合，同一对端点允许并行光纤（按不同段标识分别计容）。
- 输出（可行）：两条路径的**段标识序列、各自逐段延迟、可复算小计**，以及
  `各小计之和 = 最小总延迟`。
- 输出（不可行）：残余网络中自起点可达的**源侧节点集合**，以及跨越该集合的
  **全部外出割边**；割边数（割容量，每段容量为 1）等于最大流（0=不可达，1=单路瓶颈），
  由最大流最小割定理证明双路物理不可行。

## 算法：为什么不是"先最短路再删边"

问题等价于容量均为 1、费用为延迟的**最小费用 2-流**。实现采用
Successive Shortest Path + 结点势能 Dijkstra（`app/solver.py`），增广时允许沿
反向弧**重路由**已分配的流，因此得到的是全局最优整数解。

页面内置招牌反例（延迟：S→A=0，S→B=5，A→B=3，A→T=10，B→T=2）：

- 唯一最短路 `S-A-B-T = 5` 独占共享桥 `B→T`；
- "先求最短路再删边"的贪心在第二条路上**误报无双路**；
- 全局最优为 `S-A-T = 10` 与 `S-B-T = 7`，**总延迟 17**。

接口响应同时返回 `greedy_compare`（贪心结论），页面透明展示二者差异，
杜绝以贪心冒充全局结论。正确性另由 `tests/test_solver.py` 对数百张随机小图
与**穷举全部路径对**的基准逐一比对，并自检割容量=最大流。

## 输入校验（定位报错并清除旧结论）

负延迟、小数/NaN、重复段标识、空/错类型字段、不存在的 source/sink 端点、
坏 JSON 等均返回 `400`，`errors[]` 精确到行号（段数组下标）与字段，一次返回全部问题；
前端收到错误即清除旧结论并高亮问题行。结构合法但不可达/无双路返回 `422` 并附割证据。

## 过期请求保护

前端每次提交领取单调令牌（`app/static/token-gate.js`），响应晚到时若已有更新的草稿
提交，该响应一律丢弃，**过期请求不会覆盖新草稿**。该逻辑由 Node 测试覆盖乱序场景。

## 运行（Docker Compose）

```bash
# 可选：配置宿主机端口（默认 8080）
echo "HOST_PORT=8080" > .env

docker compose up --build
# 健康入口（宿主机端口）：
curl http://localhost:8080/health      # {"status": "ok"}
# 编排台页面：http://localhost:8080/
```

## 一键验收

```bash
docker compose run --rm verify
```

`verify` 等待 `web` 健康检查通过后，通过**真实 HTTP 接口**执行：

1. 构建检查：Python 字节码编译、`node --check` JS 语法；
2. API/HTTP 冒烟：`/health`、编排台页面、静态资源、404；
3. 业务断言：全局最优双路（共享桥反例，贪心必败，总延迟 17）、并行光纤双路、
   共享瓶颈的源侧集合+外出割边（并验证删割边后确实阻断全部路径）、不可达、
   各类非法输入定位；
4. Python `unittest`（算法 + HTTP 集成，含随机穷举对拍）；
5. `node --test`（过期请求防护）。

全部完成后进程退出，**退出码 0/非 0 即验收结论**。

## 本机无 Docker 运行

服务与校验均零第三方 Python 依赖（Node 仅用于前端测试/语法检查，缺失时自动跳过）：

```bash
python3 scripts/verify.py            # 自举临时服务并完成全部验收
PORT=8000 python3 -m app.server      # 直接启动服务
python3 -m unittest discover -s tests -v
node --test tests/
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 `{"status":"ok"}` |
| GET | `/` | 编排台页面 |
| POST | `/api/solve` | 求解；200 双路最优 / 422 割证据 / 400 校验错误 |

请求体示例：

```json
{
  "source": "S", "sink": "T",
  "fibers": [
    {"id": "F1", "source": "S", "target": "A", "delay": 0},
    {"id": "F2", "source": "S", "target": "B", "delay": 5}
  ]
}
```

## 目录结构

```
app/
  network_model.py   # 域模型与输入校验（精确定位）
  solver.py          # 最小费用流双路求解 + 最小割证据 + 贪心对照
  server.py          # 零依赖 HTTP 服务（健康入口/页面/API）
  static/            # 编排台前端（令牌防过期、错误定位、双路/割证据渲染）
tests/               # Python 单元+HTTP 集成测试、Node 令牌测试
scripts/verify.py    # Compose verify 一键验收入口
Dockerfile, docker-compose.yml
```
