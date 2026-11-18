// GENERATED from app/engine_registry.py by scripts/generate_engine_registry_ts.py.
// Do not edit; run python -m scripts.generate_engine_registry_ts.

export const ENGINE_REGISTRY = {
  "yt-dlp": {
    "label": "yt-dlp",
    "role": "Site extraction and media downloads",
    "priority": "P0",
    "available": true,
    "title": "Video & audio sites",
    "purpose": "Videos, playlists, subtitles and audio from supported sites",
    "defaults": {
      "format": "bv*+ba/b",
      "subtitles_lang": "",
      "write_subtitles": false,
      "embed_subtitles": false,
      "extra_args": [],
      "allow_playlist": false,
      "playlist_items": ""
    }
  },
  "aria2c": {
    "label": "aria2c",
    "role": "Direct downloads, BitTorrent, and magnet links",
    "priority": "P0",
    "available": true,
    "title": "Large files & torrents",
    "purpose": "Direct files, magnets, FTP and torrent downloads",
    "defaults": {
      "options": {
        "max-connection-per-server": "4",
        "split": "4",
        "max-tries": "5",
        "retry-wait": "5"
      }
    }
  },
  "gallery-dl": {
    "label": "gallery-dl",
    "role": "Image galleries and supported image-board extractors",
    "priority": "P0",
    "available": true,
    "title": "Image galleries",
    "purpose": "Image collections from supported gallery websites",
    "defaults": {
      "options": {},
      "cookies_file": "",
      "proxy": ""
    }
  },
  "generic": {
    "label": "generic / curl",
    "role": "Plain direct HTTP(S) downloads",
    "priority": "P1",
    "available": true,
    "title": "Direct file downloads",
    "purpose": "Files from public HTTP and HTTPS links",
    "defaults": {
      "filename": "",
      "follow_redirects": false,
      "max_redirects": 5,
      "retry_count": 2,
      "user_agent": ""
    }
  },
  "you-get": {
    "label": "you-get",
    "role": "Fallback site extractor",
    "priority": "P1",
    "available": true,
    "title": "Alternative video sites",
    "purpose": "Fallback downloader for supported videos and audio",
    "defaults": {
      "format": "",
      "cookies_file": "",
      "no_proxy": false
    }
  },
  "svtplay-dl": {
    "label": "svtplay-dl",
    "role": "Nordic broadcaster and video services",
    "priority": "P2",
    "available": true,
    "title": "Nordic television",
    "purpose": "Video and subtitles from Nordic broadcasting sites",
    "defaults": {
      "resolution": "",
      "quality": "",
      "subtitles": false,
      "all_subtitles": false,
      "all_episodes": false,
      "output_format": "mp4"
    }
  },
  "get-sauce": {
    "label": "get-sauce",
    "role": "Specialized media and gallery extractor",
    "priority": "P2",
    "available": true,
    "title": "Specialized media",
    "purpose": "Specialized media and gallery download sources",
    "defaults": {
      "stream_index": 0,
      "amount": 0,
      "workers": 4,
      "merge": "default",
      "pages": "",
      "subdirectory": false,
      "timeout_minutes": 15
    }
  }
} as const;

export type Engine = keyof typeof ENGINE_REGISTRY;
export type EngineSelection = 'auto' | Engine;
export const ENGINE_ORDER = Object.keys(ENGINE_REGISTRY) as Engine[];
export const ENGINE_DESCRIPTIONS: Record<Engine, {title: string; purpose: string}> = Object.fromEntries(ENGINE_ORDER.map(engine => [engine, {title: ENGINE_REGISTRY[engine].title, purpose: ENGINE_REGISTRY[engine].purpose}])) as Record<Engine, {title: string; purpose: string}>;
