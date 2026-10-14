"""OMDb (omdbapi.com) client: the fallback when TMDb isn't configured or finds nothing.

OMDb is IMDb-based: titles, plots, genres, ratings, cast and a poster URL in
the standard responses. It has no backdrops or episode stills, and the free
key allows about 1,000 requests a day, so responses are cached for a long
time and a "limit reached" answer stops OMDb lookups for the rest of the
run instead of failing every remaining item.

OMDb reports most errors inside a 200 response (``"Response": "False"``);
``check`` turns those into ``OMDbError`` (or ``OMDbLimit``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from picturehouse.metadata.api_client import CachedApi, MetadataApiError

API = "https://www.omdbapi.com"
TTL = 60 * 86400  # OMDb data changes rarely and the daily quota is small
NOT_FOUND = ("not found", "incorrect imdb id", "too many results")


class OMDbError(MetadataApiError):
    pass


class OMDbLimit(OMDbError):
    """The daily request limit is used up; try again tomorrow."""


class OMDbNotFound(OMDbError):
    pass


def na(v: Any) -> Any:
    """OMDb writes missing values as the string "N/A"."""
    return None if v in (None, "", "N/A") else v


def year_of(v: str | None) -> int | None:
    m = re.match(r"\s*(\d{4})", v or "")
    return int(m.group(1)) if m else None


def split_names(v: str | None) -> list[str]:
    v = na(v)
    if not v:
        return []
    # "Christopher Nolan, Emma Thomas (co-director)" -> names without roles
    return [re.sub(r"\s*\(.*?\)\s*$", "", n).strip() for n in v.split(",") if n.strip()]


class OMDb(CachedApi):
    name = "OMDb"
    error = OMDbError

    def __init__(self, api_key: str, cache_dir: Path, *,
                 transport: httpx.BaseTransport | None = None, rate: float = 5.0) -> None:  # fmt: skip
        super().__init__(API, cache_dir, params={"apikey": api_key}, transport=transport,
                         rate=rate)  # fmt: skip

    def check(self, data: dict[str, Any], path: str) -> dict[str, Any]:
        if str(data.get("Response", "True")).lower() == "false":
            err = str(data.get("Error") or "unknown error")
            low = err.lower()
            if "limit" in low:
                raise OMDbLimit(f"OMDb daily request limit reached ({err})")
            if "invalid api key" in low or "no api key" in low:
                raise OMDbError(f"OMDb rejected the API key ({err})")
            if any(s in low for s in NOT_FOUND):
                # Cache "nothing found" too: it costs quota to ask again.
                return {"Response": "False", "Error": err, "_not_found": True}
            raise OMDbError(f"OMDb: {err}")
        return data

    # ---------------------------------------------------------------- API

    def search(self, query: str, year: int | None, kind: str) -> list[dict]:
        """kind: movie | series. Search results are brief (title, year, id, poster)."""
        data = self.get("/", TTL, s=query, y=year, type=kind)
        return [] if data.get("_not_found") else list(data.get("Search") or [])

    def by_id(self, imdb_id: str) -> dict:
        data = self.get("/", TTL, i=imdb_id, plot="full")
        if data.get("_not_found"):
            raise OMDbNotFound(f"OMDb has no {imdb_id}")
        return data

    def season(self, imdb_id: str, season: int) -> list[dict]:
        data = self.get("/", TTL, i=imdb_id, Season=season)
        return [] if data.get("_not_found") else list(data.get("Episodes") or [])
