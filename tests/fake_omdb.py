"""A tiny in-process OMDb for tests (httpx.MockTransport), with OMDb's quirks:
errors inside 200 responses, "N/A" values, year ranges for series."""

from __future__ import annotations

import httpx

TITLES = {
    "tt0211915": {"Title": "Amélie", "Year": "2001", "Rated": "R", "Genre": "Comedy, Romance",
                  "Director": "Jean-Pierre Jeunet", "Writer": "Guillaume Laurant",
                  "Actors": "Audrey Tautou, Mathieu Kassovitz", "Plot": "OMDb plot for Amélie.",
                  "Poster": "https://m.media-amazon.com/images/amelie.jpg", "imdbRating": "8.3",
                  "imdbID": "tt0211915", "Type": "movie"},
    "tt1000001": {"Title": "Broadcast Capture", "Year": "2010", "Rated": "N/A", "Genre": "Drama",
                  "Director": "N/A", "Writer": "N/A", "Actors": "N/A", "Plot": "Recorded off air.",
                  "Poster": "N/A", "imdbRating": "N/A", "imdbID": "tt1000001", "Type": "movie"},
    "tt5000001": {"Title": "Odd Codecs", "Year": "2020", "Plot": "One.", "imdbID": "tt5000001",
                  "Type": "movie", "Poster": "N/A"},
    "tt5000002": {"Title": "Odd Codecs", "Year": "2020", "Plot": "Two.", "imdbID": "tt5000002",
                  "Type": "movie", "Poster": "N/A"},
    "tt0555555": {"Title": "Another Show", "Year": "2015–2018", "Rated": "TV-14",
                  "Genre": "Comedy", "Director": "N/A", "Writer": "Show Creator",
                  "Actors": "Lead Actor", "Plot": "OMDb plot for Another Show.",
                  "Poster": "https://m.media-amazon.com/images/another.jpg", "imdbRating": "7.0",
                  "imdbID": "tt0555555", "Type": "series"},
}  # fmt: skip
SEARCH = {
    ("movie", "amélie"): ["tt0211915"],
    ("movie", "broadcast capture"): ["tt1000001"],
    ("movie", "odd codecs"): ["tt5000001", "tt5000002"],
    ("series", "another show"): ["tt0555555"],
}
SEASONS = {("tt0555555", "1"): [("1", "Pilot Part 1"), ("2", "Pilot Part 2"), ("3", "Third")]}


class FakeOMDb:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.limit_after: int | None = None  # answer "Request limit reached!" after N calls

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        self.calls.append(q)
        if q.get("apikey") != "omdb-key":
            return httpx.Response(401, json={"Response": "False", "Error": "Invalid API key!"})
        if self.limit_after is not None and len(self.calls) > self.limit_after:
            return httpx.Response(200, json={"Response": "False",
                                             "Error": "Request limit reached!"})  # fmt: skip
        if "s" in q:
            ids = SEARCH.get((q.get("type", "movie"), q["s"].lower()), [])
            hits = [TITLES[i] for i in ids if not q.get("y") or TITLES[i]["Year"][:4] == q["y"]]
            if not hits:
                return httpx.Response(200, json={"Response": "False", "Error": "Movie not found!"})
            brief = [{k: h[k] for k in ("Title", "Year", "imdbID", "Type", "Poster")} for h in hits]
            return httpx.Response(200, json={"Search": brief, "totalResults": str(len(brief)),
                                             "Response": "True"})  # fmt: skip
        if "Season" in q:
            eps = SEASONS.get((q["i"], q["Season"]))
            if eps is None:
                return httpx.Response(
                    200, json={"Response": "False", "Error": "Series or season not found!"}
                )
            return httpx.Response(200, json={"Response": "True", "Episodes": [
                {"Title": t, "Released": "2015-09-0" + n, "Episode": n, "imdbRating": "7.5",
                 "imdbID": f"tt09{n}"} for n, t in eps]})  # fmt: skip
        if "i" in q and q["i"] in TITLES:
            return httpx.Response(200, json={**TITLES[q["i"]], "Response": "True"})
        return httpx.Response(200, json={"Response": "False", "Error": "Incorrect IMDb ID."})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
