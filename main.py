import flask
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp # Import at the top
import os
import uuid
import logging
import time
import shutil # For shutil.which

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# --- YouTube Music API Setup ---
# Consider using cookies with YTMusic if searches also become problematic:
# YTMusic(absolute_cookies_path if os.path.exists(absolute_cookies_path) else None)
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}", exc_info=True)

# --- yt-dlp Configuration ---
TEMP_AUDIO_DIR_NAME = 'temp_audio'
TEMP_AUDIO_DIR = os.path.abspath(TEMP_AUDIO_DIR_NAME)

if not os.path.exists(TEMP_AUDIO_DIR):
    try:
        os.makedirs(TEMP_AUDIO_DIR)
        logger.info(f"Created temporary audio directory: {TEMP_AUDIO_DIR}")
    except Exception as e:
        logger.error(f"Error creating temporary audio directory {TEMP_AUDIO_DIR}: {e}", exc_info=True)

COOKIES_FILE_PATH = 'cookies.txt'
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

YDL_OPTS_BASE = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': False,
    'no_warnings': False,
    'paths': {'home': TEMP_AUDIO_DIR},
    'verbose': True,
    'nocheckcertificate': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0',
        'Accept-Language': 'en-US,en;q=0.9',
    },
    'extractor_args': {
        'youtube': {
            'player_client': ['web', 'android', 'ios'],
        }
    },
}

if os.path.exists(absolute_cookies_path):
    logger.info(f"Cookies file found at {absolute_cookies_path}. Adding to yt-dlp base options.")
    YDL_OPTS_BASE['cookiefile'] = absolute_cookies_path
else:
    logger.warning(
        f"Cookies file not found at {absolute_cookies_path}. "
        f"yt-dlp will run without cookies. This may lead to 'Sign in to confirm' or availability errors."
    )

# --- Startup Diagnostics ---
def run_startup_diagnostics():
    logger.info("--- Running yt-dlp Startup Diagnostics ---")
    
    # 1. Check yt-dlp version
    try:
        logger.info(f"Detected yt-dlp version: {yt_dlp.version.__version__}")
        if "2025" in yt_dlp.version.__version__: # Check for the unusual version string
            logger.warning(f"UNUSUAL yt-dlp version detected: {yt_dlp.version.__version__}. Please ensure this is intended and up-to-date from official sources.")
    except Exception as e:
        logger.error(f"DIAGNOSTIC FAILED: Could not get yt-dlp version: {e}", exc_info=True)
        logger.info("--- yt-dlp Startup Diagnostics Complete (with errors) ---")
        return

    # 2. Test basic info extraction for a known public video
    # Using yt-dlp's own test video ID 'BaW_jenozKc' (short, public, stable)
    test_video_url = 'https://www.youtube.com/watch?v=zGDzdps75ns'
    test_video_id = 'zGDzdps75ns'
    logger.info(f"Attempting to extract info for test video: {test_video_url}")
    
    diag_ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'verbose': False, # Keep diagnostic logs tidy
        'nocheckcertificate': YDL_OPTS_BASE.get('nocheckcertificate', True),
        'http_headers': YDL_OPTS_BASE.get('http_headers', {}), # Use same headers as main app
        'extractor_args': YDL_OPTS_BASE.get('extractor_args', {}), # Use same extractor args
    }
    # Include cookies in diagnostic test if they are configured and exist
    if 'cookiefile' in YDL_OPTS_BASE and os.path.exists(YDL_OPTS_BASE['cookiefile']):
        diag_ydl_opts['cookiefile'] = YDL_OPTS_BASE['cookiefile']
        logger.info("Diagnostic info extraction will attempt to use cookies.")
    else:
        logger.info("Diagnostic info extraction will not use cookies.")

    try:
        with yt_dlp.YoutubeDL(diag_ydl_opts) as ydl:
            logger.info(f"Diagnostic yt-dlp effective options: {ydl.params}")
            info = ydl.extract_info(test_video_url, download=False)
            if info and info.get('id') == test_video_id:
                logger.info(f"DIAGNOSTIC PASSED: Successfully extracted info for '{info.get('title', 'N/A')}' (ID: {info.get('id')}).")
            else:
                logger.error(f"DIAGNOSTIC FAILED: Info extraction for test video {test_video_id} did not return expected data. Received info: {info}")
    except yt_dlp.utils.DownloadError as de:
        if "Video unavailable" in str(de):
            logger.error(f"DIAGNOSTIC FAILED (CRITICAL): Test video {test_video_id} reported as UNAVAILABLE. This indicates a significant problem with YouTube access from this server/IP. Error: {de}", exc_info=True)
        else:
            logger.error(f"DIAGNOSTIC FAILED: yt-dlp DownloadError during info extraction for test video {test_video_id}: {de}", exc_info=True)
    except Exception as e:
        logger.error(f"DIAGNOSTIC FAILED: Unexpected error during info extraction for test video {test_video_id}: {e}", exc_info=True)

    # 3. Check for ffmpeg and ffprobe
    logger.info("Checking for ffmpeg and ffprobe in system PATH...")
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    if ffmpeg_path:
        logger.info(f"DIAGNOSTIC INFO: ffmpeg found at: {ffmpeg_path}")
    else:
        logger.warning("DIAGNOSTIC WARNING: ffmpeg NOT found in system PATH. Audio conversion and some formats might not be available.")
    
    if ffprobe_path:
        logger.info(f"DIAGNOSTIC INFO: ffprobe found at: {ffprobe_path}")
    else:
        logger.warning("DIAGNOSTIC WARNING: ffprobe NOT found in system PATH. Some functionalities might be limited.")

    logger.info("--- yt-dlp Startup Diagnostics Complete ---")

