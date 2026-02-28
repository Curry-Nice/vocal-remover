# Demucs 人声分离服务

这是一个基于 [adefossez/demucs](https://github.com/adefossez/demucs) 的独立人声分离 HTTP 服务，适合作为其他业务的后端依赖。

服务使用 FastAPI 实现，并通过 Docker 容器部署，推荐在腾讯云 CVM 上运行。

---

## HTTP 接口

### 1. 健康检查

- **方法**: `GET /healthz`  
- **返回示例**:

```json
{ "status": "ok" }
```

### 2. 音频人声分离

- **方法**: `POST /api/v1/separate`
- **Content-Type**: `multipart/form-data`
- **请求字段**:
  - `audio` (必填): 音频文件，支持 `wav/mp3/flac/m4a` 等
  - `model` (可选): Demucs 模型名，默认 `htdemucs`
  - `stems` (可选): `2` 或 `4`，默认 `2`
    - `2`: 输出 `vocals.wav` 和 `accompaniment.wav`
    - `4`: 输出 `vocals.wav`, `drums.wav`, `bass.wav`, `other.wav`
  - `task_id` (可选): 任务 ID，方便调用方关联日志

- **成功返回**:
  - 状态码: `200`
  - 类型: `application/zip`
  - 内容: 一个 zip 文件，包含上述约定的若干 `*.wav`

- **失败返回**:
  - 状态码: `4xx/5xx`
  - 类型: `application/json`
  - 示例:

```json
{
  "error": "demucs_failed",
  "detail": "stderr last lines ...",
  "task_id": "your-task-id"
}
```

---

## 本地开发（可选）

```bash
cd demucs_service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 需要提前在环境中安装 demucs（例如）
pip install "git+https://github.com/adefossez/demucs.git"

uvicorn main:app --host 0.0.0.0 --port 8001
```

---

## 使用 Docker 部署（腾讯云 CVM）

### 1. 推送到 GitHub

1. 在本地创建一个新的 Git 仓库（如果尚未创建）:

```bash
cd /path/to/your/vocal-remover
git init
git add .
git commit -m "Initial demucs separation service"
git remote add origin git@github.com:<your-account>/<your-repo>.git
git push -u origin main
```

2. 腾讯云上使用 `git clone` 拉取该仓库。

### 2. 在腾讯云上构建镜像

SSH 登录到你的腾讯云服务器后：

```bash
git clone https://github.com/<your-account>/<your-repo>.git
cd <your-repo>

sudo docker build -t demucs-service:latest .
```

### 3. 运行容器

```bash
sudo docker run -d \
  --name demucs-service \
  -p 8001:8001 \
  --restart=always \
  demucs-service:latest
```

容器启动后，服务会监听 `8001` 端口：

- 健康检查: `GET http://<服务器IP>:8001/healthz`
- 分离接口: `POST http://<服务器IP>:8001/api/v1/separate`

### 4. 安全组配置

在腾讯云控制台中，为这台 CVM 的安全组添加入站规则：

- 协议端口: `TCP 8001`
- 源: 你的业务所在 IP 段（或内网），避免对所有公网开放

如果你的其他业务和本服务在同一台机器上运行，可以只在本地访问：

- `http://127.0.0.1:8001/api/v1/separate`

这样就不需要在安全组中暴露 8001 端口到公网。

