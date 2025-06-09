from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp
import os
import logging
import json
import subprocess

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# --- YouTube Music API Setup ---
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}", exc_info=True)

COOKIES_FILE_PATH = 'cookies.txt'
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

# --- Helper for yt-dlp options ---
def get_ydl_opts(extra_opts=None):
    opts = {
        'quiet': True,
        'no_warnings': True,
    }
    if os.path.exists(absolute_cookies_path):
        opts['cookies'] = absolute_cookies_path
        logger.info(f"Using cookies file: {absolute_cookies_path}")
    else:
        logger.warning(f"Cookies file not found at {absolute_cookies_path}.")
    if extra_opts:
        opts.update(extra_opts)
    return opts

def _build_yt_dlp_command(base_command_list, opts_dict):
    cmd = list(base_command_list)
    for k, v in opts_dict.items():
        if k == 'cookies' and not os.path.exists(v):
            logger.warning(f"Skipping non-existent cookies file for yt-dlp command: {v}")
            continue
        # This logic correctly handles flags like --no-playlist
        if isinstance(v, bool) and v:
            cmd.append(f'--{k.replace("_", "-")}')
        elif isinstance(v, (str, int, float)):
            cmd.append(f'--{k.replace("_", "-")}')
            cmd.append(str(v))
    return cmd

@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Backend is running and ready!"}), 200

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
            
            search_results = ytmusic.search(search_query, filter='songs')
            if not search_results:
                logger.warning(f"SSE: No songs found via YTMusic API for \"{search_query}\"")
                yield sse_message({"status": "error", "message": f"No songs found for \"{search_query}\""})
                return

            first_song_ytmusic = search_results[0]
            video_id = first_song_ytmusic['videoId']
            
            if not video_id:
                yield sse_message({"status": "error", "message": "Could not get video ID."})
                return

            yield sse_message({"status": "found_initial", "message": f"Match found. Fetching details..."})
            
            stream_url_for_info = f'https://music.youtube.com/watch?v={video_id}'
            # CORRECTED: Changed 'noplaylist' to 'no_playlist'
            ydl_info_opts = get_ydl_opts({
                'format': 'bestaudio/best', 
                'dump_json': True,
                'skip_download': True,
                'no_playlist': True,
            })
            
            yt_dlp_info_command = _build_yt_dlp_command(['yt-dlp'], ydl_info_opts)
            yt_dlp_info_command.append(stream_url_for_info)

            logger.info(f"SSE: Fetching metadata with yt-dlp for {video_id}: {' '.join(yt_dlp_info_command)}")
            process = subprocess.Popen(yt_dlp_info_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate(timeout=30)

            if process.returncode != 0:
                err_msg = stderr.decode(errors='ignore').strip()
                logger.error(f"SSE: yt-dlp metadata fetch error for {video_id} (Code {process.returncode}): {err_msg}")
                yield sse_message({"status": "error", "message": f"Error fetching details: {err_msg or 'Video unavailable.'}"})
                return
            
            metadata = json.loads(stdout)
            song_title = metadata.get('title', 'Unknown Title')
            song_artist = metadata.get('artist') or metadata.get('channel') or 'Unknown Artist'
            
            song_details = {
                "title": song_title,
                "artist": song_artist,
                "video_id": video_id,
                "duration_seconds": metadata.get('duration', 0),
                "thumbnail_url": metadata.get('thumbnails', [{}])[-1].get('url', ''),
                "original_query": search_query
            }
            
            yield sse_message({
                "status": "ready_to_stream", # Frontend will know it can now request the stream URL
                "message": f"Ready: {song_title}",
                "song_details": song_details,
                "video_id": video_id
            })

        except subprocess.TimeoutExpired:
            logger.error(f"SSE: yt-dlp metadata fetch timed out for \"{search_query}\"")
            yield sse_message({"status": "error", "message": "Fetching song details timed out."})
        except Exception as e:
            logger.error(f"SSE: Unexpected error in generate_events for \"{search_query}\": {e}", exc_info=True)
            yield sse_message({"status": "error", "message": f"An unexpected server error occurred."})

    return Response(generate_events(), mimetype='text/event-stream')


# NEW ARCHITECTURE: This endpoint is fast and lightweight.
# It replaces the old, slow, and complex /stream_audio endpoint.
@app.route('/get_stream_url/<video_id>')
def get_stream_url(video_id):
    if not video_id:
        return jsonify({"error": "Video ID is required"}), 400

    logger.info(f"URL_FETCH: Request for video_id: {video_id}")
    
    try:
        # CORRECTED: Changed 'noplaylist' to 'no_playlist'
        ydl_url_opts = get_ydl_opts({
            'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
            'get_url': True,
            'no_playlist': True,
        })
        
        yt_dlp_get_url_command = _build_yt_dlp_command(['yt-dlp'], ydl_url_opts)
        yt_dlp_get_url_command.append(f'https://music.youtube.com/watch?v={video_id}')
        
        logger.info(f"URL_FETCH: yt-dlp get URL command: {' '.join(yt_dlp_get_url_command)}")
        
        process = subprocess.Popen(yt_dlp_get_url_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_url, stderr_url = process.communicate(timeout=20)

        if process.returncode != 0:
            error_message = stderr_url.decode(errors='ignore').strip()
            logger.error(f"URL_FETCH: yt-dlp failed to get URL for {video_id}. Error: {error_message}")
            return jsonify({"error": f"Could not get stream URL: {error_message}"}), 502

        direct_audio_url = stdout_url.decode().strip()
        if not direct_audio_url.startswith('http'):
            logger.error(f"URL_FETCH: yt-dlp returned invalid URL for {video_id}")
            return jsonify({"error": "Upstream service returned an invalid stream URL."}), 502
            
        logger.info(f"URL_FETCH: Success. Returning direct URL for {video_id}.")
        return jsonify({"status": "success", "stream_url": direct_audio_url})

    except subprocess.TimeoutExpired:
        logger.error(f"URL_FETCH: yt-dlp get URL timed out for {video_id}")
        return jsonify({"error": "Request for stream URL timed out."}), 504
    except Exception as e:
        logger.error(f"URL_FETCH: Exception getting URL for {video_id}: {e}", exc_info=True)
        return jsonify({"error": "A server error occurred while fetching the stream URL."}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
