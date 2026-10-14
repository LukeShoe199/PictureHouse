"""The Movie Database client (rate limiting, retries and caching come from
``api_client.CachedApi``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from picturehouse.metadata.api_client import MAX_ATTEMPTS, CachedApi, MetadataApiError

API = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
DETAIL_TTL = 30 * 86400
SEARCH_TTL = 7 * 86400
__all__ = ["MAX_ATTEMPTS", "TMDb", "TMDbError", "image_url"]


class TMDbError(MetadataApiError):
    pass


class TMDb(CachedApi):
    name = "TMDb"
    error = TMDbError

    def __init__(self, api_key: str, cache_dir: Path, *, language: str = "en-GB",
                 region: str = "GB", transport: httpx.BaseTransport | None = None,
                 rate: float = 10.0) -> None:  # fmt: skip
        self.language = language
        self.region = region
        if api_key.startswith("eyJ"):  # v4 read-access token (a JWT)
            headers, params = {"Authorization": f"Bearer {api_key}"}, {}
        else:
            headers, params = {}, {"api_key": api_key}
        super().__init__(API, cache_dir, headers=headers, params=params, transport=transport,
                         rate=rate)  # fmt: skip

    def get(self, path: str, ttl: float, **params: Any) -> dict[str, Any]:
        params.setdefault("language", self.language)
        return super().get(path, ttl, **params)

    # ---------------------------------------------------------------- API

    def search_movie(self, query: str, year: int | None = None) -> list[dict]:
        return self.get("/search/movie", SEARCH_TTL, query=query, year=year)["results"]

    def search_tv(self, query: str, year: int | None = None) -> list[dict]:
        return self.get("/search/tv", SEARCH_TTL, query=query,
                        first_air_date_year=year)["results"]  # fmt: skip

    def movie(self, tmdb_id: int) -> dict:
        return self.get(f"/movie/{tmdb_id}", DETAIL_TTL,
                        append_to_response="credits,release_dates")  # fmt: skip

    def tv(self, tmdb_id: int) -> dict:
        return self.get(f"/tv/{tmdb_id}", DETAIL_TTL,
                        append_to_response="credits,content_ratings")  # fmt: skip

    def season(self, tmdb_id: int, season: int) -> dict:
        return self.get(f"/tv/{tmdb_id}/season/{season}", DETAIL_TTL)

    def find_imdb(self, imdb_id: str) -> dict:
        return self.get(f"/find/{imdb_id}", DETAIL_TTL, external_source="imdb_id")

    # ---------------------------------------------------------------- helpers

    def certification(self, details: dict, kind: str) -> str | None:
        if kind == "movie":
            for c in (details.get("release_dates") or {}).get("results", []):
                if c.get("iso_3166_1") == self.region:
                    for rd in c.get("release_dates", []):
                        if rd.get("certification"):
                            return rd["certification"]
        else:
            for c in (details.get("content_ratings") or {}).get("results", []):
                if c.get("iso_3166_1") == self.region and c.get("rating"):
                    return c["rating"]
        return None


def image_url(path: str | None, size: str) -> str | None:
    return f"{IMAGE_BASE}/{size}{path}" if path else None
