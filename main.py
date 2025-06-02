from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import subprocess
import logging

app = FastAPI()
logging.basicConfig(level=logging.INFO)

@app.get("/")
async def root():
    return {"message": "YouTube Music Streamer backend is running."}

@app.get("/stream")
async def stream(mode: str = "search", query: str = None):
    logging.info(f"Stream requested. Mode: {mode}, Query: {query}")

    if not query:
        raise HTTPException(status_code=400, detail="Query parameter is required")

    if mode == "genre" and query.lower() == "lofi":
        query_url = "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    elif mode == "playlist":
        query_url = query
    else:
        query_url = f"ytsearch:{query}"

    cmd = [
        "yt-dlp",
        "--cookies", "cookies.txt",
        "-f", "bestaudio",
        "-o", "-",
        "--quiet",
        "--no-warnings",
        query_url
    ]

    logging.info(f"Running command: {' '.join(cmd)}")

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return StreamingResponse(process.stdout, media_type="audio/webm")
