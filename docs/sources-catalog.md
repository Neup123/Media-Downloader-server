# Download sources catalog

Every source this system can download from, catalogued **by what the user must provide** — not by engine. The engine that handles a source is named in its entry, but the categories below are about the user's side of the bargain: nothing, an optional account, cookies, an account, a premium account, or a connected identity.

## How to read this catalog

- **One entry per source.** If a source works for free and an account merely unlocks more, it appears once, in the "account adds more" category, with the additions in parentheses after its name — never as two entries in two categories.
- **Cookies** live in the cookie vault (user settings → Advanced), once per site, and are reused by every engine. "Cookies required" means the source will not download without them.
- **"To verify at integration"** marks entries whose engine support or exact requirements are not certain yet. Nothing in this catalog claims a source works until its engine integration has verified it.
- **Background services:** sources served by pyLoad or cobalt additionally need that service enabled by an administrator (opt-in, "Enable with caution" in Advanced settings).
- Adult-only sources are not catalogued.

---

## 1. Free — nothing to provide

- [YouTube](https://www.youtube.com/) — yt-dlp. Public videos, playlists and channels.
- [Vimeo](https://vimeo.com/) — yt-dlp. Public videos.
- [Dailymotion](https://www.dailymotion.com/) — yt-dlp. Public videos.
- [Twitch](https://www.twitch.tv/) — yt-dlp (VODs), Streamlink (live). Past broadcasts and live streams.
- [Kick](https://kick.com/) — Streamlink. Live streams (plugin support to verify at integration).
- [SoundCloud](https://soundcloud.com/) — yt-dlp. Public tracks (Go+ catalogue excluded).
- [Bandcamp](https://bandcamp.com/) — yt-dlp. Freely streamable tracks and albums.
- [Internet Archive](https://archive.org/) — yt-dlp / direct download. Video, audio, books and files from the archive's collections.
- [Reddit](https://www.reddit.com/) — yt-dlp / gallery-dl. Public posts with video or image galleries.
- [Imgur](https://imgur.com/) — gallery-dl. Public images and albums.
- [DeviantArt](https://www.deviantart.com/) — gallery-dl. Public works and galleries.
- [Flickr](https://www.flickr.com/) — gallery-dl. Public photos.
- [Danbooru](https://danbooru.donmai.us/) — gallery-dl. Image-board posts and pools.
- [Gelbooru](https://gelbooru.com/) — gallery-dl. Image-board posts.
- [Wikimedia Commons](https://commons.wikimedia.org/) — gallery-dl. Freely licensed media files. *(Currently failing on the QA rig — tracked in #136; to verify at integration.)*
- [SVT Play](https://www.svtplay.se/) — svtplay-dl. Swedish public-service programmes (region rules of the service apply).
- [UR Play](https://urplay.se/) — svtplay-dl. Swedish educational programmes (to verify at integration).
- [TV4 Play](https://www.tv4play.se/) — svtplay-dl. Free tier programmes (to verify at integration).
- [DR TV](https://www.dr.dk/drtv) — svtplay-dl. Danish public-service programmes (to verify at integration).
- [NRK TV](https://tv.nrk.no/) — svtplay-dl. Norwegian public-service programmes (to verify at integration).
- [Yle Areena](https://areena.yle.fi/) — svtplay-dl. Finnish public-service programmes (to verify at integration).
- [Youku](https://www.youku.com/) — you-get. Public videos (to verify at integration).
- [AcFun](https://www.acfun.cn/) — you-get. Public videos (to verify at integration).
- [MangaDex](https://mangadex.org/) — get-sauce. Manga chapters, with CBZ packaging; further get-sauce sources to verify at integration.
- **Direct file links (any host)** — aria2 / generic. Any plain file URL: documents (PDF), archives (ZIP), disc images (ISO), direct media files.
- **Torrent and magnet links** — aria2. `.torrent` files and magnet links; seeding is disabled in the server configuration, so the instance never uploads.
- **Raw stream manifests (any host)** — N_m3u8DL-RE. A direct `.m3u8` (HLS) or `.mpd` (DASH) URL, downloaded and merged into one file.
- **Live streams (200+ services)** — Streamlink. Any live service covered by a Streamlink plugin, beyond the named entries above; per-service support to verify at integration.

## 2. Free — an account adds more

The account's cookies are provided once through the cookie vault; the additions are in parentheses.

- [YouTube](https://www.youtube.com/) (account adds: age-restricted and members-only videos, and reliable downloads past bot walls on server IPs) — yt-dlp, cobalt fallback.
- [Facebook](https://www.facebook.com/) (account adds: friends-only and otherwise non-public videos) — yt-dlp, cobalt fallback. Public videos are free.
- [X (Twitter)](https://x.com/) (account adds: sensitive / age-gated media; some content now requires a login — to verify at integration) — yt-dlp, cobalt fallback.
- [TikTok](https://www.tiktok.com/) (account adds: age- or region-restricted posts; downloads are watermark-free either way) — yt-dlp, cobalt fallback.
- [Vimeo](https://vimeo.com/) (account adds: private videos shared with the account) — yt-dlp. Public videos are free.
- [Nico Nico Douga](https://www.nicovideo.jp/) (account adds: members-only videos and higher quality) — yt-dlp / you-get.
- [Pixiv](https://www.pixiv.net/) (account adds: restricted works and full-size original files) — gallery-dl.
- [Crunchyroll](https://www.crunchyroll.com/) (premium account adds: the premium catalogue) — yt-dlp. The free, ad-supported catalogue is free.
- [Patreon](https://www.patreon.com/) (account with an active patronage adds: patron-only posts) — yt-dlp. Public posts are free.
- [Bilibili](https://www.bilibili.com/) (account adds: member quality 1080p and above; a VIP account adds 4K) — BBDown. Public videos are free at anonymous quality.
- [iQIYI](https://www.iqiyi.com/) (VIP account adds: VIP catalogue and quality) — you-get. Free catalogue is free (to verify at integration).
- [Tencent Video](https://v.qq.com/) (VIP account adds: VIP catalogue and quality) — you-get. Free catalogue is free (to verify at integration).
- [NetEase Cloud Music](https://music.163.com/) (account/VIP adds: licensed and higher-quality tracks) — musicdl (to verify at integration).
- [QQ Music](https://y.qq.com/) (VIP account adds: VIP-locked songs and quality) — musicdl (to verify at integration).
- [Kugou Music](https://www.kugou.com/) (account/VIP adds: locked tracks) — musicdl (to verify at integration).
- [Kuwo Music](https://www.kuwo.cn/) (account/VIP adds: locked tracks and lossless quality) — musicdl (to verify at integration).

### File hosters (via pyLoad — background service, admin opt-in)

Free downloading works with waiting times and speed caps; a premium account for that specific hoster (account adds: no waiting, full speed, larger files) is entered per hoster in settings.

- [Rapidgator](https://rapidgator.net/) — pyLoad (supported hosters to verify at integration against pyLoad's plugin list).
- [Nitroflare](https://nitroflare.com/) — pyLoad.
- [1fichier](https://1fichier.com/) — pyLoad.
- [Uploaded](https://uploaded.net/) — pyLoad.
- [Turbobit](https://turbobit.net/) — pyLoad.
- [Mega](https://en.wikipedia.org/wiki/Mega_(service)) (a free account adds: higher transfer quota) — pyLoad.

## 3. Cookies required

Nothing downloads from these sources until the site's cookies are in the vault (or provisioned by an admin).

- [Instagram](https://www.instagram.com/) — yt-dlp, cobalt fallback. A logged-in account's cookies are required for most content today; they unlock posts, reels, stories and full carousels at original quality. (An occasional public post may work without — to verify at integration.)
- [LinkedIn](https://www.linkedin.com/) — yt-dlp. Account cookies are required for most video downloads.

## 4. Account required

- [Pixiv FANBOX](https://www.fanbox.cc/) — gallery-dl. Supporter-only posts need an account that supports the creator.
- [Fantia](https://fantia.jp/) — gallery-dl. Paid-plan posts need an account with that plan (to verify at integration).

## 5. Premium account required

- [Nebula](https://nebula.tv/) — yt-dlp. Subscription-only streaming; a Nebula account is required for everything (to verify at integration).
- [SoundCloud Go+](https://soundcloud.com/) — yt-dlp. Go+ catalogue tracks require a Go+ subscription (to verify at integration); all other SoundCloud content is in category 1.

## 6. Connected identity required (Telegram)

- [Telegram](https://telegram.org/) — Telegram engine. Downloading anything from Telegram — public channels included — runs through a connected Telegram account: it requires the account's api_id + api_hash (a free developer registration) and a one-time login, after which the stored session does the downloading. Public channels: everything the account can view. Private chats and groups: only ones the connected account is a member of. The connection is either the user's own or one provisioned for the whole instance by an admin — in which case all downloads run under the admin's Telegram identity, with the rate-limit and ban risk on his account.
