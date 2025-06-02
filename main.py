from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
import subprocess
import asyncio
import os

app = FastAPI()

COOKIE_FILE = "cookies.txt"  # Make sure this file is uploaded with backend

@app.get("/stream")
async def stream(mode: str = Query(...), query: str = Query(...)):
    print(f"[INFO] Received request: mode={mode}, query={query}")

    if not os.path.exists(COOKIE_FILE):
        raise HTTPException(status_code=500, detail="cookies.txt not found on server")

    if mode == "playlist":
        yt_url = query
    elif mode == "song":
        yt_url = query
    elif mode == "genre":
        # Simple ytsearch for genre term
        yt_url = f"ytsearch:{query}"
    else:
        raise HTTPException(status_code=400, detail="Invalid mode")

    cmd = [
        "yt-dlp",
        "--cookies", COOKIE_FILE,
        "-f", "bestaudio",
        "-g",  # Get direct media URL
        yt_url
    ]

    print(f"[INFO] Running command: {' '.join(cmd)}")

    # Run command async, get media URL
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        error_message = stderr.decode()
        print(f"[ERROR] yt-dlp error: {error_message}")
        raise HTTPException(status_code=500, detail="Failed to extract media URL")

    media_url = stdout.decode().strip().split("\n")[0]  # Use first URL
    print(f"[INFO] Media URL: {media_url}")

    # Stream audio from direct URL (pass through)
    def iterfile():
        import requests
        with requests.get(media_url, stream=True) as r:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

    return StreamingResponse(iterfile(), media_type="audio/mpeg")
