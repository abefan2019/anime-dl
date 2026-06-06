import re
import typing
import os

from anime_dl.const import regex, general
from anime_dl.object.episode import Episode
from anime_dl.scrapper.scrapper import Scrapper
from anime_dl.utils.config_loader import ConfigLoader
from anime_dl.utils import http_client
from anime_dl.utils.logger import Logger
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urlunparse

logger = Logger()
config_loader = ConfigLoader()
XGCARTOON_URL_KEYS = ("xgcartoon", "lincartoon", "dailygh")
XGCARTOON_DOMAINS = (
    "www.xgcartoon.com",
    "www.lincartoon.com",
    "www.dailygh.com",
)
SCRAPER_TIMEOUT = (5, 30)


class XgCartoonScrapper(Scrapper):
    def get_episodes(self, url: str) -> typing.List[Episode]:
        series_match = self._match_url(url, "series")
        episode_match = self._match_url(url, "episode")
        if series_match:
            return self.parse_series(url)
        elif episode_match:
            m = episode_match
            cartoon_id = m.groups()[0]
            episode = self.parse_episode(url)
            series = list(
                filter(
                    lambda i: i.referer_url == url,
                    self.parse_series(
                        f"{self._origin(url)}/detail/{cartoon_id}"
                    ),
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

    def parse_series(self, url: str) -> typing.List[Episode]:
        try:
            episodes = []
            headers = general.REQUEST["header"]
            response = self._get_page(url, headers)
            page_origin = self._origin(response.url or url)
            doc = BeautifulSoup(response.text, "html.parser")
            series_name = doc.select_one("h1.h1").text.strip()
            image_src = doc.select_one(".detail-sider > amp-img").attrs["src"].strip()
            season = None
            episode_no = 1
            items = doc.select("div.detail-right__volumes > div.row > div")
            for item in items:
                if "volume-title" in item.attrs["class"]:
                    season = item.text.strip()
                    episode_no = 1
                else:
                    chapter = item.select_one("a.goto-chapter")
                    if chapter:
                        episode_name = chapter.attrs["title"].strip()
                        href = chapter.attrs["href"].strip()
                        cartoon_id = parse_qs(urlparse(href).query)["cartoon_id"][0]
                        chapter_id = parse_qs(urlparse(href).query)["chapter_id"][0]
                        referer_url = (
                            f"{page_origin}/video/{cartoon_id}/{chapter_id}.html"
                        )
                        episodes.append(
                            Episode()
                            .set_series_name(series_name)
                            .set_season(season)
                            .set_episode_name(episode_name)
                            .set_episode_no(episode_no)
                            .set_referer_url(referer_url)
                            .set_image_src(image_src)
                        )
                        episode_no = episode_no + 1
            return episodes
        except Exception as e:
            logger.error(f"{url}: {e}")
            return []

    def resolve_episode(self, episode: Episode) -> Episode:
        if episode.video_url or not episode.referer_url:
            return episode

        resolved = self.parse_episode(episode.referer_url)
        return (
            episode.set_episode_name(episode.episode_name or resolved.episode_name)
            .set_video_url(resolved.video_url)
            .set_referer_url(resolved.referer_url or episode.referer_url)
        )

    def parse_episode(self, url: str) -> Episode:
        try:
            headers = general.REQUEST["header"]
            response = self._get_page(url, headers)
            doc = BeautifulSoup(response.text, "html.parser")
            episode_name = doc.select_one("h1.h1").text.strip()
            iframe_src = doc.select_one("iframe").attrs["src"].strip()
            if "vid" in parse_qs(urlparse(iframe_src).query):
                vid = parse_qs(urlparse(iframe_src).query)["vid"][0]
                video_url = f"https://xgct-video.vzcdn.net/{vid}/playlist.m3u8"
                vtt_url = f"https://xgct-video.vzcdn.net/{vid}/captions/TW.vtt"
                response = http_client.get(vtt_url)
                if response.status_code == 200:
                    path = os.path.join(
                        config_loader.get(section="DIRECTORY", key="output"),
                        "vtt",
                        episode_name + ".vtt",
                    )
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(response.content)
            else:
                video_url = self.parse_iframe(iframe_src)
            return (
                Episode()
                .set_episode_name(episode_name)
                .set_video_url(video_url)
                .set_referer_url(url)
            )
        except Exception as e:
            logger.error(f"{url}: {e}")
            return Episode()

    def parse_iframe(self, url: str) -> str:
        try:
            headers = general.REQUEST["header"]
            response = self._get_page(url, headers)
            doc = BeautifulSoup(response.text, "html.parser")
            return doc.select_one("video#main-video source").attrs["src"].strip()
        except Exception as e:
            logger.error(f"{url}: {e}")
            return Episode()

    def _match_url(self, url: str, url_type: str):
        for key in XGCARTOON_URL_KEYS:
            match = re.search(regex.URL[key][url_type], url)
            if match:
                return match
        return None

    def _get_page(self, url: str, headers: dict):
        last_exception = None
        for mirror_url in self._mirror_urls(url):
            try:
                response = http_client.get(
                    mirror_url,
                    headers=headers,
                    timeout=SCRAPER_TIMEOUT,
                )
                response.raise_for_status()
                if mirror_url != url:
                    logger.warning(f"using mirror: {mirror_url}")
                return response
            except Exception as e:
                last_exception = e
                logger.warning(f"{mirror_url}: {e}")
        raise last_exception

    def _mirror_urls(self, url: str) -> typing.List[str]:
        parsed_url = urlparse(url)
        if parsed_url.netloc not in XGCARTOON_DOMAINS:
            return [url]

        domains = [parsed_url.netloc] + [
            domain for domain in XGCARTOON_DOMAINS if domain != parsed_url.netloc
        ]
        return [
            urlunparse(parsed_url._replace(netloc=domain))
            for domain in domains
        ]

    def _origin(self, url: str) -> str:
        parsed_url = urlparse(url)
        return f"{parsed_url.scheme}://{parsed_url.netloc}"
