import io
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

# 与前端约定一致：单文件上传最大 50MB
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB

app = FastAPI(title="Demucs Separation Service", version="1.0.0")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


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

        try:
            cmd = _build_demucs_command(
                input_path=input_path,
                output_root=output_root,
                model=model,
                stems=stems,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        try:
            completed = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired as e:
            raise HTTPException(
                status_code=504,
                detail="Demucs processing timeout.",
            ) from e
        except subprocess.CalledProcessError as e:
            return JSONResponse(
                status_code=500,
                content={
                    "error": "demucs_failed",
                    "detail": e.stderr[-2000:],
                    "task_id": job_id,
                },
            )

        # demucs 默认输出结构：<output_root>/<model>/<track_name>/*.wav
        track_name = os.path.splitext(os.path.basename(input_path))[0]
        stems_dir = os.path.join(output_root, model, track_name)

        zip_buffer, zip_name = _make_zip_from_stems(
            stems_dir=stems_dir,
            stems=stems,
            task_id=job_id,
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

