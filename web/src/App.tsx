import { useCallback, useEffect, useMemo, useState } from 'react';

type Engine = 'yt-dlp' | 'aria2c' | 'gallery-dl' | 'generic';
type EngineSelection = 'auto' | Engine;
type Preset = { id: string; name: string; engine: Engine; settings: Record<string, unknown>; description: string };
type EngineRule = { id: string; host_suffix: string; path_prefix: string; engine: Engine; priority: number; enabled: boolean; description: string };
type Job = {
  id: string; url: string; engine: Engine; status: string;
  progress: Record<string, unknown>; output_files: { filename: string; size_bytes: number; verified: boolean }[];
  log_text: string; error?: string | null; created_at: string;
};
type YtSettings = {
  format: string; subtitles_lang: string; write_subtitles: boolean; embed_subtitles: boolean;
  extra_args: string[]; cookies_file?: string; allow_playlist: boolean; playlist_items: string;
};
type MediaPreview = {
  title: string; kind: 'video' | 'playlist'; entry_count: number;
  entries: { index: number; title: string; id: string; duration: number | null }[];
  formats: { format_id: string; ext: string; resolution: string; filesize: number | null }[];
};
type AriaSettings = { options: Record<string, string> };
type GallerySettings = { options: Record<string, string>; cookies_file?: string; proxy?: string };
type GenericSettings = { filename: string; follow_redirects: boolean; max_redirects: number; retry_count: number; user_agent: string };
type OptionField = { key: string; label: string; category: string; type: string; description?: string; minimum?: number; maximum?: number };
type EngineStatus = { name: Engine; installed: boolean; version?: string | null };

function CommonOptionFields({ fields, values, onChange }: {
  fields: OptionField[]; values: Record<string, string>; onChange: (key: string, value: string) => void;
}) {
  const groups = Array.from(new Set(fields.map(field => field.category)));
  return <div className="common-options">
    {groups.map(category => <div className="option-category" key={category}>
      <div className="field-title">{category}</div>
      {fields.filter(field => field.category === category).map(field => <label key={field.key} title={field.description ?? ''}>
        {field.label}
        {field.type === 'boolean'
          ? <select value={values[field.key] ?? ''} onChange={event => onChange(field.key, event.target.value)}>
              <option value="">Engine default</option><option value="true">Enabled</option><option value="false">Disabled</option>
            </select>
          : <input type={field.type === 'integer' ? 'number' : 'text'}
              min={field.minimum} max={field.maximum}
              value={values[field.key] ?? ''}
              placeholder="Use engine default"
              onChange={event => onChange(field.key, event.target.value)} />}
        {field.description && <span className="muted option-help">{field.description}</span>}
      </label>)}
    </div>)}
  </div>;
}

const API = '/api/v1';
const ytDefaults: YtSettings = {
  format: 'bv*+ba/b', subtitles_lang: '', write_subtitles: false,
  embed_subtitles: false, extra_args: [], allow_playlist: false, playlist_items: '',
};
const ariaDefaults: AriaSettings = {
  options: { 'max-connection-per-server': '4', split: '4', 'max-tries': '5', 'retry-wait': '5' },
};
const galleryDefaults: GallerySettings = { options: {}, cookies_file: '', proxy: '' };
const genericDefaults: GenericSettings = { filename: '', follow_redirects: false, max_redirects: 5, retry_count: 2, user_agent: '' };

function parseOptionsJson(value: string): Record<string, string> {
  const parsed = parseJsonObject(value);
  return parsed as Record<string, string>;
}

function parseJsonObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Settings must be a JSON object.');
  }
  return parsed as Record<string, unknown>;
}

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
  const [tab, setTab] = useState<'downloads' | 'engines' | 'routing' | 'delivery'>('downloads');
  const [engine, setEngine] = useState<EngineSelection>('auto');
  const [selectedPresetId, setSelectedPresetId] = useState('');
  const [presets, setPresets] = useState<Preset[]>([]);
  const [engineRules, setEngineRules] = useState<EngineRule[]>([]);
  const [batchUrls, setBatchUrls] = useState('');
  const [url, setUrl] = useState('');
  const [mediaPreview, setMediaPreview] = useState<MediaPreview | null>(null);
  const [selectedPlaylistEntries, setSelectedPlaylistEntries] = useState<number[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [yt, setYt] = useState<YtSettings>(ytDefaults);
  const [ytOverride, setYtOverride] = useState<YtSettings>(ytDefaults);
  const [aria, setAria] = useState<AriaSettings>(ariaDefaults);
  const [ariaOverride, setAriaOverride] = useState<AriaSettings>(ariaDefaults);
  const [gallery, setGallery] = useState<GallerySettings>(galleryDefaults);
  const [galleryOverride, setGalleryOverride] = useState<GallerySettings>(galleryDefaults);
  const [generic, setGeneric] = useState<GenericSettings>(genericDefaults);
  const [genericOverride, setGenericOverride] = useState<GenericSettings>(genericDefaults);
  const [optionCatalog, setOptionCatalog] = useState<Record<Engine, OptionField[]>>({ 'yt-dlp': [], aria2c: [], 'gallery-dl': [], generic: [] });
  const [engineStatus, setEngineStatus] = useState<EngineStatus[]>([]);
  const [galleryOptionsText, setGalleryOptionsText] = useState('{}');
  const [galleryOverrideOptionsText, setGalleryOverrideOptionsText] = useState('{}');
  const [cookies, setCookies] = useState<{ filename: string; size_bytes: number }[]>([]);
  const [mode, setMode] = useState<'local' | 'webdav'>('local');
  const [davUrl, setDavUrl] = useState('');
  const [davUser, setDavUser] = useState('');
  const [davPassword, setDavPassword] = useState('');
  const [davFolder, setDavFolder] = useState('Downloader');
  const [passwordConfigured, setPasswordConfigured] = useState(false);
  const [presetName, setPresetName] = useState('');
  const [presetEngine, setPresetEngine] = useState<Engine>('yt-dlp');
  const [presetDescription, setPresetDescription] = useState('');
  const [presetSettingsText, setPresetSettingsText] = useState('{}');
  const [ruleHost, setRuleHost] = useState('');
  const [rulePath, setRulePath] = useState('/');
  const [ruleEngine, setRuleEngine] = useState<Engine>('yt-dlp');
  const [rulePriority, setRulePriority] = useState('100');
  const [ruleDescription, setRuleDescription] = useState('');

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
    void request(API + '/engines/generic/settings').then(data => {
      const saved = { ...genericDefaults, ...data.settings };
      setGeneric(saved); setGenericOverride(saved);
    }).catch(error => setMessage(String(error)));
    void request(API + '/engines/gallery-dl/settings').then(data => {
      const saved = { ...galleryDefaults, ...data.settings };
      setGallery(saved); setGalleryOverride(saved);
      setGalleryOptionsText(JSON.stringify(saved.options ?? {}, null, 2));
      setGalleryOverrideOptionsText(JSON.stringify(saved.options ?? {}, null, 2));
    }).catch(error => setMessage(String(error)));
    void request(API + '/engines').then(setEngineStatus).catch(error => setMessage(String(error)));
    for (const name of ['yt-dlp', 'aria2c', 'gallery-dl', 'generic'] as Engine[]) {
      void request(API + '/engines/' + name + '/options').then(data =>
        setOptionCatalog(current => ({ ...current, [name]: data.fields }))
      ).catch(error => setMessage(String(error)));
    }
    void request(API + '/cookies').then(setCookies).catch(error => setMessage(String(error)));
    void request(API + '/presets').then(setPresets).catch(error => setMessage(String(error)));
    void request(API + '/engine-rules').then(setEngineRules).catch(error => setMessage(String(error)));
  }, [apiKey, request]);

  useEffect(() => {
    if (!apiKey || tab !== 'delivery') return;
    void request(API + '/delivery').then(data => {
      setMode(data.mode); setDavUrl(data.webdav_url ?? '');
      setDavUser(data.username ?? ''); setDavFolder(data.folder ?? 'Downloader');
      setPasswordConfigured(Boolean(data.password_configured));
    }).catch(error => setMessage(String(error)));
  }, [apiKey, tab, request]);

  useEffect(() => {
    if (!apiKey || !selected) return;
    const controller = new AbortController();

    async function streamSelectedJob() {
      try {
        const response = await fetch(API + '/jobs/' + selected + '/events', {
          headers: { 'X-API-Key': apiKey },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error('Could not open the selected job event stream');
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (!controller.signal.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let boundary = buffer.indexOf('\n\n');
          while (boundary !== -1) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const dataLine = block.split(/\r?\n/).find(line => line.startsWith('data:'));
            if (dataLine) {
              try {
                const update = JSON.parse(dataLine.slice(5).trim()) as Job;
                setJobs(previous => previous.map(job => job.id === update.id ? update : job));
              } catch {
                setMessage('Received an invalid job update from the service.');
              }
            }
            boundary = buffer.indexOf('\n\n');
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setMessage(error instanceof Error ? error.message : 'The job event stream disconnected');
        }
      }
    }

    void streamSelectedJob();
    return () => controller.abort();
  }, [apiKey, selected]);

  function currentOverrides(): object {
    if (engine === 'yt-dlp') return ytOverride;
    if (engine === 'aria2c') return ariaOverride;
    if (engine === 'gallery-dl') {
      return { ...galleryOverride, options: parseOptionsJson(galleryOverrideOptionsText) };
    }
    if (engine === 'generic') return genericOverride;
    return {};
  }

  async function inspectUrl() {
    if (!url.trim()) return;
    setBusy(true);
    setMediaPreview(null);
    try {
      const result = await request(API + '/preview', {
        method: 'POST',
        body: JSON.stringify({ url, max_entries: 50 }),
      }) as MediaPreview;
      setMediaPreview(result);
      if (result.kind === 'playlist') {
        const entries = result.entries.map(entry => entry.index);
        setSelectedPlaylistEntries(entries);
        setYtOverride(value => ({
          ...value, allow_playlist: true, playlist_items: entries.join(','),
        }));
      }
      setMessage('Media information loaded. Review formats or playlist selections before downloading.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Media inspection failed');
    } finally {
      setBusy(false);
    }
  }

  function togglePreviewEntry(index: number, enabled: boolean) {
    const next = enabled
      ? [...new Set([...selectedPlaylistEntries, index])].sort((a, b) => a - b)
      : selectedPlaylistEntries.filter(value => value !== index);
    setSelectedPlaylistEntries(next);
    setYtOverride(value => ({
      ...value, allow_playlist: true, playlist_items: next.join(','),
    }));
  }

  function updateAriaOption(key: string, value: string, global: boolean) {
    const edit = (current: AriaSettings) => {
      const options = { ...current.options };
      if (value === '') delete options[key];
      else options[key] = value;
      return { options };
    };
    if (global) setAria(edit); else setAriaOverride(edit);
  }

  function updateGenericOption(key: string, value: string, global: boolean) {
    const setValue = global ? setGeneric : setGenericOverride;
    setValue(previous => ({
      ...previous,
      [key]: key === 'follow_redirects' ? value === 'true' :
        key === 'max_redirects' || key === 'retry_count' ? Number(value) : value,
    }));
  }

  function genericOptionValues(value: GenericSettings): Record<string, string> {
    return Object.fromEntries(Object.entries(value).map(([key, field]) => [key, String(field)]));
  }

  function updateGalleryOption(key: string, value: string, global: boolean) {
    const setter = global ? setGalleryOptionsText : setGalleryOverrideOptionsText;
    setter(previous => {
      try {
        const options = { ...parseOptionsJson(previous) };
        if (value === '') delete options[key];
        else options[key] = value;
        return JSON.stringify(options, null, 2);
      } catch {
        setMessage('Fix the JSON in the advanced editor before changing graphical options.');
        return previous;
      }
    });
  }

  function catalogValues(source: string): Record<string, string> {
    try { return parseOptionsJson(source); }
    catch { return {}; }
  }

  function engineVersion(name: Engine) {
    const status = engineStatus.find(item => item.name === name);
    return status?.installed ? 'Installed · ' + (status.version || 'version unavailable') : 'Executable not detected';
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true);
    try {
      const job = await request(API + '/jobs', {
        method: 'POST',
        body: JSON.stringify({
          url, engine, preset_id: selectedPresetId || null, overrides: currentOverrides(),
        }),
      });
      setSelected(job.id); setUrl(''); setMediaPreview(null); setMessage('Job queued.'); await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not submit job');
    } finally { setBusy(false); }
  }

  async function submitBatch() {
    setBusy(true);
    try {
      const urls = batchUrls.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
      if (urls.length === 0) throw new Error('Add at least one URL to the batch.');
      if (urls.length > 50) throw new Error('A batch can contain at most 50 URLs.');
      const response = await request(API + '/jobs/batch', {
        method: 'POST',
        body: JSON.stringify({
          items: urls.map(itemUrl => ({
            url: itemUrl, engine, preset_id: selectedPresetId || null, overrides: currentOverrides(),
          })),
        }),
      });
      setSelected(response.jobs?.[0]?.id ?? null);
      setBatchUrls('');
      setMessage(response.created + ' jobs added to the queue.');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not submit batch');
    } finally { setBusy(false); }
  }

  async function loadRoutingSettings() {
    setPresets(await request(API + '/presets'));
    setEngineRules(await request(API + '/engine-rules'));
  }

  async function createPreset(event: React.FormEvent) {
    event.preventDefault();
    try {
      const settings = parseJsonObject(presetSettingsText);
      const created = await request(API + '/presets', {
        method: 'POST',
        body: JSON.stringify({ name: presetName, engine: presetEngine, description: presetDescription, settings }),
      });
      setPresets(current => [...current, created].sort((a, b) => a.name.localeCompare(b.name)));
      setPresetName(''); setPresetDescription(''); setMessage('Preset saved.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save preset');
    }
  }

  async function deletePreset(id: string) {
    try {
      await request(API + '/presets/' + id, { method: 'DELETE' });
      setPresets(current => current.filter(item => item.id !== id));
      if (selectedPresetId === id) setSelectedPresetId('');
      setMessage('Preset deleted.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not delete preset');
    }
  }

  async function createEngineRule(event: React.FormEvent) {
    event.preventDefault();
    try {
      const created = await request(API + '/engine-rules', {
        method: 'POST',
        body: JSON.stringify({
          host_suffix: ruleHost, path_prefix: rulePath, engine: ruleEngine,
          priority: Number(rulePriority), enabled: true, description: ruleDescription,
        }),
      });
      setEngineRules(current => [...current, created].sort((a, b) => b.priority - a.priority));
      setRuleHost(''); setRulePath('/'); setRulePriority('100'); setRuleDescription('');
      setMessage('URL engine rule saved.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save URL rule');
    }
  }

  async function deleteEngineRule(id: string) {
    try {
      await request(API + '/engine-rules/' + id, { method: 'DELETE' });
      setEngineRules(current => current.filter(item => item.id !== id));
      setMessage('URL engine rule deleted.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not delete URL rule');
    }
  }

  async function saveEngine(name: Engine) {
    try {
      if (name === 'yt-dlp') {
        const settings = yt;
        await request(API + '/engines/yt-dlp/settings', {
          method: 'PUT', body: JSON.stringify({ settings }),
        });
        setYtOverride(settings);
      } else if (name === 'aria2c') {
        const settings = aria;
        await request(API + '/engines/aria2c/settings', {
          method: 'PUT', body: JSON.stringify({ settings }),
        });
        setAriaOverride(settings);
      } else if (name === 'generic') {
        const settings = generic;
        await request(API + '/engines/generic/settings', {
          method: 'PUT', body: JSON.stringify({ settings }),
        });
        setGenericOverride(settings);
      } else {
        const settings = { ...gallery, options: parseOptionsJson(galleryOptionsText) };
        await request(API + '/engines/gallery-dl/settings', {
          method: 'PUT', body: JSON.stringify({ settings }),
        });
        setGallery(settings);
        setGalleryOverride(settings);
        setGalleryOverrideOptionsText(JSON.stringify(settings.options, null, 2));
      }
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
      <button className={tab === 'routing' ? 'active' : ''} onClick={() => setTab('routing')}>Presets & routing</button>
      <button className={tab === 'delivery' ? 'active' : ''} onClick={() => setTab('delivery')}>Delivery</button>
    </nav>

    {tab === 'downloads' && <div className="layout">
      <section className="panel submit-panel">
        <div className="eyebrow">NEW DOWNLOAD</div><h2>Submit a link</h2>
        <form onSubmit={submit}>
          <label>Media URL<input type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://" required /></label>
          <label>Engine<select value={engine} onChange={event => { setEngine(event.target.value as EngineSelection); setSelectedPresetId(''); }}>
            <option value="auto">Automatic — use URL rules</option>
            <option value="yt-dlp">yt-dlp — websites and media</option>
            <option value="aria2c">aria2c — direct, torrent, magnet</option>
            <option value="gallery-dl">gallery-dl — image galleries</option>
            <option value="generic">generic / curl — direct file URLs</option>
          </select></label>
          <label>Option preset<select value={selectedPresetId} onChange={event => setSelectedPresetId(event.target.value)}>
            <option value="">No preset</option>
            {presets.filter(preset => engine === 'auto' || preset.engine === engine).map(preset => (
              <option key={preset.id} value={preset.id}>{preset.name} ({preset.engine})</option>
            ))}
          </select></label>
          {engine === 'yt-dlp' ? <div className="options-block">
            <div className="field-title">Per-download overrides</div>
            <label>Format<input value={ytOverride.format} onChange={event => setYtOverride(value => ({ ...value, format: event.target.value }))} /></label>
            <label>Subtitle languages<input value={ytOverride.subtitles_lang} onChange={event => setYtOverride(value => ({ ...value, subtitles_lang: event.target.value }))} /></label>
            <div className="checks"><label className="check"><input type="checkbox" checked={ytOverride.allow_playlist} onChange={event => setYtOverride(value => ({ ...value, allow_playlist: event.target.checked, playlist_items: event.target.checked ? value.playlist_items : '' }))} /> Allow playlist downloads</label></div>
            {ytOverride.allow_playlist && <label>Playlist entries (e.g. 1,3,5-7)<input value={ytOverride.playlist_items} onChange={event => setYtOverride(value => ({ ...value, playlist_items: event.target.value }))} placeholder="Leave empty for entire playlist" /></label>}
            <button type="button" className="secondary" disabled={busy || !url.trim()} onClick={() => void inspectUrl()}>Inspect media formats / playlist</button>
            {mediaPreview && <div className="options-block">
              <div className="field-title">{mediaPreview.title}</div>
              {mediaPreview.kind === 'video'
                ? <label>Available formats<select value={ytOverride.format} onChange={event => setYtOverride(value => ({ ...value, format: event.target.value }))}>
                    <option value={ytOverride.format}>Current: {ytOverride.format}</option>
                    {mediaPreview.formats.map(format => <option key={format.format_id} value={format.format_id}>{format.format_id} · {format.ext} · {format.resolution}</option>)}
                  </select></label>
                : <div><p className="muted">Select entries to download ({mediaPreview.entries.length} previewed).</p>
                    <div className="preview-list">{mediaPreview.entries.map(entry =>
                      <label className="check" key={entry.index}><input type="checkbox" checked={selectedPlaylistEntries.includes(entry.index)} onChange={event => togglePreviewEntry(entry.index, event.target.checked)} /> {entry.index}. {entry.title}</label>
                    )}</div>
                  </div>}
            </div>}

            <label>Cookie file<select value={ytOverride.cookies_file ?? ''} onChange={event => setYtOverride(value => ({ ...value, cookies_file: event.target.value }))}>
              <option value="">No cookie file</option>{cookies.map(file => <option key={file.filename} value={file.filename}>{file.filename}</option>)}
            </select></label>
            <div className="checks">
              <label className="check"><input type="checkbox" checked={ytOverride.write_subtitles} onChange={event => setYtOverride(value => ({ ...value, write_subtitles: event.target.checked }))} /> Download subtitles</label>
              <label className="check"><input type="checkbox" checked={ytOverride.embed_subtitles} onChange={event => setYtOverride(value => ({ ...value, embed_subtitles: event.target.checked }))} /> Embed subtitles</label>
            </div>
          </div> : engine === 'aria2c' ? <div className="options-block">
            <div className="field-title">Per-download overrides</div>
            <CommonOptionFields fields={optionCatalog.aria2c} values={ariaOverride.options} onChange={(key, value) => updateAriaOption(key, value, false)} />
          </div> : engine === 'gallery-dl' ? <div className="options-block">
            <div className="field-title">Per-download overrides</div>
            <CommonOptionFields fields={optionCatalog['gallery-dl']} values={catalogValues(galleryOverrideOptionsText)} onChange={(key, value) => updateGalleryOption(key, value, false)} />
            <label>Advanced extractor options (JSON object)<textarea rows={4} value={galleryOverrideOptionsText} onChange={event => setGalleryOverrideOptionsText(event.target.value)} /></label>
            <label>Cookie file<select value={galleryOverride.cookies_file ?? ''} onChange={event => setGalleryOverride(value => ({ ...value, cookies_file: event.target.value }))}>
              <option value="">No cookie file</option>{cookies.map(file => <option key={file.filename} value={file.filename}>{file.filename}</option>)}
            </select></label>
            <label>Proxy URL<input value={galleryOverride.proxy ?? ''} onChange={event => setGalleryOverride(value => ({ ...value, proxy: event.target.value }))} placeholder="Optional proxy" /></label>
          </div> : engine === 'generic' ? <div className="options-block">
            <div className="field-title">Direct download overrides</div>
            <CommonOptionFields fields={optionCatalog.generic} values={genericOptionValues(genericOverride)} onChange={(key, value) => updateGenericOption(key, value, false)} />
          </div> : <div className="options-block">
            <div className="field-title">Automatic selection</div>
            <p className="muted">The service chooses the engine from your saved host/path rules. FTP and magnet links default to aria2c; unmatched URLs default to yt-dlp. A selected preset provides engine defaults when no URL rule matches.</p>
          </div>}
          <button className="primary" disabled={busy || !apiKey || !url.trim()}>{busy ? 'Adding…' : 'Add to queue'}</button>
        </form>
        <div className="options-block">
          <div className="field-title">Batch download</div>
          <label>One URL per line<textarea rows={5} value={batchUrls} onChange={event => setBatchUrls(event.target.value)} placeholder="https://example.com/video-1&#10;https://example.com/video-2" /></label>
          <p className="muted">Up to 50 links per batch. The whole batch is rejected if any URL is invalid.</p>
          <button className="secondary" disabled={busy || !apiKey || !batchUrls.trim()} onClick={() => void submitBatch()}>{busy ? 'Adding…' : 'Add batch to queue'}</button>
        </div>
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
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>yt-dlp</h2><p className="muted">{engineVersion('yt-dlp')}. Global defaults apply to new jobs; per-download options take precedence.</p>
        <label>Format selector<input value={yt.format} onChange={event => setYt(value => ({ ...value, format: event.target.value }))} /></label>
        <label>Subtitle languages<input value={yt.subtitles_lang} onChange={event => setYt(value => ({ ...value, subtitles_lang: event.target.value }))} /></label>
        <div className="checks"><label className="check"><input type="checkbox" checked={yt.allow_playlist} onChange={event => setYt(value => ({ ...value, allow_playlist: event.target.checked, playlist_items: event.target.checked ? value.playlist_items : '' }))} /> Download playlists by default</label></div>
        {yt.allow_playlist && <label>Default playlist selection (optional)<input value={yt.playlist_items} onChange={event => setYt(value => ({ ...value, playlist_items: event.target.value }))} placeholder="e.g. 1-5 or leave empty for all" /></label>}

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
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>aria2c</h2><p className="muted">{engineVersion('aria2c')}. These settings map to aria2 options; RPC access and output paths remain service-managed.</p>
        <CommonOptionFields fields={optionCatalog.aria2c} values={aria.options} onChange={(key, value) => updateAriaOption(key, value, true)} />
        <button className="secondary" onClick={() => setAria(ariaDefaults)}>Reset shown defaults</button>
        <button className="primary" onClick={() => void saveEngine('aria2c')}>Save aria2c defaults</button>
      </section>
      <section className="panel">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>gallery-dl</h2>
        <p className="muted">{engineVersion('gallery-dl')}. Common extractor options are graphical; site-specific options remain available in the advanced JSON editor. The service controls the output root.</p>
        <CommonOptionFields fields={optionCatalog['gallery-dl']} values={catalogValues(galleryOptionsText)} onChange={(key, value) => updateGalleryOption(key, value, true)} />
        <label>Advanced extractor options (JSON object)<textarea rows={8} value={galleryOptionsText} onChange={event => setGalleryOptionsText(event.target.value)} /></label>
        <label>Default cookie file<select value={gallery.cookies_file ?? ''} onChange={event => setGallery(value => ({ ...value, cookies_file: event.target.value }))}>
          <option value="">None</option>{cookies.map(file => <option key={file.filename} value={file.filename}>{file.filename}</option>)}
        </select></label>
        <label>Default proxy URL<input value={gallery.proxy ?? ''} onChange={event => setGallery(value => ({ ...value, proxy: event.target.value }))} placeholder="Optional proxy" /></label>
        <button className="secondary" onClick={() => { setGallery(galleryDefaults); setGalleryOptionsText('{}'); }}>Reset shown defaults</button>
        <button className="primary" onClick={() => void saveEngine('gallery-dl')}>Save gallery-dl defaults</button>
      </section>
      <section className="panel">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>generic / curl</h2>
        <p className="muted">{engineVersion('generic')}. For direct HTTP(S) links. Redirects are disabled by default.</p>
        <CommonOptionFields fields={optionCatalog.generic} values={genericOptionValues(generic)} onChange={(key, value) => updateGenericOption(key, value, true)} />
        <button className="primary" onClick={() => void saveEngine('generic')}>Save generic defaults</button>
      </section>
      <div className="hint panel"><strong>Advanced options</strong><p>Service-managed paths, cookie arguments, progress reporting, and process execution flags cannot be overridden.</p></div>
    </div>}

    {tab === 'routing' && <div className="settings-layout">
      <section className="panel">
        <div className="eyebrow">SAVED OPTIONS</div><h2>Create a preset</h2>
        <p className="muted">A preset stores engine-specific settings. Job-level overrides take precedence over preset settings.</p>
        <form onSubmit={createPreset}>
          <label>Preset name<input value={presetName} onChange={event => setPresetName(event.target.value)} required maxLength={120} /></label>
          <label>Engine<select value={presetEngine} onChange={event => setPresetEngine(event.target.value as Engine)}>
            <option value="yt-dlp">yt-dlp</option><option value="aria2c">aria2c</option><option value="gallery-dl">gallery-dl</option><option value="generic">generic / curl</option>
          </select></label>
          <label>Description<input value={presetDescription} onChange={event => setPresetDescription(event.target.value)} maxLength={1000} /></label>
          <label>Settings JSON<textarea rows={7} value={presetSettingsText} onChange={event => setPresetSettingsText(event.target.value)} /></label>
          <button className="primary">Save preset</button>
        </form>
      </section>
      <section className="panel">
        <div className="eyebrow">PRESET LIBRARY</div><h2>Saved presets</h2>
        {!presets.length ? <div className="empty">No presets saved.</div> : <div className="job-list">
          {presets.map(preset => <article key={preset.id} className="job">
            <div className="job-head"><div className="job-title"><span className="engine-pill">{preset.engine}</span><strong>{preset.name}</strong></div></div>
            {preset.description && <p className="muted">{preset.description}</p>}
            <pre className="log">{JSON.stringify(preset.settings, null, 2)}</pre>
            <div className="job-actions"><button className="small danger" onClick={() => void deletePreset(preset.id)}>Delete</button></div>
          </article>)}
        </div>}
      </section>
      <section className="panel">
        <div className="eyebrow">AUTOMATIC ENGINE SELECTION</div><h2>Add a URL rule</h2>
        <p className="muted">Host suffix matches the exact host and its subdomains. A more specific path or higher priority wins.</p>
        <form onSubmit={createEngineRule}>
          <label>Host suffix<input value={ruleHost} onChange={event => setRuleHost(event.target.value)} placeholder="example.com" required /></label>
          <label>Path prefix<input value={rulePath} onChange={event => setRulePath(event.target.value)} placeholder="/" /></label>
          <label>Engine<select value={ruleEngine} onChange={event => setRuleEngine(event.target.value as Engine)}>
            <option value="yt-dlp">yt-dlp</option><option value="aria2c">aria2c</option><option value="gallery-dl">gallery-dl</option><option value="generic">generic / curl</option>
          </select></label>
          <label>Priority (0–1000)<input type="number" min="0" max="1000" value={rulePriority} onChange={event => setRulePriority(event.target.value)} /></label>
          <label>Description<input value={ruleDescription} onChange={event => setRuleDescription(event.target.value)} maxLength={255} /></label>
          <button className="primary">Save URL rule</button>
        </form>
      </section>
      <section className="panel">
        <div className="eyebrow">URL RULES</div><h2>Routing table</h2>
        {!engineRules.length ? <div className="empty">No URL rules configured. Automatic selection defaults to yt-dlp, or aria2c for FTP/magnet links.</div> : <div className="job-list">
          {engineRules.map(rule => <article key={rule.id} className="job">
            <div className="job-head"><div className="job-title"><span className="engine-pill">{rule.engine}</span><strong>{rule.host_suffix}{rule.path_prefix === '/' ? '' : rule.path_prefix}</strong></div><span className="status">Priority {rule.priority}</span></div>
            {rule.description && <p className="muted">{rule.description}</p>}
            <div className="job-actions"><button className="small danger" onClick={() => void deleteEngineRule(rule.id)}>Delete</button></div>
          </article>)}
        </div>}
      </section>
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
