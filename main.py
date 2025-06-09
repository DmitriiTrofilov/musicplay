from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp
import os
import logging
import json
import subprocess
import time

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# --- YouTube Music API Setup ---
try:
    # Using a brand header is good practice if you have one, otherwise default is fine
    ytmusic = YTMusic(requests_session=True)
    logger.info("YTMusic initialized successfully with a persistent session.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}", exc_info=True)

COOKIES_FILE_PATH = 'cookies.txt'
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

# --- Helper for yt-dlp options ---
def get_ydl_opts(extra_opts=None):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
        'noplaylist': True,
        'retries': 10,
        'fragment_retries': 10,
        'socket_timeout': 30,
    }
    if os.path.exists(absolute_cookies_path):
        opts['cookies'] = absolute_cookies_path
    if extra_opts:
        opts.update(extra_opts)
    return opts

@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Backend is running and ready to stream!"}), 200

def sse_message(data):
    """Helper to format SSE messages."""
    return f"data: {json.dumps(data)}\n\n"

@app.route('/search_and_prepare_song_sse')
def search_and_prepare_song_sse():
    search_query = request.args.get('query')
    if not search_query:
        return Response(sse_message({"status": "error", "message": "Search query is required"}), mimetype='text/event-stream')

    logger.info(f"SSE: Preparing song for query: \"{search_query}\"")

    @stream_with_context
    def generate_events():
        try:
            yield sse_message({"status": "searching", "message": f"Searching for \"{search_query}\"..."})
            search_results = ytmusic.search(search_query, filter='songs')
            if not search_results:
                yield sse_message({"status": "error", "message": f"No songs found for \"{search_query}\""})
                return

            video_id = search_results[0].get('videoId')
            if not video_id:
                yield sse_message({"status": "error", "message": "Could not get video ID."})
                return

            yield sse_message({"status": "found_initial", "message": "Match found. Fetching details..."})
            
            # Use yt-dlp to get metadata. This is much more reliable than calling a subprocess.
            with yt_dlp.YoutubeDL(get_ydl_opts({'dump_single_json': True, 'extract_flat': 'in_playlist'})) as ydl:
                info = ydl.extract_info(f'https://music.youtube.com/watch?v={video_id}', download=False)
                
            song_details = {
                "title": info.get('title', 'Unknown Title'),
                "artist": info.get('artist') or info.get('channel') or 'Unknown Artist',
                "video_id": video_id,
                "duration_seconds": info.get('duration', 0),
                "thumbnail_url": info.get('thumbnails', [{}])[-1].get('url', ''),
                "original_query": search_query
            }
            
            yield sse_message({
                "status": "ready_to_stream",
                "message": f"Ready: {song_details['title']}",
                "song_details": song_details,
                "video_id": video_id
            })

        except yt_dlp.utils.DownloadError as de:
            logger.error(f"SSE: yt-dlp DownloadError during prep for \"{search_query}\": {de}", exc_info=True)
            yield sse_message({"status": "error", "message": f"Download error: {str(de)}"})
        except Exception as e:
            logger.error(f"SSE: Unexpected error in generate_events for \"{search_query}\": {e}", exc_info=True)
            yield sse_message({"status": "error", "message": "An unexpected server error occurred."})

    return Response(generate_events(), mimetype='text/event-stream')


@app.route('/stream_audio/<video_id>')
def stream_audio(video_id):
    if not video_id:
        return jsonify({"error": "Video ID is required"}), 400

    logger.info(f"STREAM: Request for video_id: {video_id}")
    
    try:
        # Step 1: Get the direct audio stream URL using yt-dlp's Python library
        with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
            info = ydl.extract_info(f'https://music.youtube.com/watch?v={video_id}', download=False)
            # Find the best audio-only format
            best_audio_format = next((f for f in info['formats'][::-1] if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), None)
            if not best_audio_format:
                best_audio_format = info['formats'][-1] # Fallback to the last format if no ideal one found
            
            direct_audio_url = best_audio_format['url']

        logger.info(f"STREAM: Obtained direct audio URL for {video_id}.")

        # Step 2: Use ffmpeg to stream this URL in chunks
        ffmpeg_cmd = [
            'ffmpeg',
            '-i', direct_audio_url,
            '-nostats', '-hide_banner',
            '-loglevel', 'error',
            '-vn',                  # No video
            '-c:a', 'copy',         # Copy codec to avoid transcoding (fast)
            '-movflags', 'frag_keyframe+empty_moov', 
            '-f', 'webm',           # Output WebM container, compatible with Opus/Vorbis
            'pipe:1'                # Output to stdout
        ]
        
        logger.info(f"STREAM: Starting ffmpeg process for {video_id}")
        ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        @stream_with_context
        def generate_audio_chunks():
            try:
                while True:
                    chunk = ffmpeg_process.stdout.read(8192) # Read in 8KB chunks
                    if not chunk:
                        logger.info(f"STREAM: ffmpeg stdout finished for {video_id}.")
                        break
                    yield chunk
            except Exception as e:
                logger.error(f"STREAM: Error during chunk generation for {video_id}: {e}")
            finally:
                logger.info(f"STREAM: Cleaning up ffmpeg process for {video_id}")
                if ffmpeg_process.poll() is None:
                    ffmpeg_process.terminate()
                    ffmpeg_process.wait()
                stderr_output = ffmpeg_process.stderr.read().decode(errors='ignore').strip()
                if stderr_output:
                    logger.warning(f"STREAM: ffmpeg stderr for {video_id}: {stderr_output}")

        return Response(generate_audio_chunks(), mimetype='audio/webm')

    except Exception as e:
        logger.error(f"STREAM: Fatal error setting up stream for {video_id}: {e}", exc_info=True)
        return Response("Error setting up stream", status=500, mimetype='text/plain')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
