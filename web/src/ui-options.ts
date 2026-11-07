/** User-facing yt-dlp quality presets. Custom selectors remain advanced. */
export const VIDEO_QUALITY = [
  {id: 'best', label: 'Best quality', format: 'bv*+ba/b'},
  {id: '1080', label: 'Up to 1080p', format: 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'},
  {id: '720', label: 'Up to 720p', format: 'bestvideo[height<=720]+bestaudio/best[height<=720]'},
  {id: 'audio', label: 'Audio only', format: 'bestaudio/best'},
] as const;
export function qualityFromFormat(format: string): string {
  return VIDEO_QUALITY.find(entry => entry.format === format)?.id ?? 'custom';
}
export function formatFromQuality(value: string): string | null {
  return VIDEO_QUALITY.find(entry => entry.id === value)?.format ?? null;
}
export function validPresetSettings(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
