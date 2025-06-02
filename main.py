from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp
import os
import uuid
import logging
import time # Added for retry delay

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app) # Enable CORS for all routes, or restrict to your local dev domain

# --- YouTube Music API Setup ---
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}")
    # Depending on the error, you might want to prevent the app from starting
    # or handle this gracefully in your routes.

# --- yt-dlp Configuration for MP3 download ---
TEMP_AUDIO_DIR = './temp_audio' # Define as a constant
# IMPORTANT: Ensure 'cookies.txt' is in the same directory as this script
# or provide the absolute path to your cookies.txt file.
# When deploying to Render, make sure cookies.txt is included in your deployment.
COOKIES_FILE_PATH = 'cookies.txt' # Assumes cookies.txt is in the root of your project

YDL_OPTS_BASE = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'noplaylist': True,
    'quiet': False,
    'no_warnings': False,
    'paths': {'home': TEMP_AUDIO_DIR},
    'outtmpl': os.path.join(TEMP_AUDIO_DIR, '%(id)s.%(ext)s'),
    'ffmpeg_location': '/usr/bin/ffmpeg' # Explicitly set if known.
                                          # On Render, ffmpeg should be in PATH via buildpack.
                                          # If it's not found, yt-dlp will error.
                                          # You can try removing this line if ffmpeg is in PATH.
}

# Conditionally add cookiefile option if cookies.txt exists
# This makes the path absolute, which is safer for yt-dlp
# Note: The working directory for a Flask app run by Gunicorn (common on Render)
# is typically the project root.
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)
if os.path.exists(absolute_cookies_path):
    logger.info(f"Cookies file found at {absolute_cookies_path}. Adding to yt-dlp options.")
    YDL_OPTS_BASE['cookiefile'] = absolute_cookies_path
else:
    logger.warning(
        f"Cookies file not found at {absolute_cookies_path}. "
        f"yt-dlp will run without cookies. This may lead to 'Sign in to confirm' errors "
        f"or issues with age-restricted/login-required content."
    )
    # You might want to raise an error here if cookies are essential for your use case
    # raise FileNotFoundError(f"Essential cookies.txt not found at {absolute_cookies_path}")


