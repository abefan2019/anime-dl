import json
import regex as re
import typing

from anime_dl.const import regex, general
from anime_dl.object.episode import Episode
from anime_dl.scrapper.scrapper import Scrapper
from anime_dl.utils import http_client
from anime_dl.utils.logger import Logger
from bs4 import BeautifulSoup

logger = Logger()


class Anime1MeScrapper(Scrapper):
    def get_episodes(self, url: str) -> typing.List[Episode]:
        if re.search(regex.URL["anime1.me"]["series_s"], url) or re.search(
            regex.URL["anime1.me"]["series"], url
        ):
            return self.parse_series(url)
        elif re.search(regex.URL["anime1.me"]["episode_s"], url) or re.search(
            regex.URL["anime1.me"]["episode"], url
        ):
            episode, series_url = self.parse_episode(url)
            series = list(
                filter(
                    lambda i: i.episode_name == episode.episode_name,
                    self.get_info(series_url),
                )
            )
            if len(series) == 1:
                episode = (
                    episode.set_series_name(series[0].series_name)
                    .set_season(series[0].season)
                    .set_episode_no(series[0].episode_no)
                    .set_image_src(series[0].image_src)
                )
            return [episode]
        else:
            raise Exception(f"Unsupported URL: {url}")

    def get_info(self, url: str) -> str:
        try:
            episodes = []
            headers = general.REQUEST["header"]
            doc = BeautifulSoup(http_client.get(url, headers=headers).text, "html.parser")
            series_name = doc.select_one("h1.page-title").text.strip()
            articles = doc.select("article")
            episode_no = 1
            for article in reversed(articles):
                episode_name = article.select_one("h2.entry-title").text.strip()
                episodes.append(
                    Episode()
                    .set_series_name(series_name)
                    .set_season("na")
                    .set_episode_name(episode_name)
                    .set_episode_no(episode_no)
                    .set_image_src("na")
                )
                episode_no = episode_no + 1
            return episodes
        except Exception as e:
            logger.error(f"{url}: {e}")
            return []

    def parse_series(self, url: str) -> typing.List[Episode]:
        try:
            episodes = []
            headers = general.REQUEST["header"]
            doc = BeautifulSoup(http_client.get(url, headers=headers).text, "html.parser")
            series_name = doc.select_one("h1.page-title").text.strip()
            articles = doc.select("article")
            episode_no = 1
            for article in reversed(articles):
                episode_name = article.select_one("h2.entry-title").text.strip()
                referer_url = article.select_one("h2.entry-title a").attrs["href"].strip()
                episodes.append(
                    Episode()
                    .set_series_name(series_name)
                    .set_season("na")
                    .set_episode_name(episode_name)
                    .set_episode_no(episode_no)
                    .set_referer_url(referer_url)
                    .set_image_src("na")
                )
                episode_no = episode_no + 1
            return episodes
        except Exception as e:
            logger.error(f"{url}: {e}")
            return []

    def resolve_episode(self, episode: Episode) -> Episode:
        if episode.video_url or not episode.referer_url:
            return episode

        resolved, _ = self.parse_episode(episode.referer_url)
        return (
            episode.set_episode_name(episode.episode_name or resolved.episode_name)
            .set_video_url(resolved.video_url)
            .set_referer_url(resolved.referer_url or episode.referer_url)
        )

    def parse_episode(self, url: str) -> tuple[Episode, str]:
        try:
            headers = general.REQUEST["header"]
            doc = BeautifulSoup(http_client.get(url, headers=headers).text, "html.parser")
            series_url = (
                "https://anime1.me" + doc.select_one("a[href^='/?cat']").attrs["href"].strip()
            )
            episode_name = doc.select_one("h2.entry-title").text.strip()
            data_apireq = doc.select_one("video").attrs["data-apireq"].strip()
            headers.update(
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                }
            )
            payload = f"d={data_apireq}"
            resp = json.loads(
                http_client.post(
                    "https://v.anime1.me/api", data=payload, headers=headers
                ).text
            )
            video_url = "https:" + resp["s"][0]["src"]
            return (
                Episode()
                .set_episode_name(episode_name)
                .set_video_url(video_url)
                .set_referer_url(url)
            ), series_url
        except Exception as e:
            logger.error(f"{url}: {e}")
            return Episode()
