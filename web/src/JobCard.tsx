import type { ReactNode } from 'react';

export type JobCardData = {
  id: string;
  url: string;
  engine: string;
  status: string;
  progress?: Record<string, unknown>;
  output_files?: { filename: string; size_bytes: number; verified: boolean; path?: string }[];
  log_text?: string;
  error?: string | null;
  created_at?: string;
};
type Operation = 'cancel' | 'retry' | 'redeliver';
type Props = { job: JobCardData; expanded: boolean; onToggle: () => void; onAction: (action: Operation) => void };

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
function amount(input: unknown): string {
  const n = Number(input);
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n < 1024) return Math.round(n) + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = n, unit = -1;
  do { value /= 1024; unit++; } while (value >= 1024 && unit < units.length - 1);
  return value.toFixed(value < 10 ? 1 : 0) + ' ' + units[unit];
}
export function jobTitle(job: JobCardData): string {
  if (job.output_files?.[0]?.filename) return job.output_files[0].filename;
  try {
    const url = new URL(job.url);
    const last = decodeURIComponent(url.pathname.split('/').filter(Boolean).pop() || '');
    return last || url.hostname || 'Download';
  } catch {
    return 'Download';
  }
}
export function filesLocation(job: JobCardData): string | null {
  const file = job.output_files?.find(item => item.verified && typeof item.path === 'string');
  if (!file?.path) return null;
  try {
    const u = new URL(file.path);
    if (u.protocol !== 'https:' || u.username || u.password) return null;
    const marker = '/remote.php/dav/files/';
    const index = u.pathname.indexOf(marker);
    if (index < 0) return null;
    const relative = u.pathname.slice(index + marker.length).split('/');
    if (relative.length < 2) return null;
    const dir = '/' + relative.slice(1, -1).map(decodeURIComponent).join('/');
    return u.origin + u.pathname.slice(0, index) + '/apps/files/files?dir=' + encodeURIComponent(dir);
  } catch { return null; }
}
function symbol(job: JobCardData): string {
  if (job.engine === 'gallery-dl' || job.engine === 'get-sauce') return '▧';
  if (/\.(mp3|ogg|wav|flac)(?:$|\?)/i.test(job.url)) return '♫';
  if (/\.(mp4|mkv|webm)(?:$|\?)/i.test(job.url) || job.engine === 'yt-dlp') return '▶';
  return '↓';
}
export function JobCard({job, expanded, onToggle, onAction}: Props) {
  const percentage = Number(job.progress?.percent);
  const percent = Number.isFinite(percentage) ? Math.max(0, Math.min(100, percentage)) : null;
  const verified = Boolean(job.output_files?.length) && job.output_files!.every(file => file.verified);
  const fileLink = job.status === 'completed' && verified ? filesLocation(job) : null;
  const actionButton = (label: string, action: Operation, style = 'secondary'): ReactNode =>
    <button type="button" className={'small ' + style} onClick={() => onAction(action)}>{label}</button>;
  const progressText = job.status === 'completed'
    ? verified ? 'Delivered ✓ Verified' : 'Completed — checking delivery'
    : job.status === 'failed' ? 'Failed — ' + (job.error || 'Check the log and try again')
    : job.status === 'cancelled' ? 'Cancelled'
    : job.status === 'downloading'
      ? 'Downloading' + (percent === null ? '' : ' ' + percent.toFixed(0) + '%') +
        (Number(job.progress?.speed_bytes_per_second) > 0
          ? ' · ' + amount(job.progress?.speed_bytes_per_second) + '/s' : '')
      : job.status === 'delivering' ? 'Delivering to your destination'
      : job.status === 'queued' ? 'Queued' : job.status.replace(/-/g, ' ');
  return <article className={'job-card job-' + job.status}>
    <div className="job-card-head">
      <span className="job-card-icon" aria-hidden="true">{symbol(job)}</span>
      <div className="job-card-info">
        <button type="button" className="job-card-title" onClick={onToggle}
          aria-expanded={expanded} aria-label={'Details for ' + jobTitle(job)}>{jobTitle(job)}</button>
        <div className="job-card-state">{progressText}</div>
        <div className="job-card-meta">{job.engine}
          {!TERMINAL.has(job.status) && job.progress?.downloaded_bytes !== undefined &&
            <span> · {amount(job.progress.downloaded_bytes)} downloaded</span>}
        </div>
      </div>
    </div>
    {!TERMINAL.has(job.status) && <div className="progress-track" role="progressbar"
      aria-label={'Download progress for ' + jobTitle(job)}
      aria-valuenow={percent ?? undefined} aria-valuemin={0} aria-valuemax={100}>
      <div className="progress-fill" style={{width: (percent ?? 0) + '%'}} /></div>}
    <div className="job-actions">
      {!TERMINAL.has(job.status) && actionButton('Cancel', 'cancel', 'danger')}
      {(job.status === 'failed' || job.status === 'cancelled') && actionButton('Try again', 'retry')}
      {job.status === 'completed' && actionButton('Re-deliver', 'redeliver')}
      {fileLink && <a className="small secondary job-card-file-link" href={fileLink}
        target="_blank" rel="noopener noreferrer">Open in Files</a>}
      <button type="button" className="small secondary" onClick={onToggle}
        aria-expanded={expanded}>{expanded ? 'Hide details' : 'Details'}</button>
    </div>
    {expanded && <div className="job-card-details">
      {!!job.output_files?.length && <div className="outputs">
        <strong>Delivered files</strong>
        {job.output_files.map((file, index) => <div className="output-row" key={index}>
          <span>{file.filename}</span><span>{amount(file.size_bytes)}</span>
          <span>{file.verified ? '✓ Verified' : 'Not verified'}</span>
        </div>)}
      </div>}
      {job.error && <p className="error-box">{job.error}</p>}
      <details><summary>Technical log (advanced)</summary>
        <pre className="log">{job.log_text || 'No log output yet.'}</pre>
      </details>
    </div>}
  </article>;
}
