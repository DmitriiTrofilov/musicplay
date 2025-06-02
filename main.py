from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp
import os
import uuid
import logging
import time

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# --- MODIFICATION START ---
# Allow CORS from any origin for all routes
CORS(app, resources={r"/*": {"origins": "*"}})
# --- MODIFICATION END ---

# --- YouTube Music API Setup ---
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}")
    # Consider exiting or disabling functionality if YTMusic is critical

# --- yt-dlp Configuration for audio download ---

# Define the name of the temporary directory
TEMP_AUDIO_DIR_NAME = 'temp_audio'
# Create an absolute path for the temporary directory.
# Assumes this script is at the root of your project or CWD is project root.
TEMP_AUDIO_DIR = os.path.abspath(TEMP_AUDIO_DIR_NAME)

# Ensure the temporary directory exists
if not os.path.exists(TEMP_AUDIO_DIR):
    try:
        os.makedirs(TEMP_AUDIO_DIR)
        logger.info(f"Created temporary audio directory: {TEMP_AUDIO_DIR}")
    except Exception as e:
        logger.error(f"Error creating temporary audio directory {TEMP_AUDIO_DIR}: {e}")
        # This could be a critical error if the directory cannot be created.

COOKIES_FILE_PATH = 'cookies.txt'  # Assumes cookies.txt is in the root of your project
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

YDL_OPTS_BASE = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': False,
    'no_warnings': False,
    'paths': {'home': TEMP_AUDIO_DIR},  # All output files go into this directory
    'outtmpl': '%(id)s.%(ext)s',  # Filename template *within* paths.home.
    # yt-dlp will create e.g., TEMP_AUDIO_DIR/videoid.webm,
    # and the postprocessor will create TEMP_AUDIO_DIR/videoid.mp3.
    # 'ffmpeg_location': '/usr/bin/ffmpeg', # COMMENTED OUT: Let yt-dlp find ffmpeg in PATH.
    # This is usually more robust on Render/PaaS.
    # If FFmpeg errors occur, ensure it's installed and in PATH.
    'verbose': True,  # Add verbose logging from yt-dlp for debugging
}

if os.path.exists(absolute_cookies_path):
    logger.info(f"Cookies file found at {absolute_cookies_path}. Adding to yt-dlp options.")
    YDL_OPTS_BASE['cookiefile'] = absolute_cookies_path
else:
    logger.warning(
        f"Cookies file not found at {absolute_cookies_path}. "
        f"yt-dlp will run without cookies. This may lead to 'Sign in to confirm' errors."
    )


@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Backend is running!"}), 200


