import { useCallback, useEffect, useMemo, useState } from 'react';

type Engine = 'yt-dlp' | 'aria2c';
type Job = {
  id: string; url: string; engine: Engine; status: string;
  progress: Record<string, unknown>; output_files: { filename: string; size_bytes: number; verified: boolean }[];
  log_text: string; error?: string | null; created_at: string;
};
type YtSettings = {
  format: string; subtitles_lang: string; write_subtitles: boolean; embed_subtitles: boolean;
  extra_args: string[]; cookies_file?: string;
};
type AriaSettings = { options: Record<string, string> };

const API = '/api/v1';
const ytDefaults: YtSettings = {
  format: 'bv*+ba/b', subtitles_lang: '', write_subtitles: false,
  embed_subtitles: false, extra_args: [],
};
const ariaDefaults: AriaSettings = {
  options: { 'max-connection-per-server': '4', split: '4', 'max-tries': '5', 'retry-wait': '5' },
};

function bytes(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  if (value < 1024) return value + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let amount = value / 1024, index = 0;
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
  return amount.toFixed(1) + ' ' + units[index];
}

function label(value: string): string {
  return value.replaceAll('-', ' ').replace(/^./, character => character.toUpperCase());
}

export default function App() {
  const [apiKey, setApiKey] = useState(() => localStorage.getItem('downloader-api-key') ?? '');
  const [tab, setTab] = useState<'downloads' | 'engines' | 'delivery'>('downloads');
  const [engine, setEngine] = useState<Engine>('yt-dlp');
  const [url, setUrl] = useState('');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [yt, setYt] = useState<YtSettings>(ytDefaults);
  const [ytOverride, setYtOverride] = useState<YtSettings>(ytDefaults);
  const [aria, setAria] = useState<AriaSettings>(ariaDefaults);
  const [ariaOverride, setAriaOverride] = useState<AriaSettings>(ariaDefaults);
  const [cookies, setCookies] = useState<{ filename: string; size_bytes: number }[]>([]);
  const [mode, setMode] = useState<'local' | 'webdav'>('local');
  const [davUrl, setDavUrl] = useState('');
  const [davUser, setDavUser] = useState('');
  const [davPassword, setDavPassword] = useState('');
  const [davFolder, setDavFolder] = useState('Downloader');
  const [passwordConfigured, setPasswordConfigured] = useState(false);

  const headers = useMemo(() => ({ 'Content-Type': 'application/json', 'X-API-Key': apiKey }), [apiKey]);
  const request = useCallback(async (path: string, init: RequestInit = {}) => {
    if (!apiKey.trim()) throw new Error('Enter the API key to connect.');
    const response = await fetch(path, { ...init, headers: { ...headers, ...(init.headers ?? {}) } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Request failed (' + response.status + ')');
    return data;
  }, [apiKey, headers]);

  const refresh = useCallback(async () => {
    try {
      setJobs(await request(API + '/jobs?limit=80'));
    } catch (error) {
      if (apiKey) setMessage(error instanceof Error ? error.message : 'Could not load jobs');
    }
  }, [apiKey, request]);

  useEffect(() => {
    if (!apiKey) return;
    localStorage.setItem('downloader-api-key', apiKey);
    void refresh();
    const interval = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(interval);
  }, [apiKey, refresh]);

  useEffect(() => {
    if (!apiKey) return;
    void request(API + '/engines/yt-dlp/settings').then(data => {
      const saved = { ...ytDefaults, ...data.settings };
      setYt(saved); setYtOverride(saved);
    }).catch(error => setMessage(String(error)));
    void request(API + '/engines/aria2c/settings').then(data => {
      const saved = { ...ariaDefaults, ...data.settings };
      setAria(saved); setAriaOverride(saved);
    }).catch(error => setMessage(String(error)));
    void request(API + '/cookies').then(setCookies).catch(error => setMessage(String(error)));
  }, [apiKey, request]);

  useEffect(() => {
    if (!apiKey || tab !== 'delivery') return;
    void request(API + '/delivery').then(data => {
      setMode(data.mode); setDavUrl(data.webdav_url ?? '');
      setDavUser(data.username ?? ''); setDavFolder(data.folder ?? 'Downloader');
      setPasswordConfigured(Boolean(data.password_configured));
    }).catch(error => setMessage(String(error)));
  }, [apiKey, tab, request]);

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true);
    try {
      const overrides = engine === 'yt-dlp' ? ytOverride : ariaOverride;
      const job = await request(API + '/jobs', {
        method: 'POST', body: JSON.stringify({ url, engine, overrides }),
      });
      setSelected(job.id); setUrl(''); setMessage('Job queued.'); await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not submit job');
    } finally { setBusy(false); }
  }

  async function saveEngine(name: Engine) {
    try {
      const settings = name === 'yt-dlp' ? yt : aria;
      await request(API + '/engines/' + name + '/settings', {
        method: 'PUT', body: JSON.stringify({ settings }),
      });
      if (name === 'yt-dlp') setYtOverride(settings); else setAriaOverride(settings);
      setMessage(name + ' defaults saved.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save settings');
    }
  }

  async function uploadCookie(file: File) {
    if (file.size > 1_000_000) { setMessage('Cookie file must be no larger than 1 MB.'); return; }
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error('Could not read cookie file'));
        reader.readAsDataURL(file);
      });
      await request(API + '/cookies/' + encodeURIComponent(file.name), {
        method: 'PUT', body: JSON.stringify({ content_base64: dataUrl.split(',', 2)[1] }),
      });
      setCookies(await request(API + '/cookies'));
      setMessage('Cookie file uploaded.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Cookie upload failed');
    }
  }

  async function jobAction(id: string, action: 'cancel' | 'retry') {
    try { await request(API + '/jobs/' + id + '/' + action, { method: 'POST' }); await refresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Job action failed'); }
  }

  async function saveDelivery(event: React.FormEvent) {
    event.preventDefault();
    try {
      const data = await request(API + '/delivery', {
        method: 'PUT',
        body: JSON.stringify({
          mode, webdav_url: davUrl || null, username: davUser || null,
          app_password: davPassword || null, folder: davFolder,
        }),
      });
      setPasswordConfigured(Boolean(data.password_configured));
      setDavPassword(''); setMessage('Delivery settings saved.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save delivery settings');
    }
  }

  const active = jobs.find(job => job.id === selected) ?? null;

  return <div className="shell">
    <header className="topbar">
      <div className="brandmark">D</div>
      <div><div className="brand">Downloader Server</div><div className="subbrand">Server-side media downloads</div></div>
      <div className="top-actions"><a href="/api-reference" target="_blank" rel="noreferrer">API reference</a></div>
    </header>

    <section className="connection panel">
      <div><div className="eyebrow">SERVICE CONNECTION</div><h2>Connect to your downloader</h2><p>Enter the server API key.</p></div>
      <div className="keyrow">
        <input type="password" value={apiKey} onChange={event => setApiKey(event.target.value)} placeholder="API key" autoComplete="current-password" />
        <button className="secondary" onClick={() => { localStorage.removeItem('downloader-api-key'); setApiKey(''); setJobs([]); }}>Disconnect</button>
      </div>
    </section>
    {message && <div className="notice" role="status">{message}</div>}

    <nav className="tabs">
      <button className={tab === 'downloads' ? 'active' : ''} onClick={() => setTab('downloads')}>Downloads</button>
      <button className={tab === 'engines' ? 'active' : ''} onClick={() => setTab('engines')}>Engine settings</button>
      <button className={tab === 'delivery' ? 'active' : ''} onClick={() => setTab('delivery')}>Delivery</button>
    </nav>

    {tab === 'downloads' && <div className="layout">
      <section className="panel submit-panel">
        <div className="eyebrow">NEW DOWNLOAD</div><h2>Submit a link</h2>
        <form onSubmit={submit}>
          <label>Media URL<input type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://" required /></label>
          <label>Engine<select value={engine} onChange={event => setEngine(event.target.value as Engine)}>
            <option value="yt-dlp">yt-dlp — websites and media</option><option value="aria2c">aria2c — direct, torrent, magnet</option>
          </select></label>
          {engine === 'yt-dlp' ? <div className="options-block">
            <div className="field-title">Per-download overrides</div>
            <label>Format<input value={ytOverride.format} onChange={event => setYtOverride(value => ({ ...value, format: event.target.value }))} /></label>
            <label>Subtitle languages<input value={ytOverride.subtitles_lang} onChange={event => setYtOverride(value => ({ ...value, subtitles_lang: event.target.value }))} /></label>
            <label>Cookie file<select value={ytOverride.cookies_file ?? ''} onChange={event => setYtOverride(value => ({ ...value, cookies_file: event.target.value }))}>
              <option value="">No cookie file</option>{cookies.map(file => <option key={file.filename} value={file.filename}>{file.filename}</option>)}
            </select></label>
            <div className="checks">
              <label className="check"><input type="checkbox" checked={ytOverride.write_subtitles} onChange={event => setYtOverride(value => ({ ...value, write_subtitles: event.target.checked }))} /> Download subtitles</label>
              <label className="check"><input type="checkbox" checked={ytOverride.embed_subtitles} onChange={event => setYtOverride(value => ({ ...value, embed_subtitles: event.target.checked }))} /> Embed subtitles</label>
            </div>
          </div> : <div className="options-block">
            <div className="field-title">Per-download overrides</div>
            {['max-connection-per-server', 'split'].map(option => <label key={option}>{option}<input type="number" min="1" max="16" value={ariaOverride.options[option] ?? '4'} onChange={event => setAriaOverride(value => ({ ...value, options: { ...value.options, [option]: event.target.value } }))} /></label>)}
          </div>}
          <button className="primary" disabled={busy || !apiKey || !url.trim()}>{busy ? 'Adding…' : 'Add to queue'}</button>
        </form>
      </section>

      <section className="panel queue-panel">
        <div className="section-heading"><div><div className="eyebrow">JOB QUEUE</div><h2>Recent downloads</h2></div><span className="count">{jobs.length} jobs</span></div>
        {!jobs.length ? <div className="empty">No jobs yet. Submit a link to start.</div> : <div className="job-list">
          {jobs.map(job => {
            const percent = typeof job.progress.percent === 'number' ? job.progress.percent : (job.status === 'completed' ? 100 : 0);
            return <article key={job.id} className={'job ' + (selected === job.id ? 'selected' : '')} onClick={() => setSelected(job.id)}>
              <div className="job-head"><div className="job-title"><span className="engine-pill">{job.engine}</span><strong title={job.url}>{job.url}</strong></div><span className={'status status-' + job.status}>{label(job.status)}</span></div>
              <div className="progress-track"><div className="progress-fill" style={{ width: percent + '%' }} /></div>
              <div className="job-meta"><span>{percent ? percent.toFixed(1) + '%' : 'Progress pending'}</span><span>{bytes(job.progress.downloaded_bytes)} / {bytes(job.progress.total_bytes)}</span><span>{bytes(job.progress.speed_bytes_per_second)}/s</span></div>
              {selected === job.id && <div className="job-actions" onClick={event => event.stopPropagation()}>
                {['queued', 'starting', 'extracting', 'downloading', 'post-processing', 'delivering'].includes(job.status) && <button className="small danger" onClick={() => void jobAction(job.id, 'cancel')}>Cancel</button>}
                {['failed', 'cancelled'].includes(job.status) && <button className="small secondary" onClick={() => void jobAction(job.id, 'retry')}>Retry</button>}
              </div>}
            </article>;
          })}
        </div>}
      </section>

      <section className="panel details-panel">
        <div className="eyebrow">JOB DETAILS</div><h2>{active ? 'Selected job' : 'Select a job'}</h2>
        {active ? <>
          <div className="detail-grid"><span>Engine</span><strong>{active.engine}</strong><span>Status</span><strong>{label(active.status)}</strong><span>Job ID</span><code>{active.id}</code></div>
          {active.error && <div className="error-box">{active.error}</div>}
          {active.output_files?.length > 0 && <div className="outputs"><div className="field-title">Delivered files</div>{active.output_files.map((file, index) => <div key={index} className="output-row"><strong>{file.filename}</strong><span>{bytes(file.size_bytes)}</span><span className="verified">{file.verified ? 'Verified' : 'Unverified'}</span></div>)}</div>}
          <div className="field-title log-title">Job log</div><pre className="log">{active.log_text || 'No log output yet.'}</pre>
        </> : <p className="muted">Progress, output files, and logs appear here.</p>}
      </section>
    </div>}

    {tab === 'engines' && <div className="settings-layout">
      <section className="panel">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>yt-dlp</h2><p className="muted">Global defaults apply to new jobs; per-download options take precedence.</p>
        <label>Format selector<input value={yt.format} onChange={event => setYt(value => ({ ...value, format: event.target.value }))} /></label>
        <label>Subtitle languages<input value={yt.subtitles_lang} onChange={event => setYt(value => ({ ...value, subtitles_lang: event.target.value }))} /></label>
        <label>Default cookie file<select value={yt.cookies_file ?? ''} onChange={event => setYt(value => ({ ...value, cookies_file: event.target.value }))}><option value="">None</option>{cookies.map(file => <option key={file.filename} value={file.filename}>{file.filename}</option>)}</select></label>
        <label>Upload browser-exported cookies<input type="file" accept=".txt,.cookies,text/plain" onChange={event => { const file = event.target.files?.[0]; if (file) void uploadCookie(file); event.target.value = ''; }} /></label>
        <div className="muted">Cookie files remain in the private service data volume.</div>
        <div className="checks">
          <label className="check"><input type="checkbox" checked={yt.write_subtitles} onChange={event => setYt(value => ({ ...value, write_subtitles: event.target.checked }))} /> Download subtitles by default</label>
          <label className="check"><input type="checkbox" checked={yt.embed_subtitles} onChange={event => setYt(value => ({ ...value, embed_subtitles: event.target.checked }))} /> Embed subtitles by default</label>
        </div>
        <label>Additional arguments (one per line)<textarea rows={4} value={yt.extra_args.join('\n')} onChange={event => setYt(value => ({ ...value, extra_args: event.target.value.split('\n').map(item => item.trim()).filter(Boolean) }))} /></label>
        <button className="primary" onClick={() => void saveEngine('yt-dlp')}>Save yt-dlp defaults</button>
      </section>
      <section className="panel">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>aria2c</h2><p className="muted">These values map to aria2 options. RPC access and output paths are service-managed.</p>
        {Object.entries(aria.options).map(([key, value]) => <label key={key}>{key}<input value={value} onChange={event => setAria(current => ({ ...current, options: { ...current.options, [key]: event.target.value } }))} /></label>)}
        <button className="secondary" onClick={() => setAria(ariaDefaults)}>Reset shown defaults</button>
        <button className="primary" onClick={() => void saveEngine('aria2c')}>Save aria2c defaults</button>
      </section>
      <div className="hint panel"><strong>Advanced options</strong><p>Service-managed paths, cookie arguments, progress reporting, and process execution flags cannot be overridden.</p></div>
    </div>}

    {tab === 'delivery' && <section className="panel delivery-panel">
      <div className="eyebrow">OUTPUT DESTINATION</div><h2>Delivery settings</h2>
      <p className="muted">Local directory or generic WebDAV. WebDAV uploads are checked against the remote file size.</p>
      <form onSubmit={saveDelivery}>
        <label>Delivery mode<select value={mode} onChange={event => setMode(event.target.value as 'local' | 'webdav')}><option value="local">Local output directory</option><option value="webdav">WebDAV (Nextcloud or other server)</option></select></label>
        {mode === 'webdav' && <>
          <label>WebDAV base URL<input type="url" value={davUrl} onChange={event => setDavUrl(event.target.value)} placeholder="https://cloud.example.com/remote.php/dav/files/username" required /></label>
          <label>Username<input value={davUser} onChange={event => setDavUser(event.target.value)} required /></label>
          <label>App password<input type="password" value={davPassword} onChange={event => setDavPassword(event.target.value)} placeholder={passwordConfigured ? 'Stored; leave blank to keep' : 'Enter app password'} autoComplete="new-password" /></label>
          <label>Destination folder<input value={davFolder} onChange={event => setDavFolder(event.target.value)} /></label>
          <div className="secure-note">{passwordConfigured ? 'An app password is stored encrypted on the server.' : 'An app password is required.'} Credentials are never returned to the browser.</div>
        </>}
        <button className="primary">Save delivery settings</button>
      </form>
    </section>}

    <footer>Downloader Server · standalone service · <a href="/api-reference" target="_blank" rel="noreferrer">API</a></footer>
  </div>;
}
