from abc import ABC, abstractmethod

from anime_dl.object.episode import Episode


class Scrapper(ABC):
    @abstractmethod
    def get_episodes(self, url: str):
        pass

    def resolve_episode(self, episode: Episode) -> Episode:
        return episode
