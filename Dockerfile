FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 安装系统依赖：ffmpeg 用于音频处理，git 用于拉取 demucs 仓库
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（FastAPI 等）
COPY demucs_service/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# 从推荐的 fork 安装 Demucs
RUN git clone https://github.com/adefossez/demucs.git /app/demucs \
    && pip install -e /app/demucs

# 固化运行时依赖，避免线上反复出现：
# 1) RuntimeError: Numpy is not available
# 2) TorchCodec is required for torchaudio save
RUN pip install --no-cache-dir "numpy<2" torchcodec

# 拷贝服务代码
COPY demucs_service/ /app/

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]

