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

- **方法**: `POST /api/v1/separate`（兼容 `POST /separate`）
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

## 业务联调：如何调用接口

假设服务已部署，基地址为 `http://<主机>:8001`（本地开发为 `http://127.0.0.1:8001`）。

### 传参方式

- **Content-Type**：必须为 `multipart/form-data`（上传文件 + 表单字段）。
- **必填**：`audio` — 一个音频文件（字段名固定为 `audio`）。
- **可选**：
  - `model`：字符串，默认 `htdemucs`。
  - `stems`：整数 `2` 或 `4`，默认 `2`（2=人声+伴奏，4=人声+鼓+贝斯+其他）。
  - `task_id`：字符串，用于日志/追踪，不传则服务端自动生成。

### cURL 示例（快速自测）

```bash
# 健康检查
curl http://127.0.0.1:8001/healthz

# 人声分离（两轨：人声 + 伴奏）
curl -X POST http://127.0.0.1:8001/api/v1/separate \
  -F "audio=@/path/to/your/song.mp3" \
  -F "task_id=my-task-001" \
  --output result.zip

# 四轨分离，并指定 model
curl -X POST http://127.0.0.1:8001/api/v1/separate \
  -F "audio=@/path/to/song.wav" \
  -F "model=htdemucs" \
  -F "stems=4" \
  -F "task_id=biz-123" \
  --output stems.zip
```

成功时终端会得到 `result.zip` / `stems.zip`，解压后为 `vocals.wav`、`accompaniment.wav`（或 4 轨时的 drums/bass/other）。

### Python 联调示例

```python
import requests

BASE = "http://127.0.0.1:8001"  # 联调时改为你的服务地址

# 1. 健康检查
r = requests.get(f"{BASE}/healthz")
print(r.json())  # {"status": "ok"}

# 2. 人声分离
with open("/path/to/your/audio.mp3", "rb") as f:
    r = requests.post(
        f"{BASE}/api/v1/separate",  # 或 /separate
        files={"audio": ("song.mp3", f, "audio/mpeg")},
        data={
            "model": "htdemucs",
            "stems": 2,
            "task_id": "my-biz-task-001",
        },
        timeout=620,
    )

if r.status_code == 200:
    with open("output_stems.zip", "wb") as out:
        out.write(r.content)
    print("ZIP 已保存为 output_stems.zip")
else:
    print(r.status_code, r.json())
```

### 联调注意点

| 项目 | 说明 |
|------|------|
| 字段名 | 文件字段**必须**叫 `audio`，否则服务端会 422。 |
| 上传大小 | 本服务支持单文件最大 **50MB**。若经 Nginx 等反向代理，须在代理侧调大限制（见下方）。 |
| 超时 | 分离可能较久（几十秒到几分钟），建议客户端 timeout ≥ 600 秒。 |
| 成功响应 | `Content-Type: application/zip`，直接写为文件即可。 |
| 失败响应 | `Content-Type: application/json`，含 `error`、`detail`、`task_id`，便于排查。 |
| 同机调用 | 业务与本品同机部署时，可直接用 `http://127.0.0.1:8001`，无需暴露公网。 |
| 处理记录 | 每次收到音频会打日志：任务 ID、文件名、大小、预期处理时间；分离过程中 demucs 进度会实时打到 stdout；结束时输出实际耗时与 zip 大小。查看容器或进程 stdout 即可看到处理过程。 |

**上传被拒（“File too large” / 413）**：多数是**反向代理或 BFF 的请求体上限**小于 50MB。  
- **Nginx**：在 `server` 或 `location` 中设置 `client_max_body_size 50m;` 后重载配置。  
- **Next.js 代理/API 路由**：在 `next.config.js` 或对应 API 路由里调大 body 解析大小（例如 50MB），否则默认约 1MB～4MB 就会拒掉请求。

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

## 如何验证服务是否可正常交互

服务启动后，可用下面两种方式确认能正常访问和人声分离。

**1. 健康检查（curl）**

```bash
curl http://127.0.0.1:8001/healthz
# 期望: {"status":"ok"}
```

**2. 冒烟测试（推荐）**

仓库内自带脚本 `demucs_service/smoke_test.py`，会请求 `/healthz` 并上传一段极短 WAV 到 `/api/v1/separate`，校验返回为 zip。**仅用 Python 标准库，无需额外依赖。**

