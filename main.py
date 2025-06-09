from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp # For metadata and getting stream URL
import os
import logging
import time
import json
import subprocess

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}) # Allow all for development, restrict in production

# --- YouTube Music API Setup ---
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}", exc_info=True)
    # Potentially exit or disable music features if YTMusic is critical
    # raise SystemExit(f"Could not initialize YTMusic: {e}") from e

COOKIES_FILE_PATH = 'cookies.txt'
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

# --- Helper for yt-dlp options ---
def get_ydl_opts(extra_opts=None):
    opts = {
        'quiet': False, # Set to False initially for more verbose yt-dlp output for debugging
        'no_warnings': False,
        # 'verbose': True, # Uncomment for maximum debugging from yt-dlp
    }
    if os.path.exists(absolute_cookies_path):
        # CORRECTED: The key for yt-dlp is 'cookies', not 'cookiefile'.
        opts['cookies'] = absolute_cookies_path
        logger.info(f"Using cookies file: {absolute_cookies_path}")
    else:
        logger.warning(f"Cookies file not found at {absolute_cookies_path}. Download quality/availability may be affected.")
    if extra_opts:
        opts.update(extra_opts)
    return opts

def _build_yt_dlp_command(base_command_list, opts_dict):
    cmd = list(base_command_list) # Start with base like ['yt-dlp']
    for k, v in opts_dict.items():
        # CORRECTED: The argument is '--cookies', which this check handles correctly now
        if k == 'cookies' and not os.path.exists(v): # Skip cookiefile if it doesn't exist
            logger.warning(f"Skipping non-existent cookies file for yt-dlp command: {v}")
            continue
        if isinstance(v, bool):
            if v: # True for flags like --quiet, --get-url
                if k in ['get-url', 'dump-json', 'skip-download', 'quiet', 'no-warnings', 'verbose', 'noplaylist']: # Common flags
                     cmd.append(f'--{k.replace("_", "-")}')
                else: # Less common boolean flags, assume they are just flags
                     cmd.append(f'--{k.replace("_", "-")}')
        elif isinstance(v, (str, int, float)): # For options like --format <value>, --output <value>
            cmd.append(f'--{k.replace("_", "-")}')
            cmd.append(str(v))
        elif isinstance(v, list): # For options that can be repeated or take list
            for item in v:
                cmd.append(f'--{k.replace("_", "-")}')
                cmd.append(str(item))
    return cmd


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
        def error_gen():
            yield sse_message({"status": "error", "message": "Search query is required"})
        return Response(error_gen(), mimetype='text/event-stream')

    logger.info(f"SSE: Preparing song for query: \"{search_query}\"")

    def generate_events():
        try:
            yield sse_message({"status": "searching", "message": f"Searching for \"{search_query}\"..."})
            
            search_results = ytmusic.search(search_query, filter='songs') # Can add more types if needed
            if not search_results:
                logger.warning(f"SSE: No songs found via YTMusic API for \"{search_query}\"")
                yield sse_message({"status": "error", "message": f"No songs found for \"{search_query}\""})
                return

            first_song_ytmusic = search_results[0]
            video_id = first_song_ytmusic['videoId']
            
            if not video_id:
                logger.error(f"SSE: YTMusic API found a result but no videoId for \"{search_query}\"")
                yield sse_message({"status": "error", "message": "Could not get video ID for the song."})
                return

            yield sse_message({"status": "found_initial", "message": f"Initial match: {first_song_ytmusic.get('title', 'Unknown Title')}. Fetching details..."})

            # Use yt-dlp to get more accurate metadata and confirm availability
            stream_url_for_info = f'https://music.youtube.com/watch?v={video_id}'
            ydl_info_opts = get_ydl_opts({
                'format': 'bestaudio/best', 
                'dump_json': True,      # Corrected from dumpjson
                'skip_download': True,
                'noplaylist': True,
            })
            
            # Build command carefully
            yt_dlp_info_command = _build_yt_dlp_command(['yt-dlp'], ydl_info_opts)
            yt_dlp_info_command.append(stream_url_for_info)

            logger.info(f"SSE: Fetching metadata with yt-dlp for {video_id}: {' '.join(yt_dlp_info_command)}")
            process = subprocess.Popen(yt_dlp_info_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate(timeout=30) # Added timeout

            if process.returncode != 0:
                err_msg = stderr.decode(errors='ignore').strip()
                logger.error(f"SSE: yt-dlp metadata fetch error for {video_id} (Code {process.returncode}): {err_msg}")
                yield sse_message({"status": "error", "message": f"Error fetching song details: {err_msg or 'Video unavailable or restricted.'}"})
                return
            
            try:
                metadata = json.loads(stdout)
            except json.JSONDecodeError as je:
                logger.error(f"SSE: Failed to parse yt-dlp JSON output for {video_id}: {je}\nOutput: {stdout[:500]}")
                yield sse_message({"status": "error", "message": "Error processing song details (JSON parse failed)."})
                return

            # Prioritize metadata from yt-dlp
            song_title = metadata.get('title', first_song_ytmusic.get('title', 'Unknown Title'))
            song_artist = metadata.get('artist') or metadata.get('channel') or metadata.get('uploader', 
                            (first_song_ytmusic.get('artists', [{'name': 'Unknown Artist'}])[0]['name'] 
                             if first_song_ytmusic.get('artists') else 'Unknown Artist'))
            duration_seconds = metadata.get('duration', first_song_ytmusic.get('duration_seconds', 0))
            
            thumbnails_yt_dlp = metadata.get('thumbnails', [])
            thumbnail_url = thumbnails_yt_dlp[-1]['url'] if thumbnails_yt_dlp else \
                            (first_song_ytmusic.get('thumbnails', [{}])[0].get('url', ''))


            song_details = {
                "title": song_title,
                "artist": song_artist,
                "video_id": video_id, # This is the key for streaming
                "duration_seconds": duration_seconds,
                "thumbnail_url": thumbnail_url,
                "original_query": search_query # Important for frontend matching, esp. for prefetch
            }
            
            yield sse_message({
                "status": "found_detailed", # New status
                "message": f"Details acquired: {song_title} by {song_artist}",
                "song_details": song_details
            })
            
            yield sse_message({
                "status": "ready_to_stream",
                "message": f"Ready to stream: {song_title}",
                "song_details": song_details,
                # The video_id is now inside song_details, but it's good practice to also send it top-level
                # for older frontend logic if needed. Your current JS reads it from song_details.
                "video_id": video_id 
            })

        except subprocess.TimeoutExpired:
            logger.error(f"SSE: yt-dlp metadata fetch timed out for \"{search_query}\"")
            yield sse_message({"status": "error", "message": "Fetching song details timed out."})
        except yt_dlp.utils.DownloadError as de:
            logger.error(f"SSE: yt-dlp DownloadError during prep for \"{search_query}\": {de}", exc_info=True)
            yield sse_message({"status": "error", "message": f"Download error preparing song: {str(de)}"})
        except Exception as e:
            logger.error(f"SSE: Unexpected error in generate_events for \"{search_query}\": {e}", exc_info=True)
            yield sse_message({"status": "error", "message": f"An unexpected server error occurred preparing the song."})

    return Response(generate_events(), mimetype='text/event-stream')


@app.route('/stream_audio/<video_id>')
def stream_audio(video_id):
    if not video_id:
        return jsonify({"error": "Video ID is required"}), 400

    start_time_str = request.args.get('startTime', '0')
    try:
        start_time = float(start_time_str)
        if start_time < 0: start_time = 0
    except ValueError:
        start_time = 0

    logger.info(f"STREAM: Request for video_id: {video_id}, startTime: {start_time:.2f}s")

    # Step 1: Get the direct audio stream URL using yt-dlp
    ydl_url_opts = get_ydl_opts({
        'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio',
        'get_url': True, # Corrected: underscore for internal key, becomes --get-url
        'noplaylist': True,
    })
    
    yt_dlp_get_url_command = _build_yt_dlp_command(['yt-dlp'], ydl_url_opts)
    yt_dlp_get_url_command.append(f'https://music.youtube.com/watch?v={video_id}')
    
    logger.info(f"STREAM: yt-dlp get URL command: {' '.join(yt_dlp_get_url_command)}")
    
    try:
        direct_stream_url_process = subprocess.Popen(yt_dlp_get_url_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_url, stderr_url = direct_stream_url_process.communicate(timeout=20) # Timeout for getting URL

        if direct_stream_url_process.returncode != 0:
            error_message = stderr_url.decode(errors='ignore').strip()
            logger.error(f"STREAM: yt-dlp failed to get stream URL for {video_id}. Code: {direct_stream_url_process.returncode}. Error: {error_message}")
            # Consider returning a 5xx error with a JSON body for the client to handle
            return Response(f"Error: Could not get stream URL. yt-dlp: {error_message}", status=502, mimetype='text/plain')


        direct_audio_url = stdout_url.decode().strip()
        if not direct_audio_url.startswith(('http://', 'https://')):
            logger.error(f"STREAM: yt-dlp returned invalid URL for {video_id}: '{direct_audio_url[:200]}'") # Log more of the URL
            return Response("Error: yt-dlp returned an invalid stream URL.", status=502, mimetype='text/plain')
            
        logger.info(f"STREAM: Obtained direct audio URL for {video_id} (first 100 chars): {direct_audio_url[:100]}...")
    except subprocess.TimeoutExpired:
        logger.error(f"STREAM: yt-dlp get URL timed out for {video_id}")
        return Response("Error: Getting stream URL timed out.", status=504, mimetype='text/plain')
    except Exception as e:
        logger.error(f"STREAM: Exception getting URL for {video_id}: {e}", exc_info=True)
        return Response(f"Error: Server issue getting stream URL. {e}", status=500, mimetype='text/plain')

    # Step 2: Use ffmpeg to stream this URL
    ffmpeg_cmd = [
        'ffmpeg',
        '-nostats', '-hide_banner',
        '-loglevel', 'warning', # 'error' or 'warning' for less verbosity in logs
    ]
    if start_time > 0.1: # Add seek only if significant to avoid issues with 0
        ffmpeg_cmd.extend(['-ss', str(start_time)]) 
    
    ffmpeg_cmd.extend([
        '-i', direct_audio_url,
        '-vn',                  # No video
        '-c:a', 'copy',         # Attempt to copy codec (fastest if compatible)
        '-movflags', 'frag_keyframe+empty_moov', # For fMP4-like behavior if outputting to MP4-ish
        '-f', 'webm',           # Output WebM (good for Opus, which bestaudio often is)
                                # Browsers handle 'audio/webm' well.
        'pipe:1'                # Output to stdout
    ])

    logger.info(f"STREAM: ffmpeg command: {' '.join(ffmpeg_cmd)}")
    
    try:
        # bufsize=-1 for system default (often fully buffered).
        # Smaller bufsize like 8192 might send initial data faster but has more overhead.
        ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=8192)
    except FileNotFoundError:
        logger.critical("STREAM: ffmpeg command not found. Ensure ffmpeg is installed and in PATH.")
        return Response("Error: ffmpeg not found on server.", status=503, mimetype='text/plain')
    except Exception as e:
        logger.error(f"STREAM: Failed to start ffmpeg process for {video_id}: {e}", exc_info=True)
        return Response(f"Error: Could not start streaming process. {e}", status=500, mimetype='text/plain')


    @stream_with_context
    def generate_audio_chunks():
        # This generator is crucial. It runs in a separate context,
        # allowing the main Flask thread to handle other requests.
        # The `finally` block ensures cleanup even if the client disconnects.
        try:
            logger.info(f"STREAM: ffmpeg - Starting to yield chunks for {video_id} from {start_time:.2f}s")
            for chunk in iter(lambda: ffmpeg_process.stdout.read(8192), b''):
                yield chunk
            logger.info(f"STREAM: ffmpeg - Finished yielding chunks for {video_id}.")
        except BrokenPipeError: # Client disconnected
            logger.warning(f"STREAM: ffmpeg - BrokenPipeError for {video_id}. Client likely disconnected.")
        except Exception as gen_exc:
            logger.error(f"STREAM: ffmpeg - Error during audio chunk generation for {video_id}: {gen_exc}", exc_info=True)
        finally:
            logger.info(f"STREAM: ffmpeg - Cleaning up process for {video_id}")
            if ffmpeg_process.stdout:
                ffmpeg_process.stdout.close()
            if ffmpeg_process.stderr:
                stderr_output = ffmpeg_process.stderr.read().decode(errors='ignore').strip()
                if stderr_output:
                    logger.warning(f"STREAM: ffmpeg stderr for {video_id}: {stderr_output}")
                ffmpeg_process.stderr.close()
            
            if ffmpeg_process.poll() is None: # If process still running
                logger.warning(f"STREAM: ffmpeg - Process for {video_id} still running, attempting to terminate.")
                ffmpeg_process.terminate()
                try:
                    ffmpeg_process.wait(timeout=5) # Wait for termination
                except subprocess.TimeoutExpired:
                    logger.error(f"STREAM: ffmpeg - Process for {video_id} did not terminate gracefully. Killing.")
                    ffmpeg_process.kill()
            logger.info(f"STREAM: ffmpeg - Cleanup complete for {video_id}. Exit code: {ffmpeg_process.returncode}")

    # Mimetype should match the -f format in ffmpeg command
    return Response(generate_audio_chunks(), mimetype='audio/webm')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    # For production, use a proper WSGI server like Gunicorn or Uvicorn.
    # threaded=True helps Flask dev server handle concurrent requests like SSE and audio streams.
    # debug=False is recommended for stability with subprocesses in threaded mode.
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
