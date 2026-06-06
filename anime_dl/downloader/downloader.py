from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from anime_dl.downloader.strategy import Strategy


class Downloader:
    def __init__(self, strategy: Strategy) -> None:
        self._strategy = strategy

    @property
    def strategy(self) -> Strategy:
        return self._strategy

    @strategy.setter
    def strategy(self, strategy: Strategy) -> None:
        self._strategy = strategy

    def download(self, data: Any) -> Any:
        return self._strategy.download(data)

    def download_many(self, data: Iterable[Any], max_workers: int = 8) -> None:
        items = list(data)
        if not items:
            return

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.download, item) for item in items]
            for future in as_completed(futures):
                future.result()
