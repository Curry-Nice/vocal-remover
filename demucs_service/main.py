import io
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

# 与前端约定一致：单文件上传最大 50MB
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Demucs Separation Service", version="1.0.0")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


def _estimate_processing_sec(file_size_bytes: int) -> tuple[float, str]:
    """
    根据文件大小粗估处理时间（启发式：约 1MB 对应 1 分钟音频，处理约 3–8 倍实时）。
    返回 (预计秒数, 可读描述)。
    """
    size_mb = file_size_bytes / (1024 * 1024)
    # 假设 1MB ≈ 0.5–1 分钟音频，处理时间约 5 倍实时
    audio_min = max(0.1, size_mb * 0.8)
    est_sec = audio_min * 60 * 5  # 约 5x 实时
    if est_sec < 60:
        desc = f"约 {int(est_sec)} 秒"
    else:
        desc = f"约 {est_sec / 60:.1f} 分钟"
    return min(est_sec, 600), desc  # 上限 10 分钟


def _build_demucs_command(
    input_path: str,
    output_root: str,
    model: str,
    stems: int,
) -> list[str]:
    if stems not in (2, 4):
        raise ValueError("stems must be 2 or 4")

    # demucs CLI: python -m demucs -n <model> [--two-stems vocals] -o <outdir> <audio>
    cmd = [
        "python",
        "-m",
        "demucs",
        "-n",
        model,
        "-o",
        output_root,
    ]

    if stems == 2:
        # 两轨：人声 + 伴奏（demucs 输出 vocals.wav 和 no_vocals.wav）
        cmd += ["--two-stems", "vocals"]

    cmd.append(input_path)
    return cmd


def _make_zip_from_stems(
    stems_dir: str,
    stems: int,
    task_id: Optional[str] = None,
) -> tuple[io.BytesIO, str]:
    if not os.path.isdir(stems_dir):
        raise FileNotFoundError(f"Stems directory not found: {stems_dir}")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in os.listdir(stems_dir):
            if not name.lower().endswith(".wav"):
                continue
            src = os.path.join(stems_dir, name)
            arcname = name

            # 两轨时把 no_vocals.wav 重命名为 accompaniment.wav
            if stems == 2 and name == "no_vocals.wav":
                arcname = "accompaniment.wav"

            zf.write(src, arcname)

    zip_buffer.seek(0)
    zip_name = f"{task_id or 'separation'}_stems.zip"
    return zip_buffer, zip_name


@app.post("/api/v1/separate")
@app.post("/separate")
async def separate(
    request: Request,
    audio: UploadFile = File(...),
    model: str = Form("htdemucs"),
    stems: int = Form(2),
    task_id: Optional[str] = Form(None),
):
    # 若代理未限制，在此统一限制请求体大小，与前端 50MB 一致
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="File too large for the separation service (server limit). Try a smaller or shorter file.",
                )
        except ValueError:
            pass  # 无效 content-length 时继续，由后续逻辑处理

    job_id = task_id or str(uuid.uuid4())
    job_dir = tempfile.mkdtemp(prefix=f"demucs_job_{job_id}_")
    input_path = os.path.join(job_dir, audio.filename or "input_audio")
    output_root = os.path.join(job_dir, "output")

    try:
        # 保存上传的音频到本地临时文件
        with open(input_path, "wb") as f:
            shutil.copyfileobj(audio.file, f)

        file_size = os.path.getsize(input_path)
        est_sec, est_desc = _estimate_processing_sec(file_size)
        filename = audio.filename or "input_audio"
        logger.info(
            "[%s] 收到音频 filename=%s size=%.2f MB model=%s stems=%s 预期处理时间 %s",
            job_id,
            filename,
            file_size / (1024 * 1024),
            model,
            stems,
            est_desc,
        )

        try:
            cmd = _build_demucs_command(
                input_path=input_path,
                output_root=output_root,
                model=model,
                stems=stems,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        logger.info("[%s] 开始分离: %s", job_id, " ".join(cmd))
        start_time = time.perf_counter()
        stderr_lines: list[str] = []

        def _log_stderr(stream: io.TextIOWrapper) -> None:
            for line in iter(stream.readline, ""):
                line = line.rstrip()
                if line:
                    stderr_lines.append(line)
                    logger.info("[%s] demucs: %s", job_id, line)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            reader = threading.Thread(target=_log_stderr, args=(proc.stderr,))
            reader.daemon = True
            reader.start()
            try:
                proc.wait(timeout=600)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise
            reader.join(timeout=2)
            if proc.returncode != 0:
                stderr_text = "\n".join(stderr_lines)[-2000:]
                elapsed = time.perf_counter() - start_time
                logger.warning(
                    "[%s] 分离失败 returncode=%s 耗时 %.1fs",
                    job_id,
                    proc.returncode,
                    elapsed,
                )
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "demucs_failed",
                        "detail": stderr_text,
                        "task_id": job_id,
                    },
                )
        except subprocess.TimeoutExpired as e:
            elapsed = time.perf_counter() - start_time
            logger.warning("[%s] 分离超时 耗时 %.1fs", job_id, elapsed)
            raise HTTPException(
                status_code=504,
                detail="Demucs processing timeout.",
            ) from e

        elapsed = time.perf_counter() - start_time
        logger.info("[%s] 分离完成 耗时 %.1f 秒", job_id, elapsed)

        # demucs 默认输出结构：<output_root>/<model>/<track_name>/*.wav
        track_name = os.path.splitext(os.path.basename(input_path))[0]
        stems_dir = os.path.join(output_root, model, track_name)

        zip_buffer, zip_name = _make_zip_from_stems(
            stems_dir=stems_dir,
            stems=stems,
            task_id=job_id,
        )
        zip_size = len(zip_buffer.getvalue())
        logger.info(
            "[%s] 返回 zip %s 大小 %.2f MB",
            job_id,
            zip_name,
            zip_size / (1024 * 1024),
        )

        headers = {
            "Content-Disposition": f'attachment; filename="{zip_name}"'
        }
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers=headers,
        )
    finally:
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            # 清理失败不影响主流程
            pass

