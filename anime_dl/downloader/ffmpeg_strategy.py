import traceback
import ffmpeg
import os
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from pathvalidate import sanitize_filename
from anime_dl.downloader.strategy import Strategy
from anime_dl.object.episode import Episode
from anime_dl.utils.config_loader import ConfigLoader
from anime_dl.utils.logger import Logger

logger = Logger()
config_loader = ConfigLoader()
M3U8_SEGMENT_WORKERS = 8
M3U8_GLOBAL_SEGMENT_WORKERS = 16
M3U8_DOWNLOAD_RETRIES = 3
M3U8_REQUEST_TIMEOUT = 30
M3U8_CHUNK_SIZE = 1024 * 256
M3U8_PROGRESS_STEP = 5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
m3u8_segment_semaphore = threading.BoundedSemaphore(M3U8_GLOBAL_SEGMENT_WORKERS)


class FfmpegStrategy(Strategy):
    def download(self, episode: Episode) -> None:
        try:
            url = episode.video_url
            filename = (
                f"{episode.series_name}.{episode.season}.{episode.episode_name}.mp4"
            )
            fnt = f"{episode.series_name}.{episode.season}.{episode.episode_name}_temp.mp4"
            output = os.path.join(
                config_loader.get(section="DIRECTORY", key="output"),
                sanitize_filename(filename),
            )
            output_t = os.path.join(
                config_loader.get(section="DIRECTORY", key="output"),
                sanitize_filename(fnt),
            )
            vtt = os.path.join(
                config_loader.get(section="DIRECTORY", key="output"),
                "vtt",
                f"{episode.episode_name}.vtt",
            )
            os.makedirs(os.path.dirname(output), exist_ok=True)
            logger.info(f"started download: {filename} ({url})")
            self._download_video(url, output_t, episode.referer_url, filename)
            self._finish_download(output_t, output, vtt)
            logger.info(f"downloaded: {filename}")
        except Exception:
            logger.error(traceback.format_exc())

    def _download_video(
        self, url: str, output: str, referer: str, label: str
    ) -> None:
        if self._is_m3u8(url):
            self._download_m3u8(url, output, referer, label)
            return

        self._download_with_ffmpeg(url, output, referer)

    def _download_with_ffmpeg(self, url: str, output: str, referer: str) -> None:
        self._run_ffmpeg_download(url, output, self._ffmpeg_input_options(referer))

    def _run_ffmpeg_download(
        self, url: str, output: str, input_options: dict
    ) -> None:
        stream = ffmpeg.input(url, **input_options)
        stream = ffmpeg.output(stream, output, **{"c": "copy"})
        ffmpeg.run(stream, overwrite_output=True)

    def _download_m3u8(
        self, url: str, output: str, referer: str, label: str = "m3u8"
    ) -> None:
        try:
            media_url, playlist = self._load_media_playlist(url, referer)
            segments = self._parse_media_playlist(media_url, playlist)
            if not segments:
                raise ValueError("playlist has no downloadable segments")

            worker_count = min(M3U8_SEGMENT_WORKERS, len(segments))
            logger.info(
                f"fast m3u8 segment download started: {label}: "
                f"{len(segments)} segment(s), "
                f"{worker_count} episode worker(s), "
                f"{M3U8_GLOBAL_SEGMENT_WORKERS} global connection(s)"
            )
            with tempfile.TemporaryDirectory(prefix="anime_dl_hls_") as tmp_dir:
                segment_dir = os.path.join(tmp_dir, "segments")
                concat_path = os.path.join(tmp_dir, "segments.ts")
                os.makedirs(segment_dir, exist_ok=True)

                segment_paths = self._download_segments(
                    segments, segment_dir, referer, label
                )
                self._concat_segments(segment_paths, concat_path)
                logger.info(f"fast m3u8 segments complete: {label}; remuxing local file")
                self._remux_transport_stream(concat_path, output)
                logger.info(f"fast m3u8 remux complete: {label}")
        except Exception as e:
            logger.error(f"fast m3u8 download failed: {label}: {e}")
            self._remove_file(output)
            raise

    def _load_media_playlist(self, url: str, referer: str) -> tuple[str, str]:
        playlist = self._fetch_text(url, referer)
        visited_urls = {url}
        while self._is_master_playlist(playlist):
            if self._has_external_audio_playlist(playlist):
                raise ValueError("master playlist uses separate audio tracks")

            media_url = self._select_media_playlist_url(url, playlist)
            if media_url in visited_urls:
                raise ValueError("master playlist did not resolve to a media playlist")

            logger.info(f"selected m3u8 variant: {media_url}")
            url = media_url
            visited_urls.add(url)
            playlist = self._fetch_text(url, referer)
        return url, playlist

    def _fetch_text(self, url: str, referer: str) -> str:
        response = requests.get(
            url,
            headers=self._request_headers(referer),
            timeout=M3U8_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.text

    def _select_media_playlist_url(self, playlist_url: str, playlist: str) -> str:
        best_variant = None
        pending_variant = None
        for line in self._playlist_lines(playlist):
            if line.startswith("#EXT-X-STREAM-INF:"):
                pending_variant = self._m3u8_variant_score(line)
                continue
            if pending_variant is None or line.startswith("#"):
                continue

            variant_url = urljoin(playlist_url, line)
            if best_variant is None or pending_variant > best_variant[0]:
                best_variant = (pending_variant, variant_url)
            pending_variant = None

        if best_variant is None:
            return playlist_url
        return best_variant[1]

    def _parse_media_playlist(self, playlist_url: str, playlist: str) -> list[str]:
        if self._is_master_playlist(playlist):
            raise ValueError("master playlist must be resolved before downloading")

        segments = []
        for line in self._playlist_lines(playlist):
            if line.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in line:
                raise ValueError("encrypted m3u8 playlist")
            if line.startswith("#EXT-X-MAP:"):
                raise ValueError("fMP4 m3u8 playlist")
            if line.startswith("#EXT-X-BYTERANGE:"):
                raise ValueError("byterange m3u8 playlist")
            if line and not line.startswith("#"):
                segments.append(urljoin(playlist_url, line))
        return segments

    def _download_segments(
        self,
        segments: list[str],
        segment_dir: str,
        referer: str,
        label: str = "m3u8",
    ) -> list[str]:
        segment_paths = [None] * len(segments)
        failed_segments = []
        completed_count = 0
        next_progress = M3U8_PROGRESS_STEP
        max_workers = min(M3U8_SEGMENT_WORKERS, len(segments))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(
                    self._download_segment, index, url, segment_dir, referer
                ): index
                for index, url in enumerate(segments)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    segment_paths[index] = future.result()
                    completed_count += 1
                    next_progress = self._log_m3u8_progress(
                        label, completed_count, len(segments), next_progress
                    )
                except Exception as e:
                    failed_segments.append((index, segments[index], e))

        if failed_segments:
            logger.warning(
                f"{len(failed_segments)} m3u8 segment(s) failed in parallel; "
                "retrying one at a time"
            )
            for index, url, _ in failed_segments:
                segment_paths[index] = self._download_segment(
                    index, url, segment_dir, referer
                )
                completed_count += 1
                next_progress = self._log_m3u8_progress(
                    label, completed_count, len(segments), next_progress
                )

        return segment_paths

    def _log_m3u8_progress(
        self, label: str, completed: int, total: int, next_progress: int
    ) -> int:
        percent = int(completed * 100 / total)
        if percent < next_progress and completed != total:
            return next_progress

        logger.info(f"m3u8 progress: {percent}% ({completed}/{total}) - {label}")
        return ((percent // M3U8_PROGRESS_STEP) + 1) * M3U8_PROGRESS_STEP

    def _download_segment(
        self, index: int, url: str, segment_dir: str, referer: str
    ) -> str:
        path = os.path.join(segment_dir, f"{index:06d}.ts")
        for attempt in range(1, M3U8_DOWNLOAD_RETRIES + 1):
            try:
                with m3u8_segment_semaphore:
                    with requests.get(
                        url,
                        headers=self._request_headers(referer),
                        stream=True,
                        timeout=M3U8_REQUEST_TIMEOUT,
                    ) as response:
                        response.raise_for_status()
                        with open(path, "wb") as file:
                            for chunk in response.iter_content(M3U8_CHUNK_SIZE):
                                if chunk:
                                    file.write(chunk)

                if os.path.getsize(path) == 0:
                    raise ValueError("empty m3u8 segment")
                return path
            except Exception:
                self._remove_file(path)
                if attempt == M3U8_DOWNLOAD_RETRIES:
                    raise
                time.sleep(0.5 * attempt)

    def _concat_segments(self, segment_paths: list[str], output: str) -> None:
        with open(output, "wb") as output_file:
            for path in segment_paths:
                with open(path, "rb") as segment_file:
                    shutil.copyfileobj(segment_file, output_file, M3U8_CHUNK_SIZE)

    def _remux_transport_stream(self, transport_stream: str, output: str) -> None:
        stream = ffmpeg.input(transport_stream)
        stream = ffmpeg.output(
            stream, output, **{"c": "copy", "movflags": "+faststart"}
        )
        ffmpeg.run(stream, overwrite_output=True)

    def _finish_download(self, temp_output: str, output: str, vtt: str) -> None:
        if os.path.exists(vtt):
            ffmpeg.output(
                ffmpeg.input(temp_output),
                ffmpeg.input(vtt),
                output,
                **{"c": "copy", "c:s": "mov_text"},
            ).run(overwrite_output=True)
            os.remove(vtt)
            os.remove(temp_output)
        else:
            self._remove_file(output)
            os.rename(temp_output, output)

    def _ffmpeg_input_options(self, referer: str) -> dict:
        options = {
            "reconnect": "1",
            "reconnect_streamed": "1",
            "reconnect_at_eof": "1",
            "reconnect_delay_max": "5",
            "user_agent": USER_AGENT,
        }
        header_text = self._ffmpeg_headers(referer)
        if header_text:
            options["headers"] = header_text
        return options

    def _request_headers(self, referer: str) -> dict:
        headers = {"User-Agent": USER_AGENT}
        if referer:
            headers["Referer"] = referer
        return headers

    def _ffmpeg_headers(self, referer: str) -> str:
        if not referer:
            return ""
        return f"Referer: {referer}\r\n"

    def _playlist_lines(self, playlist: str) -> list[str]:
        return [line.strip() for line in playlist.splitlines() if line.strip()]

    def _is_master_playlist(self, playlist: str) -> bool:
        return any(
            line.startswith("#EXT-X-STREAM-INF:")
            for line in self._playlist_lines(playlist)
        )

    def _m3u8_variant_score(self, line: str) -> tuple[int, int]:
        width, height = self._m3u8_resolution_attribute(line)
        return (width * height, self._m3u8_int_attribute(line, "BANDWIDTH"))

    def _m3u8_int_attribute(self, line: str, name: str) -> int:
        value = self._m3u8_attributes(line).get(name)
        if value is None or not value.isdigit():
            return 0
        return int(value)

    def _m3u8_resolution_attribute(self, line: str) -> tuple[int, int]:
        value = self._m3u8_attributes(line).get("RESOLUTION", "")
        match = re.search(r"^([0-9]+)x([0-9]+)$", value)
        if match is None:
            return (0, 0)
        return (int(match.group(1)), int(match.group(2)))

    def _m3u8_attributes(self, line: str) -> dict[str, str]:
        return {
            key: value.strip('"')
            for key, value in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line)
        }

    def _has_external_audio_playlist(self, playlist: str) -> bool:
        return any(
            line.startswith("#EXT-X-MEDIA:")
            and "TYPE=AUDIO" in line
            and "URI=" in line
            for line in self._playlist_lines(playlist)
        )

    def _is_m3u8(self, url: str) -> bool:
        return url.split("?", 1)[0].lower().endswith(".m3u8")

    def _remove_file(self, path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