# Run diagnostics when the module is loaded by Python.
# This happens for each Gunicorn worker and also when run directly via `python main.py`.
run_startup_diagnostics()

# --- Flask Routes ---
@app.route('/')
def health_check():
    # You can also add a more detailed health check here, perhaps returning status of yt-dlp from diagnostics
    return jsonify({"status": "ok", "message": "Backend is running!"}), 200

@app.route('/search_and_download', methods=['GET'])
def search_and_download():
    search_query = request.args.get('query')
    if not search_query:
        logger.warning("Search query missing.")
        return jsonify({"error": "Search query is required"}), 400

    logger.info(f"Received search query: {search_query}")

    try:
        logger.info(f"Searching for: '{search_query}' using YTMusic API")
        search_results = ytmusic.search(search_query, filter='songs', limit=5)

        if not search_results:
            logger.warning(f"No songs found for query: '{search_query}' via YTMusic API")
            return jsonify({"error": "No songs found for your query"}), 404

        downloaded_successfully = False
        last_download_error_message = "No suitable video found or all download attempts failed."
        response_file_path = None
        final_song_title = "Unknown Title"
        final_song_artist = "Unknown Artist"
        final_downloaded_ext = "unknown"

        for i, song_data in enumerate(search_results):
            video_id = song_data.get('videoId')
            current_song_title = song_data.get('title', f'Unknown Title {i+1}')
            artist_info = song_data.get('artists', [{'name': f'Unknown Artist {i+1}'}])
            current_song_artist = artist_info[0]['name'] if artist_info else f'Unknown Artist {i+1}'

            logger.info(f"Attempt {i+1}/{len(search_results)}: Trying '{current_song_title}' by '{current_song_artist}' (ID: {video_id})")

            if not video_id:
                logger.warning(f"Skipping result {i+1} due to missing videoId.")
                last_download_error_message = "A search result was missing a video ID."
                continue

            ydl_opts_specific = YDL_OPTS_BASE.copy()
            ydl_opts_specific['outtmpl'] = {
                'default': f'{video_id}.%(ext)s',
                'chapter': '%(title)s - %(section_number)03d %(section_title)s [%(id)s].%(ext)s'
            }
            if 'cookiefile' in ydl_opts_specific and not os.path.exists(ydl_opts_specific['cookiefile']):
                 logger.warning(f"Cookie file {ydl_opts_specific['cookiefile']} not found for attempt {i+1}. Removing from options.")
                 del ydl_opts_specific['cookiefile']

            for f_cleanup in os.listdir(TEMP_AUDIO_DIR):
                if f_cleanup.startswith(video_id + "."):
                    try:
                        os.remove(os.path.join(TEMP_AUDIO_DIR, f_cleanup))
                        logger.info(f"Removed pre-existing/partial file: {f_cleanup}")
                    except Exception as e_clean:
                        logger.warning(f"Could not remove pre-existing file {f_cleanup}: {e_clean}")
            
            try:
                with yt_dlp.YoutubeDL(ydl_opts_specific) as ydl:
                    download_url = f'https://www.youtube.com/watch?v={video_id}'
                    logger.info(f"Starting download for video ID: {video_id} from {download_url} with effective options: {ydl.params}")
                    
                    ydl.download([download_url])
                    logger.info(f"Download process reported as complete for video ID: {video_id}.")

                downloaded_file_name = None
                for f_in_dir in os.listdir(TEMP_AUDIO_DIR):
                    if f_in_dir.startswith(video_id + "."):
                        downloaded_file_name = f_in_dir
                        break
                
                if not downloaded_file_name:
                    logger.error(f"Audio file NOT FOUND after download attempt for video ID: {video_id} (expected pattern: {video_id}.*).")
                    logger.info(f"Contents of {TEMP_AUDIO_DIR}: {os.listdir(TEMP_AUDIO_DIR)}")
                    last_download_error_message = f"Audio file not found post-download for {video_id}."
                    continue

                actual_downloaded_path = os.path.join(TEMP_AUDIO_DIR, downloaded_file_name)
                final_downloaded_ext = downloaded_file_name.split('.')[-1]
                
                unique_temp_filename_base = str(uuid.uuid4())
                response_file_path = os.path.join(TEMP_AUDIO_DIR, f"{unique_temp_filename_base}.{final_downloaded_ext}")
                os.rename(actual_downloaded_path, response_file_path)
                logger.info(f"Renamed downloaded audio from {actual_downloaded_path} to {response_file_path}")

                final_song_title = current_song_title
                final_song_artist = current_song_artist
                downloaded_successfully = True
                break

            except yt_dlp.utils.DownloadError as de_inner:
                logger.warning(f"yt-dlp DownloadError for '{current_song_title}' (ID: {video_id}): {de_inner}")
                last_download_error_message = str(de_inner)
                continue
            except Exception as e_inner:
                logger.error(f"Unexpected error during download attempt for '{current_song_title}' (ID: {video_id}): {e_inner}", exc_info=True)
                last_download_error_message = str(e_inner)
                continue

        if not downloaded_successfully:
            logger.error(f"All download attempts failed for query '{search_query}'. Last error: {last_download_error_message}")
            error_to_report = f"Failed to download audio after trying {len(search_results)} result(s). Last error: {last_download_error_message}. This may be due to regional restrictions or server IP issues with YouTube."
            return jsonify({"error": error_to_report}), 500

        logger.info(f"Sending file: {response_file_path} as '{final_song_artist} - {final_song_title}.{final_downloaded_ext}'")
        
        mimetype_map = {
            'mp3': 'audio/mpeg', 'm4a': 'audio/mp4', 'webm': 'audio/webm',
            'opus': 'audio/opus', 'ogg': 'audio/ogg',
        }
        mimetype = mimetype_map.get(final_downloaded_ext.lower(), 'application/octet-stream')

        response = send_file(
            response_file_path,
            as_attachment=True,
            download_name=f"{final_song_artist} - {final_song_title}.{final_downloaded_ext}",
            mimetype=mimetype
        )

        @response.call_on_close
        def cleanup_file():
            try:
                if os.path.exists(response_file_path):
                    os.remove(response_file_path)
                    logger.info(f"Cleaned up temporary file: {response_file_path}")
            except Exception as e_cleanup:
                logger.error(f"Error cleaning up file {response_file_path}: {e_cleanup}", exc_info=True)
        
        return response

    except Exception as e_outer:
        logger.error(f"An unexpected error occurred in search_and_download: {e_outer}", exc_info=True)
        return jsonify({"error": f"An internal server error occurred: {str(e_outer)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
