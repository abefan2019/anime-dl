import traceback
from collections import Counter
from urllib.parse import unquote

import os
import re
from pathvalidate import sanitize_filename

from anime_dl.const import regex
from anime_dl.downloader.downloader import Downloader
from anime_dl.downloader.ffmpeg_strategy import FfmpegStrategy
from anime_dl.scrapper.agdm_tv_creator import AgdmTvCreator
from anime_dl.scrapper.anime1_in_creator import Anime1InCreator
from anime_dl.scrapper.anime1_me_creator import Anime1MeCreator
from anime_dl.scrapper.xgcartoon_creator import XgCartoonCreator
from anime_dl.scrapper.yhdm_one_creator import YhdmOneCreator
from anime_dl.utils.config_loader import ConfigLoader
from anime_dl.utils.logger import Logger
from anime_dl.validator.episode_name_validator import EpisodeNameValidator
from anime_dl.validator.season_validator import SeasonValidator
from anime_dl.validator.series_name_validator import SeriesNameValidator
from anime_dl.validator.video_url_validator import VideoUrlValidator

config_loader = ConfigLoader()
logger = Logger()
MAX_DOWNLOAD_WORKERS = 8


def is_downloaded(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def remove_files(*paths: str) -> None:
    for path in paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def parse_selection(value: str, max_value: int) -> list[int]:
    value = value.strip().lower()
    if value in ("", "a", "all", "*"):
        return list(range(1, max_value + 1))

    selected = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise ValueError(f"invalid range: {part}")
            values = range(start, end + 1)
        else:
            values = [int(part)]

        for index in values:
            if index < 1 or index > max_value:
                raise ValueError(f"selection out of range: {index}")
            if index not in selected:
                selected.append(index)

    if not selected:
        raise ValueError("empty selection")
    return selected


def get_seasons(episodes) -> list[str]:
    return list(dict.fromkeys(episode.season for episode in episodes))


def choose_download_episodes(episodes):
    seasons = get_seasons(episodes)
    if not seasons:
        return []

    episode_counts = Counter(episode.season for episode in episodes)
    print("Seasons:")
    for index, season in enumerate(seasons, start=1):
        print(f"({index}) {season} ({episode_counts[season]} episode(s))")

    season_indexes = parse_selection(
        input("Choose seasons (all, 1, 1-3, 1,3): "),
        len(seasons),
    )
    selected_seasons = [seasons[index - 1] for index in season_indexes]
    selectable_episodes = [
        episode for episode in episodes if episode.season in selected_seasons
    ]

    print("Episodes:")
    for index, episode in enumerate(selectable_episodes, start=1):
        print(f"({index}) [{episode.season}] {episode.episode_name}")

    episode_indexes = parse_selection(
        input("Choose episodes (all, 1, 1-3, 1,3): "),
        len(selectable_episodes),
    )
    return [selectable_episodes[index - 1] for index in episode_indexes]


class EpisodeDownloadStrategy:
    def __init__(self, scrapper, strategy, validator) -> None:
        self._scrapper = scrapper
        self._strategy = strategy
        self._validator = validator

    def download(self, episode) -> None:
        should_skip, prechecked_path = self._skip_if_downloaded(episode)
        if should_skip:
            return

        if not episode.video_url:
            logger.info(f"fetching video link: {episode.episode_name}")
        episode = self._scrapper.resolve_episode(episode)
        self._validator.validate(episode)
        fp, fpt, fn = self._file_paths(episode)
        if fp != prechecked_path and is_downloaded(fp):
            print(f"Skip {fn} (downloaded)")
            return

        remove_files(fp, fpt)
        self._strategy.download(episode)

    def _skip_if_downloaded(self, episode):
        if not self._has_filename_parts(episode):
            return False, None

        fp, _, fn = self._file_paths(episode)
        if is_downloaded(fp):
            print(f"Skip {fn} (downloaded)")
            return True, fp
        return False, fp

    def _has_filename_parts(self, episode) -> bool:
        return all((episode.series_name, episode.season, episode.episode_name))

    def _file_paths(self, episode):
        fn = f"{episode.series_name}.{episode.season}.{episode.episode_name}.mp4"
        fnt = f"{episode.series_name}.{episode.season}.{episode.episode_name}_temp.mp4"
        fp = os.path.join(
            config_loader.get(section="DIRECTORY", key="output"),
            sanitize_filename(fn),
        )
        fpt = os.path.join(
            config_loader.get(section="DIRECTORY", key="output"),
            sanitize_filename(fnt),
        )
        return fp, fpt, fn


def main(url: str) -> None:
    try:
        url = unquote(url)

        if (
            re.search(regex.URL["xgcartoon"]["domain"], url)
            or re.search(regex.URL["lincartoon"]["domain"], url)
            or re.search(regex.URL["dailygh"]["domain"], url)
        ):
            scrapper = XgCartoonCreator()
        elif re.search(regex.URL["anime1.me"]["domain"], url):
            scrapper = Anime1MeCreator()
        elif re.search(regex.URL["anime1.in"]["domain"], url):
            scrapper = Anime1InCreator()
        elif re.search(regex.URL["yhdm.one"]["domain"], url):
            scrapper = YhdmOneCreator()
        elif re.search(regex.URL["agdm.tv"]["domain"], url):
            scrapper = AgdmTvCreator()
        else:
            raise Exception(f"Unsupported URL: {url}")

        # scrapping
        episodes = scrapper.get_episodes(url)

        # validator
        video_url_validator = VideoUrlValidator()
        series_name_validator = SeriesNameValidator()
        season_validator = SeasonValidator()
        episode_name_validator = EpisodeNameValidator()
        video_url_validator.set_next(series_name_validator).set_next(
            season_validator
        ).set_next(episode_name_validator)

        # downloader
        ffmpeg_strategy = FfmpegStrategy()
        download_strategy = EpisodeDownloadStrategy(
            scrapper, ffmpeg_strategy, video_url_validator
        )
        downloader = Downloader(download_strategy)
        download_queue = choose_download_episodes(episodes)
        if download_queue:
            logger.info(
                f"downloading {len(download_queue)} episode(s) "
                f"with up to {MAX_DOWNLOAD_WORKERS} concurrent downloads"
            )
            downloader.download_many(download_queue, max_workers=MAX_DOWNLOAD_WORKERS)
    except Exception as e:
        logger.error(traceback.format_exc())
        raise
