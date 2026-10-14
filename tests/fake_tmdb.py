"""A tiny in-process TMDb for tests (httpx.MockTransport)."""

from __future__ import annotations

import httpx

MOVIES = {
    999001: {
        "id": 999001,
        "title": "The Grand Test",
        "release_date": "2019-05-01",
        "overview": "TMDb plot for the grand test.",
        "poster_path": "/grand.jpg",
        "backdrop_path": "/grand-bg.jpg",
        "genres": [{"name": "Drama"}],
        "imdb_id": "tt9990010",
        "vote_average": 7.1,
    },
    194: {
        "id": 194,
        "title": "Amélie",
        "release_date": "2001-04-25",
        "overview": "A shy waitress decides to change the lives of those around her.",
        "poster_path": "/amelie.jpg",
        "genres": [{"name": "Comedy"}, {"name": "Romance"}],
        "imdb_id": "tt0211915",
        "vote_average": 7.9,
    },
    5001: {"id": 5001, "title": "Odd Codecs", "release_date": "2020-01-01", "overview": "One."},
    5002: {"id": 5002, "title": "Odd Codecs", "release_date": "2020-09-01", "overview": "Two."},
    1917: {"id": 530915, "title": "1917", "release_date": "2019-12-25", "overview": "WW1."},
}
for m in MOVIES.values():
    m.setdefault("genres", [])
    m["credits"] = {
        "crew": [{"name": "Some Director", "job": "Director"}],
        "cast": [{"name": "Actor One"}, {"name": "Actor Two"}],
    }
    m["release_dates"] = {
        "results": [{"iso_3166_1": "GB", "release_dates": [{"certification": "12A"}]}]
    }

MOVIE_SEARCH = {
    "amélie": [MOVIES[194], {"id": 77, "title": "Amelie Returns", "release_date": "2015-01-01"}],
    "odd codecs": [MOVIES[5001], MOVIES[5002]],
    "broadcast capture": [],
}

SHOWS = {
    999002: {
        "id": 999002,
        "name": "Test Show",
        "first_air_date": "2018-01-01",
        "overview": "A show about tests.",
        "poster_path": "/testshow.jpg",
        "genres": [{"name": "Comedy"}],
        "created_by": [{"name": "Creator"}],
    },
    555: {
        "id": 555,
        "name": "Another Show",
        "first_air_date": "2015-09-01",
        "overview": "Another one.",
        "poster_path": "/another.jpg",
        "genres": [],
    },
}
for sh in SHOWS.values():
    sh["credits"] = {"cast": [{"name": "Lead"}]}
    sh["content_ratings"] = {"results": [{"iso_3166_1": "GB", "rating": "15"}]}
SHOW_SEARCH = {"another show": [SHOWS[555]]}
SEASONS = {
    (999002, 1): [(1, "Pilot (TMDb)"), (2, "The Second One (TMDb)")],
    (999002, 2): [(1, "Season Two Opener")],
    (999002, 0): [(1, "Behind the Scenes (TMDb)")],
    (555, 1): [(1, "Double Part 1"), (2, "Double Part 2"), (3, "Third")],
}


class FakeTMDb:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_next = 0  # respond 500 this many times
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/3")
        self.calls.append(path)
        if self.down:
            raise httpx.ConnectError("offline")
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(500)
        q = (request.url.params.get("query") or "").lower()
        if path == "/search/movie":
            return httpx.Response(200, json={"results": MOVIE_SEARCH.get(q, [])})
        if path == "/search/tv":
            return httpx.Response(200, json={"results": SHOW_SEARCH.get(q, [])})
        parts = path.strip("/").split("/")
        if parts[0] == "movie" and int(parts[1]) in MOVIES:
            return httpx.Response(200, json=MOVIES[int(parts[1])])
        if parts[0] == "tv" and len(parts) == 2 and int(parts[1]) in SHOWS:
            return httpx.Response(200, json=SHOWS[int(parts[1])])
        if parts[0] == "tv" and len(parts) == 4:
            key = (int(parts[1]), int(parts[3]))
            if key in SEASONS:
                eps = [
                    {
                        "id": key[0] * 1000 + n,
                        "episode_number": n,
                        "name": name,
                        "overview": f"{name} overview",
                        "air_date": "2018-02-01",
                        "still_path": f"/still{n}.jpg",
                    }
                    for n, name in SEASONS[key]
                ]
                return httpx.Response(200, json={"episodes": eps})
        return httpx.Response(404, json={"status_message": "not found"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