# Ensure the temporary directory exists
if not os.path.exists(TEMP_AUDIO_DIR):
    try:
        os.makedirs(TEMP_AUDIO_DIR)
        logger.info(f"Created temporary audio directory: {TEMP_AUDIO_DIR}")
    except Exception as e:
        logger.error(f"Error creating temporary audio directory {TEMP_AUDIO_DIR}: {e}")


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
        # 1. Search on YouTube Music
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

        # 2. Download the audio using yt-dlp
        unique_mp3_filename = f"{str(uuid.uuid4())}.mp3"
        final_mp3_path = os.path.join(TEMP_AUDIO_DIR, unique_mp3_filename)

        ydl_opts_specific = YDL_OPTS_BASE.copy()
        # No need to change outtmpl here if YDL_OPTS_BASE is already correct
        # The outtmpl will create video_id.mp3 (or video_id.original_ext then converted)

        expected_downloaded_mp3_path = os.path.join(TEMP_AUDIO_DIR, f"{video_id}.mp3")

        if os.path.exists(expected_downloaded_mp3_path):
            try:
                os.remove(expected_downloaded_mp3_path)
                logger.info(f"Removed pre-existing file: {expected_downloaded_mp3_path}")
            except Exception as e:
                logger.warning(f"Could not remove pre-existing file {expected_downloaded_mp3_path}: {e}")
        # Also clean potential unique file from a previous failed run, if any (less likely)
        if os.path.exists(final_mp3_path):
             try:
                os.remove(final_mp3_path)
                logger.info(f"Removed pre-existing unique-named file: {final_mp3_path}")
             except Exception as e:
                logger.warning(f"Could not remove pre-existing unique file {final_mp3_path}: {e}")


        with yt_dlp.YoutubeDL(ydl_opts_specific) as ydl:
            logger.info(f"Starting download for video ID: {video_id} using options: {ydl_opts_specific}")
            download_url = f'https://music.youtube.com/watch?v={video_id}'
            # For debugging, you can print the effective options yt-dlp is using
            # logger.debug(f"Effective yt-dlp options: {ydl.params}")
            ydl.download([download_url])
            logger.info(f"Download process completed for video ID: {video_id}. Expected at: {expected_downloaded_mp3_path}")


        if not os.path.exists(expected_downloaded_mp3_path):
            logger.error(f"MP3 file not found after download attempt: {expected_downloaded_mp3_path}")
            # Check for other files in temp dir
            files_in_temp = os.listdir(TEMP_AUDIO_DIR)
            logger.info(f"Files currently in {TEMP_AUDIO_DIR}: {files_in_temp}")
            possible_original_extensions = ['.webm', '.m4a', '.opus', '.ogg'] # Add .ogg
            found_original = None
            for ext in possible_original_extensions:
                original_file_path = os.path.join(TEMP_AUDIO_DIR, f"{video_id}{ext}")
                if os.path.exists(original_file_path):
                    logger.warning(f"Found original downloaded file: {original_file_path}. FFmpeg might have failed or is slow, or outtmpl didn't result in .mp3 directly.")
                    found_original = original_file_path
                    # Attempt to rename this to the expected mp3 path if it's the only candidate
                    # This is a bit of a hack; ideally, FFmpeg post-processor handles it.
                    if not os.path.exists(expected_downloaded_mp3_path) and found_original.endswith(('.mp3', '.MP3')): # if it's already mp3
                        try:
                            os.rename(found_original, expected_downloaded_mp3_path)
                            logger.info(f"Renamed {found_original} to {expected_downloaded_mp3_path}")
                        except Exception as rename_err:
                            logger.error(f"Could not rename {found_original} to {expected_downloaded_mp3_path}: {rename_err}")
                    break
            if not os.path.exists(expected_downloaded_mp3_path): # Check again after potential rename
                if found_original:
                    return jsonify({"error": f"Audio downloaded ({os.path.basename(found_original)}) but MP3 conversion might have failed or filename mismatch."}), 500
                return jsonify({"error": "Could not process the audio file (MP3 not found after download and conversion)."}), 500

        os.rename(expected_downloaded_mp3_path, final_mp3_path)
        logger.info(f"Renamed downloaded file to unique path: {final_mp3_path}")

        logger.info(f"Sending file: {final_mp3_path}")
        response = send_file(
            final_mp3_path,
            as_attachment=True,
            download_name=f"{song_artist} - {song_title}.mp3",
            mimetype='audio/mpeg'
        )

        @response.call_on_close
        def cleanup_file():
            # Add a small delay before cleanup, especially if issues arise
            # time.sleep(0.1)
            try:
                if os.path.exists(final_mp3_path):
                    os.remove(final_mp3_path)
                    logger.info(f"Cleaned up temporary file: {final_mp3_path}")
                else:
                    logger.warning(f"Cleanup: File not found to delete: {final_mp3_path}")
            except Exception as e:
                logger.error(f"Error cleaning up file {final_mp3_path}: {e}")

        return response

    except yt_dlp.utils.DownloadError as de:
        # More specific error logging
        error_message = str(de)
        logger.error(f"yt-dlp DownloadError: {error_message}")
        if "Sign in to confirm" in error_message:
            logger.error("Authentication error: Cookies might be invalid, expired, or not correctly applied.")
            return jsonify({"error": "Failed to download due to authentication issue. Cookies might be required or invalid."}), 500
        return jsonify({"error": f"Failed to download audio: {error_message}"}), 500
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        return jsonify({"error": f"An internal server error occurred: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001)) # Changed default for local dev if 5000 is busy
    app.run(host='0.0.0.0', port=port, debug=False)
