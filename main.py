from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_best_audio_url(query: str) -> str:
    # Run yt-dlp to get best audio URL of the first search result
    cmd = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "--skip-download",
        "--print", "url",
        "-f", "bestaudio",
        f"ytsearch1:{query}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise Exception("yt-dlp failed or no URL found")
    url = result.stdout.strip()
    return url

@app.get("/stream")
async def stream(query: str = Query(...)):
    try:
        audio_url = get_best_audio_url(query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Option 1: Redirect client to audio URL (simpler, but client must support playing that URL)
    return RedirectResponse(audio_url)

    # Option 2: (alternative) Stream audio through backend (proxy)
    # res = requests.get(audio_url, stream=True)
    # return StreamingResponse(res.iter_content(chunk_size=8192), media_type="audio/mpeg")
