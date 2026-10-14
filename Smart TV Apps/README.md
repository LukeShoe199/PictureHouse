# Smart TV apps

Apps that put PictureHouse on a TV, with an interface made for the remote
control. Any TV can already play from PictureHouse over DLNA, in the TV's
own media player, with nothing installed. These apps add posters,
Continue watching, music with a visualiser and photo slideshows.

| TV | Folder | Status |
|---|---|---|
| LG (webOS, 2018 and later) | [`LG webOS/`](LG%20webOS) | Ready. Install guide in its README. |
| Samsung (Tizen) | — | Planned |

## How they work

Each app is a small launcher. It finds PictureHouse on the home network
(`GET /api/hello` on port 9595) and opens the TV interface that PictureHouse
itself serves at `/tv/` (`src/picturehouse/web/static/tv/`). So:

- the TV interface is shared by every TV app and updates along with
  PictureHouse;
- a new TV platform needs only a launcher and packaging, plus checks that
  the `/tv/` pages run in that TV's browser engine.
