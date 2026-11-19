// GENERATED from app/schemas.py by scripts/generate_media_preview_ts.py.
// Do not edit by hand; run python -m scripts.generate_media_preview_ts.

export interface PreviewMetadataOut {
  thumbnail: string | null;
  duration: number | null;
  timestamp: number | null;
  release_timestamp: number | null;
  upload_date: string | null;
  live_status: string | null;
  is_live: boolean | null;
}

export interface PreviewEntryOut {
  thumbnail: string | null;
  duration: number | null;
  timestamp: number | null;
  release_timestamp: number | null;
  upload_date: string | null;
  live_status: string | null;
  is_live: boolean | null;
  index: number;
  id: string;
  title: string;
  size_bytes?: number | null;
  size_estimate_bytes?: number | null;
}

export interface PreviewFormatOut {
  format_id: string;
  ext: string;
  resolution: string;
  vcodec: string;
  acodec: string;
  filesize: number | null;
}

export interface MediaPreviewOut {
  thumbnail: string | null;
  duration: number | null;
  timestamp: number | null;
  release_timestamp: number | null;
  upload_date: string | null;
  live_status: string | null;
  is_live: boolean | null;
  title: string;
  id: string;
  extractor: string;
  kind: 'playlist' | 'video';
  entry_count: number | null;
  entries: PreviewEntryOut[];
  formats: PreviewFormatOut[];
  subtitle_languages: string[];
  offset: number;
  max_entries: number;
  ordering: 'source';
  has_more: boolean;
  next_offset: number | null;
  size_bytes?: number | null;
  size_estimate_bytes?: number | null;
}

export interface TorrentManifestFileOut {
  index: number;
  path: string;
  name: string;
  size_bytes: number;
}

export interface TorrentManifestOut {
  file_count: number;
  files: TorrentManifestFileOut[];
}
