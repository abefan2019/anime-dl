import json
import re
import typing

from anime_dl.const import regex, general
from anime_dl.object.episode import Episode
from anime_dl.scrapper.scrapper import Scrapper
from anime_dl.utils import http_client
from anime_dl.utils.logger import Logger
from bs4 import BeautifulSoup

logger = Logger()


class AgdmTvScrapper(Scrapper):
    def get_episodes(self, url: str) -> typing.List[Episode]:
        if re.search(regex.URL["agdm.tv"]["series"], url):
            return self.parse_series(url)
        elif re.search(regex.URL["agdm.tv"]["episode"], url):
            m = re.search(regex.URL["agdm.tv"]["episode"], url)
            id = int(m.groups()[0])
            episode_no = m.groups()[2]
            episode, x_servers = self.parse_episode(url)
            # check if video source available
            if self.test_connectivity(episode.video_url):
                return [episode]
            # try other servers
            else:
                logger.warning(f"video source fail: {url} ({episode.video_url})")
                for x_server in x_servers:
                    x_url = f"https://agdm.tv/play/{id}-{x_server}-{episode_no}.html"
                    logger.info(f"try another server: {x_url}")
                    episode, _ = self.parse_episode(x_url)
                    if self.test_connectivity(episode.video_url):
                        return [episode]
                    else:
                        logger.warning(f"video source fail: {x_url} ({episode.video_url})")
            return [Episode()]
        else:
            raise Exception(f"Unsupported URL: {url}")

    def parse_series(self, url: str) -> typing.List[Episode]:
        try:
            episodes = []
            headers = general.REQUEST["header"]
            doc = BeautifulSoup(http_client.get(url, headers=headers).text, "html.parser")
            series_name_node = doc.select_one(".myui-info__title")
            series_name = (
                series_name_node.text.strip()
                if series_name_node is not None
                else "na"
            )
            image_src = (
                doc.select_one("a.myui-vodlist__thumb img.lazyload")
                .attrs["src"]
                .strip()
            )
            # select_one for 1st playlist
            content_list = doc.select_one("ul.myui-content__list")
            items = content_list.select("li a")
            episode_no = 1
            for item in items:
                referer_url = "https://agdm.tv" + item.attrs["href"].strip()
                episodes.append(
                    Episode()
                    .set_series_name(series_name)
                    .set_season("na")
                    .set_episode_name(item.text.strip())
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

        resolved, x_servers = self.parse_episode(episode.referer_url)
        if self.test_connectivity(resolved.video_url):
            return self._merge_episode(episode, resolved)

        logger.warning(f"video source fail: {episode.referer_url} ({resolved.video_url})")
        m = re.search(regex.URL["agdm.tv"]["episode"], episode.referer_url)
        id = int(m.groups()[0])
        episode_no = m.groups()[2]
        for x_server in x_servers:
            x_url = f"https://agdm.tv/play/{id}-{x_server}-{episode_no}.html"
            logger.info(f"try another server: {x_url}")
            resolved, _ = self.parse_episode(x_url)
            if self.test_connectivity(resolved.video_url):
                return self._merge_episode(episode, resolved)
            logger.warning(f"video source fail: {x_url} ({resolved.video_url})")
        return episode

    def _merge_episode(self, episode: Episode, resolved: Episode) -> Episode:
        return (
            episode.set_series_name(resolved.series_name or episode.series_name)
            .set_season(resolved.season or episode.season)
            .set_episode_name(resolved.episode_name or episode.episode_name)
            .set_episode_no(resolved.episode_no or episode.episode_no)
            .set_video_url(resolved.video_url)
            .set_referer_url(resolved.referer_url or episode.referer_url)
            .set_image_src(resolved.image_src or episode.image_src)
        )

    def parse_episode(self, url: str) -> tuple[Episode, typing.List[int]]:
        try:
            m = re.search(regex.URL["agdm.tv"]["episode"], url)
            server = int(m.groups()[1])
            episode_no = m.groups()[2]
            headers = general.REQUEST["header"]
            doc = BeautifulSoup(http_client.get(url, headers=headers).text, "html.parser")
            html = str(doc)
            player_aaaa = json.loads(
                html.split("var player_aaaa=")[1]
                .split("</script>")[0]
                .strip()
                .encode()
                .decode("unicode_escape")
            )
            series_name = player_aaaa["vod_data"]["vod_name"]
            episode_name = doc.select_one(
                ".myui-panel_hd small.text-muted"
            ).text.strip()
            video_url = player_aaaa["url"]
            playlists = doc.select("ul.nav-tabs li a[href^='#playlist']")
            x_servers = list(
                filter(
                    lambda s: s != server,
                    map(
                        lambda p: int(p.attrs["href"].replace("#playlist", "")),
                        playlists,
                    ),
                )
            )
            return (
                Episode()
                .set_series_name(series_name)
                .set_season("na")
                .set_episode_name(episode_name)
                .set_episode_no(episode_no)
                .set_video_url(video_url)
                .set_referer_url(url)
                .set_image_src("na")
            ), x_servers
        except Exception as e:
            logger.error(f"{url}: {e}")
            return Episode()

    def test_connectivity(self, url):
        headers = general.REQUEST["header"]
        return http_client.get(url, headers=headers).ok
