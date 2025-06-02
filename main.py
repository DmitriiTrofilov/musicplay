from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/stream")
def stream(mode: str = Query(...), query: str = Query(...)):
    logger.info(f"==> Received /stream request | mode: {mode} | query: {query}")

    try:
        if mode == "playlist":
            yt_query = query
            cmd = ["yt-dlp", "-j", "-f", "bestaudio", yt_query]
        else:
            yt_query = f"ytsearch:{query}"
            cmd = ["yt-dlp", "-j", "-f", "bestaudio", yt_query]

        logger.info(f"==> Running command: {' '.join(cmd)}")

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        videos = []
        for line in result.stdout.decode().splitlines():
            info = json.loads(line)
            track_info = {
                "title": info.get("title"),
                "url": info.get("url")
            }
            logger.info(f"==> Fetched track: {track_info['title']}")
            videos.append(track_info)

        logger.info(f"==> Returning {len(videos)} track(s)")
        return {"tracks": videos}

    except subprocess.CalledProcessError as e:
        error_output = e.stderr.decode()
        logger.error(f"==> yt-dlp error: {error_output}")
        return {"error": "yt-dlp failed", "details": error_output}

    except Exception as e:
        logger.exception("==> Unexpected error")
        return {"error": "Unexpected error", "details": str(e)}