```bash
# 先在一个终端启动服务（见上方「本地开发」）
cd demucs_service
uvicorn main:app --host 0.0.0.0 --port 8001

# 在另一个终端执行
cd demucs_service
python smoke_test.py
# 可选：指定服务地址
python smoke_test.py http://127.0.0.1:8001
```

若输出包含「全部通过，服务可正常交互。」即表示健康检查与人声分离接口均可成功交互。若分离步骤报错，请确认已安装 demucs（`pip install "git+https://github.com/adefossez/demucs.git"`）。

---

## 一、在腾讯云上新增 CVM 实例

1. **登录腾讯云控制台**  
   打开 [腾讯云控制台](https://console.cloud.tencent.com/) → 进入 **云服务器 CVM**。

2. **购买/新建实例**  
   - 点击 **新建** 或 **购买实例**。  
   - **计费模式**：按需选「按量计费」或「包年包月」。  
   - **地域与可用区**：选离你用户近的（如国内选广州/上海等）。  
   - **实例规格**：选 **标准型**，例如 **2核 8GB**（SA5.MEDIUM8）即可满足「每天 5 首、每首约 3 分钟」的人声分离。  
   - **镜像**：选 **Ubuntu Server 22.04 LTS 或 24.04 LTS**（64 位）。  
   - **系统盘**：建议 **50GB 及以上** 通用型 SSD。  
   - **网络**：选默认 VPC；**公网 IP** 选择「分配」；带宽按需（如 5–10 Mbps 或按流量）。  
   - **安全组**：新建或选已有；**务必在下方「安全组规则」中放行 SSH（22）**，否则无法登录。  

3. **设置登录方式**  
   - 选择 **SSH 密钥** 或 **密码**，并记住/下载。  
   - 若用密钥：下载 `.pem` 后本地执行 `chmod 400 xxx.pem`，用 `ssh -i xxx.pem ubuntu@公网IP` 登录。  

4. **完成购买**  
   实例创建成功后，在 CVM 列表里看到 **公网 IP**，用 SSH 连接即可。

5. **（可选）放行人声分离服务端口**  
   若要从外网访问人声分离接口，在实例的 **安全组** → **入站规则** 中新增一条：  
   - 协议端口：`TCP:8001`  
   - 来源：你的业务 IP 或 `0.0.0.0/0`（仅测试用，生产建议限制 IP）。

---

## 二、把代码部署到腾讯云（Docker）

以下在 **已有一台可 SSH 登录的腾讯云 CVM** 上执行。

### 1. SSH 登录服务器

```bash
# 密钥登录示例
ssh -i /path/to/your.pem ubuntu@<你的公网IP>

# 或密码登录
ssh ubuntu@<你的公网IP>
```

### 2. 安装 Docker（若未安装）

```bash
sudo apt update
sudo apt install -y docker.io
sudo systemctl enable docker
sudo systemctl start docker
```

### 3. 拉取代码并构建镜像

```bash
git clone https://github.com/Curry-Nice/vocal-remover.git
cd vocal-remover

sudo docker build -t demucs-service:latest .
```

构建可能需几分钟（会下载 Python、Demucs 依赖和模型）。

### 4. 运行容器

```bash
sudo docker run -d \
  --name demucs-service \
  -p 8001:8001 \
  --restart=always \
  demucs-service:latest
```

### 5. 验证部署

```bash
# 看容器是否在跑
sudo docker ps

# 健康检查
curl http://127.0.0.1:8001/healthz
# 应返回 {"status":"ok"}
```

若从本机访问：浏览器或 curl 访问 `http://<服务器公网IP>:8001/healthz`（需安全组已放行 8001）。

### 6. 后续更新代码

```bash
cd /path/to/vocal-remover   # 即你 clone 的目录
git pull
sudo docker build -t demucs-service:latest .
sudo docker stop demucs-service
sudo docker rm demucs-service
sudo docker run -d --name demucs-service -p 8001:8001 --restart=always demucs-service:latest
```

---

安全组说明：若其他业务和本服务在同一台机器，可只访问 `http://127.0.0.1:8001/api/v1/separate`（或 `http://127.0.0.1:8001/separate`），无需对公网开放 8001。

