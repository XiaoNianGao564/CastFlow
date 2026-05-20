# CastFlow - Docker 部署
# Python 3.11-slim + 国内镜像加速
# 构建: docker build -t castflow:latest .
# 运行: docker compose up -d

FROM python:3.11-slim

# 国内 apt 镜像（清华）
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's/security.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources \
    && rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends gcc libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 国内 pip 镜像（清华）
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

# 先复制依赖声明，利用 docker build cache
COPY pyproject.toml .
COPY castflow/ castflow/

# 安装项目 + 可选依赖（full = chromadb / fastapi / streamlit 等）
RUN pip install --no-cache-dir -e ".[full]"

# 额外兼容包（langchain-openai 被 langchain-community 引用）
RUN pip install --no-cache-dir langchain-openai langchain-mcp-adapters

# 复制其余代码
COPY api/ api/
COPY streamlit_app.py .
COPY run.py .
COPY .env.example .env

# 启动脚本
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000 8501

ENTRYPOINT ["/entrypoint.sh"]
