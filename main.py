# main.py
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/stream")
def stream(mode: str = Query(...), query: str = Query(...)):
    try:
        if mode == "playlist":
            yt_query = query
            cmd = ["yt-dlp", "-j", "-f", "bestaudio", yt_query]
        else:
            yt_query = f"ytsearch:{query}"
            cmd = ["yt-dlp", "-j", "-f", "bestaudio", yt_query]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        
        videos = []
        for line in result.stdout.decode().splitlines():
            info = json.loads(line)
            videos.append({
                "title": info.get("title"),
                "url": info.get("url")
            })

        return {"tracks": videos}
    
    except subprocess.CalledProcessError as e:
        return {"error": e.stderr.decode()}
