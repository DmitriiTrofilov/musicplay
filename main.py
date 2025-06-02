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
CORS(app)

# --- YouTube Music API Setup ---
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}")
    # Consider exiting or disabling functionality if YTMusic is critical

# --- yt-dlp Configuration for MP3 download ---

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

COOKIES_FILE_PATH = 'cookies.txt' # Assumes cookies.txt is in the root of your project
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

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
    'paths': {'home': TEMP_AUDIO_DIR},  # All output files go into this directory
    'outtmpl': '%(id)s.%(ext)s',        # Filename template *within* paths.home.
                                        # yt-dlp will create e.g., TEMP_AUDIO_DIR/videoid.webm,
                                        # and the postprocessor will create TEMP_AUDIO_DIR/videoid.mp3.
    # 'ffmpeg_location': '/usr/bin/ffmpeg', # COMMENTED OUT: Let yt-dlp find ffmpeg in PATH.
                                            # This is usually more robust on Render/PaaS.
                                            # If FFmpeg errors occur, ensure it's installed and in PATH.
    'verbose': True, # Add verbose logging from yt-dlp for debugging
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

        unique_mp3_filename = f"{str(uuid.uuid4())}.mp3"
        final_mp3_path = os.path.join(TEMP_AUDIO_DIR, unique_mp3_filename)

        # This is the path where yt-dlp (after post-processing) should place the MP3.
        # It's based on video_id.mp3 inside TEMP_AUDIO_DIR.
        expected_downloaded_mp3_path = os.path.join(TEMP_AUDIO_DIR, f"{video_id}.mp3")
        logger.info(f"Expecting MP3 at: {expected_downloaded_mp3_path}")


        # Clean up any pre-existing file with the same video_id or unique name from a failed previous run
        for path_to_clean in [expected_downloaded_mp3_path, final_mp3_path]:
            if os.path.exists(path_to_clean):
                try:
                    os.remove(path_to_clean)
                    logger.info(f"Removed pre-existing file: {path_to_clean}")
                except Exception as e:
                    logger.warning(f"Could not remove pre-existing file {path_to_clean}: {e}")
        
        # Also clean up potential original downloaded files (e.g. .webm, .m4a)
        # based on video_id if they exist from a previous interrupted run.
        for ext_to_clean in ['.webm', '.m4a', '.opus', '.ogg']:
            potential_orig_file = os.path.join(TEMP_AUDIO_DIR, f"{video_id}{ext_to_clean}")
            if os.path.exists(potential_orig_file):
                try:
                    os.remove(potential_orig_file)
                    logger.info(f"Removed pre-existing original format file: {potential_orig_file}")
                except Exception as e:
                    logger.warning(f"Could not remove pre-existing original file {potential_orig_file}: {e}")


        # Use a fresh copy of YDL_OPTS_BASE for this download instance
        ydl_opts_specific = YDL_OPTS_BASE.copy()

        with yt_dlp.YoutubeDL(ydl_opts_specific) as ydl:
            logger.info(f"Starting download for video ID: {video_id} with options: {ydl_opts_specific}")
            download_url = f'https://music.youtube.com/watch?v={video_id}'
            ydl.download([download_url])
            # The log from yt-dlp itself (with verbose:True) should now show the correct destination path for FFmpeg.
            logger.info(f"Download process (including post-processing) reported as complete for video ID: {video_id}.")


        if not os.path.exists(expected_downloaded_mp3_path):
            logger.error(f"MP3 file NOT FOUND at expected path after download: {expected_downloaded_mp3_path}")
            logger.info(f"Contents of {TEMP_AUDIO_DIR}: {os.listdir(TEMP_AUDIO_DIR)}") # List dir contents
            # Check for original downloaded file if MP3 is missing, indicating FFmpeg issue
            possible_original_extensions = ['.webm', '.m4a', '.opus', '.ogg']
            found_original_file = None
            for ext in possible_original_extensions:
                original_file_path_check = os.path.join(TEMP_AUDIO_DIR, f"{video_id}{ext}")
                if os.path.exists(original_file_path_check):
                    found_original_file = original_file_path_check
                    logger.warning(f"Found original downloaded file: {found_original_file}. FFmpeg conversion to MP3 might have failed or output to a different name.")
                    break
            if found_original_file:
                 return jsonify({"error": f"Audio downloaded ({os.path.basename(found_original_file)}) but MP3 conversion failed or MP3 not found at expected location."}), 500
            return jsonify({"error": "Critical error: MP3 file not found after download and FFmpeg processing. Check logs for FFmpeg errors."}), 500

        # Rename the video_id.mp3 to a unique name before sending
        # This helps prevent conflicts if multiple requests for the same song occur nearly simultaneously,
        # though cleanup should ideally handle this. It's more for robustness during the send_file operation.
        os.rename(expected_downloaded_mp3_path, final_mp3_path)
        logger.info(f"Renamed downloaded MP3 from {expected_downloaded_mp3_path} to {final_mp3_path}")

        logger.info(f"Sending file: {final_mp3_path}")
        response = send_file(
            final_mp3_path,
            as_attachment=True,
            download_name=f"{song_artist} - {song_title}.mp3",
            mimetype='audio/mpeg'
        )

        @response.call_on_close
        def cleanup_file():
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
        error_message = str(de)
        logger.error(f"yt-dlp DownloadError: {error_message}", exc_info=True)
        if "Sign in to confirm" in error_message:
            logger.error("Authentication error: Cookies might be invalid, expired, or not correctly applied.")
            return jsonify({"error": "Failed to download due to authentication issue. Cookies might be required or invalid."}), 500
        # Check for common FFmpeg issues in the error message
        if "ffmpeg" in error_message.lower() or "ffprobe" in error_message.lower():
            logger.error("FFmpeg/FFprobe related error. Ensure FFmpeg is correctly installed and accessible in PATH on the server.")
            return jsonify({"error": f"Audio processing error (FFmpeg): {error_message}"}), 500
        return jsonify({"error": f"Failed to download audio: {error_message}"}), 500
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        return jsonify({"error": f"An internal server error occurred: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