@app.route('/search_and_download', methods=['GET'])
def search_and_download():
    search_query = request.args.get('query')
    if not search_query:
        logger.warning("Search query missing.")
        return jsonify({"error": "Search query is required"}), 400

    logger.info(f"Received search query: {search_query}")

    try:
        logger.info(f"Searching for: {search_query}")
        search_results = ytmusic.search(search_query, filter='songs')

        if not search_results:
            logger.warning(f"No songs found for query: {search_query}")
            return jsonify({"error": "No songs found for your query"}), 404

        first_song = search_results[0]
        video_id = first_song['videoId']
        song_title = first_song.get('title', 'Unknown Title')
        song_artist = first_song.get('artists', [{'name': 'Unknown Artist'}])[0]['name']
        logger.info(f"Found song: {song_title} by {song_artist} (ID: {video_id})")

        if not video_id:
            logger.error("Could not get video ID for the song.")
            return jsonify({"error": "Could not get video ID for the song"}), 500

        unique_audio_filename = f"{str(uuid.uuid4())}"
        final_audio_path = os.path.join(TEMP_AUDIO_DIR, unique_audio_filename)

        # This is the path where yt-dlp will place the downloaded audio.
        expected_downloaded_audio_path = os.path.join(TEMP_AUDIO_DIR, f"{video_id}")
        logger.info(f"Expecting audio at: {expected_downloaded_audio_path}")

        # Clean up any pre-existing file with the same video_id or unique name from a failed previous run
        for path_to_clean in [expected_downloaded_audio_path, final_audio_path]:
            if os.path.exists(path_to_clean):
                try:
                    os.remove(path_to_clean)
                    logger.info(f"Removed pre-existing file: {path_to_clean}")
                except Exception as e:
                    logger.warning(f"Could not remove pre-existing file {path_to_clean}: {e}")

        # Use a fresh copy of YDL_OPTS_BASE for this download instance
        ydl_opts_specific = YDL_OPTS_BASE.copy()

        with yt_dlp.YoutubeDL(ydl_opts_specific) as ydl:
            logger.info(f"Starting download for video ID: {video_id} with options: {ydl_opts_specific}")
            download_url = f'https://music.youtube.com/watch?v={video_id}'
            ydl.download([download_url])
            # The log from yt-dlp itself (with verbose:True) should now show the correct destination path.
            logger.info(f"Download process reported as complete for video ID: {video_id}.")

        # Determine the actual downloaded file extension
        downloaded_file_ext = None
        for filename in os.listdir(TEMP_AUDIO_DIR):
            if filename.startswith(video_id):
                downloaded_file_ext = filename[len(video_id) + 1:]  # +1 for the dot
                break

        if not downloaded_file_ext:
            logger.error(f"Audio file NOT FOUND after download for video ID: {video_id}")
            logger.info(f"Contents of {TEMP_AUDIO_DIR}: {os.listdir(TEMP_AUDIO_DIR)}")  # List dir contents
            return jsonify({"error": "Critical error: Audio file not found after download. Check logs."}), 500

        expected_downloaded_audio_path = f"{expected_downloaded_audio_path}.{downloaded_file_ext}"
        final_audio_path = f"{final_audio_path}.{downloaded_file_ext}"

        if not os.path.exists(expected_downloaded_audio_path):
            logger.error(f"Downloaded audio file NOT FOUND at expected path: {expected_downloaded_audio_path}")
            logger.info(f"Contents of {TEMP_AUDIO_DIR}: {os.listdir(TEMP_AUDIO_DIR)}")  # List dir contents
            return jsonify({"error": "Critical error: Downloaded audio file not found. Check logs."}), 500

        # Rename the downloaded audio file to a unique name before sending
        os.rename(expected_downloaded_audio_path, final_audio_path)
        logger.info(f"Renamed downloaded audio from {expected_downloaded_audio_path} to {final_audio_path}")

        logger.info(f"Sending file: {final_audio_path}")
        response = send_file(
            final_audio_path,
            as_attachment=True,
            download_name=f"{song_artist} - {song_title}.{downloaded_file_ext}",
            mimetype='audio/mpeg' if downloaded_file_ext == 'mp3' else 'audio/webm'  # Adjust mimetype as needed
        )

        @response.call_on_close
        def cleanup_file():
            try:
                if os.path.exists(final_audio_path):
                    os.remove(final_audio_path)
                    logger.info(f"Cleaned up temporary file: {final_audio_path}")
                else:
                    logger.warning(f"Cleanup: File not found to delete: {final_audio_path}")
            except Exception as e:
                logger.error(f"Error cleaning up file {final_audio_path}: {e}")

        return response

    except yt_dlp.utils.DownloadError as de:
        error_message = str(de)
        logger.error(f"yt-dlp DownloadError: {error_message}", exc_info=True)
        if "Sign in to confirm" in error_message:
            logger.error("Authentication error: Cookies might be invalid, expired, or not correctly applied.")
            return jsonify({"error": "Failed to download due to authentication issue. Cookies might be required or invalid."}), 500
        return jsonify({"error": f"Failed to download audio: {error_message}"}), 500
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        return jsonify({"error": f"An internal server error occurred: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
