import typing

from abc import ABC, abstractmethod
from anime_dl.object.episode import Episode


class Creator(ABC):
    @abstractmethod
    def factory_method(self):
        pass

    def _get_scrapper(self):
        if not hasattr(self, "_scrapper"):
            self._scrapper = self.factory_method()
        return self._scrapper

    def get_episodes(self, url: str) -> typing.List[Episode]:
        return self._get_scrapper().get_episodes(url)

    def resolve_episode(self, episode: Episode) -> Episode:
        if episode.video_url:
            return episode
        return self._get_scrapper().resolve_episode(episode)
