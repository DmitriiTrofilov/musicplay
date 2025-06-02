from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
import subprocess
import shlex

app = FastAPI()

@app.get("/stream")
def stream_audio(query: str = Query(..., min_length=1)):
    # Use yt-dlp to search and get the first audio URL
    # We'll download audio in mp3 format directly
    command = (
        f"yt-dlp -o - --quiet --no-warnings "
        f"-f 'bestaudio[ext=m4a]/bestaudio' "
        f"--external-downloader ffmpeg "
        f"--external-downloader-args '-vn -acodec libmp3lame -f mp3' "
        f"ytsearch1:{shlex.quote(query)}"
    )

    try:
        process = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start yt-dlp: {e}")

    if process.stdout is None:
        raise HTTPException(status_code=500, detail="Failed to capture yt-dlp output")

    headers = {
        "Content-Type": "audio/mpeg",
        "Cache-Control": "no-cache",
        "Accept-Ranges": "bytes"
    }

    return StreamingResponse(process.stdout, headers=headers)
