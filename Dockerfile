# 束线保护双链路编排服务
# 运行时零第三方依赖：仅用 Python 标准库；
# 安装 nodejs 仅供 verify 阶段的 JS 语法检查与前端测试。
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/ ./app/
COPY tests/ ./tests/
COPY scripts/ ./scripts/

ENV HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=10 \
    CMD python3 -c "import json,urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2); sys.exit(0 if json.load(r)=={'status':'ok'} else 1)"

CMD ["python3", "-m", "app.server"]
