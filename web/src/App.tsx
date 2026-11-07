import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiErrorMessage } from './api-errors';
import { JobCard } from './JobCard';
import { QualityControl } from './QualityControl';
import { PresetOptionEditor } from './PresetOptionEditor';
import { EngineAutosaveQueue, type SaveState } from './engine-autosave';
import { moveRule, stepRule, isReordered } from './rule-order';

type Engine = 'yt-dlp' | 'aria2c' | 'gallery-dl' | 'generic' | 'you-get' | 'svtplay-dl' | 'get-sauce';
type EngineSelection = 'auto' | Engine;
type View = 'download' | 'library' | 'my-settings' | 'administration';
type AdminArea = 'connection' | 'delivery' | 'engines' | 'automation' | 'access';
const LEGACY_NAVIGATION: Record<string, {view: View; area?: AdminArea}> = {
  downloads: {view: 'download'},
  'engine-settings': {view: 'administration', area: 'engines'},
  engines: {view: 'administration', area: 'engines'},
  delivery: {view: 'administration', area: 'delivery'},
  routing: {view: 'administration', area: 'automation'},
  presets: {view: 'my-settings'},
  'presets-routing': {view: 'my-settings'},
  'presets-and-routing': {view: 'my-settings'},
};
function initialNavigation(): {view: View; area?: AdminArea; canonical: string} {
  const old = window.location.hash.slice(1).toLowerCase();
  const redirected = LEGACY_NAVIGATION[old];
  if (redirected) return {...redirected, canonical: redirected.view};
  if (['download', 'library', 'my-settings', 'administration'].includes(old)) {
    return {view: old as View, canonical: old};
  }
  return {view: 'download', canonical: 'download'};
}
function initialView(): View {
  return initialNavigation().view;
}
type Preset = { id: string; name: string; engine: Engine; settings: Record<string, unknown>; description: string; is_shared?: boolean };
type PresetDraft = { name: string; engine: Engine; settings: Record<string, unknown>; description: string; is_shared: boolean };
type EngineRule = { id: string; host_suffix: string; path_prefix: string; engine: Engine; priority: number; enabled: boolean; description: string };
type EngineRuleDraft = Omit<EngineRule, 'id'>;
type DeliveryDraft = {mode: 'local' | 'webdav'; webdav_url: string | null; username: string | null; folder: string; app_password: null};
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
type YouGetSettings = { format: string; cookies_file: string; no_proxy: boolean };
type SvtSettings = { resolution: string; quality: string; subtitles: boolean; all_subtitles: boolean; all_episodes: boolean; output_format: string };
type GetSauceSettings = { stream_index: number; amount: number; workers: number; merge: string; pages: string; subdirectory: boolean; timeout_minutes: number };
type OptionField = { key: string; label: string; category: string; type: string; description?: string; minimum?: number; maximum?: number };
type EngineStatus = { name: Engine; installed: boolean; version?: string | null };

const ENGINE_DESCRIPTIONS: Record<Engine, {title: string; purpose: string}> = {
  'yt-dlp': {title: 'Video & audio sites', purpose: 'Videos, playlists, subtitles and audio from supported sites'},
  aria2c: {title: 'Large files & torrents', purpose: 'Direct files, magnets, FTP and torrent downloads'},
  'gallery-dl': {title: 'Image galleries', purpose: 'Image collections from supported gallery websites'},
  generic: {title: 'Direct file downloads', purpose: 'Files from public HTTP and HTTPS links'},
  'you-get': {title: 'Alternative video sites', purpose: 'Fallback downloader for supported videos and audio'},
  'svtplay-dl': {title: 'Nordic television', purpose: 'Video and subtitles from Nordic broadcasting sites'},
  'get-sauce': {title: 'Specialized media', purpose: 'Specialized media and gallery download sources'},
};
const ENGINE_ORDER = Object.keys(ENGINE_DESCRIPTIONS) as Engine[];

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
const youGetDefaults: YouGetSettings = { format: '', cookies_file: '', no_proxy: false };
const svtDefaults: SvtSettings = { resolution: '', quality: '', subtitles: false, all_subtitles: false, all_episodes: false, output_format: 'mp4' };
const getSauceDefaults: GetSauceSettings = { stream_index: 0, amount: 0, workers: 4, merge: 'default', pages: '', subdirectory: false, timeout_minutes: 15 };

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
  const [tab, setTab] = useState<View>(initialView);
  const [adminArea, setAdminArea] = useState<AdminArea>(() => initialNavigation().area ?? 'connection');
  useEffect(() => {
    const syncLocation = () => {
      const destination = initialNavigation();
      setTab(destination.view);
      if (destination.area) setAdminArea(destination.area);
      if (window.location.hash !== '#' + destination.canonical) {
        window.history.replaceState(null, '', '#' + destination.canonical);
      }
    };
    // Bookmarks, cross-page links and the browser Back button may all change
    // the hash without remounting React. Keep the visible view in sync.
    window.addEventListener('hashchange', syncLocation);
    syncLocation();
    return () => window.removeEventListener('hashchange', syncLocation);
  }, []);
  const [connectionDraft, setConnectionDraft] = useState(() => localStorage.getItem('downloader-api-key') ?? '');
  const [connected, setConnected] = useState(false);
  const [checkingConnection, setCheckingConnection] = useState(false);
  const [deliveryVerified, setDeliveryVerified] = useState(false);
  const [testingDelivery, setTestingDelivery] = useState(false);
  const [deliveryDirty, setDeliveryDirty] = useState(false);
  const [firstCompletedVerified, setFirstCompletedVerified] = useState(false);
  const [historyChecked, setHistoryChecked] = useState(false);
  const [accessAcknowledged, setAccessAcknowledged] = useState(
    () => localStorage.getItem('downloader-admin-access-ack') === 'yes',
  );
  function navigate(view: View) {
    setTab(view); window.history.replaceState(null, '', '#' + view);
  }
  async function checkConnection() {
    const key = connectionDraft.trim();
    if (!key) { setMessage('Enter your administrator API key to connect.'); return; }
    setCheckingConnection(true);
    try {
      const response = await fetch(API + '/engines', { headers: { 'X-API-Key': key } });
      if (!response.ok) throw new Error('Could not connect. Check the API key and server availability.');
      const result = await response.json() as EngineStatus[];
      if (!Array.isArray(result)) throw new Error('Unexpected service response.');
      localStorage.setItem('downloader-api-key', key);
      setEngineStatus(result); setApiKey(key); setConnected(true);
      setHistoryChecked(false); setDeliveryVerified(false);
      setMessage('Connected ✓');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not connect.');
    } finally { setCheckingConnection(false); }
  }
  function disconnect() {
    localStorage.removeItem('downloader-api-key');
    setApiKey(''); setConnectionDraft(''); setConnected(false); setJobs([]);
    setHistoryChecked(false); setDeliveryVerified(false);
    setMessage('Disconnected. Reconnect to download.');
    navigate('my-settings');
  }
  const [engine, setEngine] = useState<EngineSelection>('auto');
  const [selectedPresetId, setSelectedPresetId] = useState('');
  const [presets, setPresets] = useState<Preset[]>([]);
  const [engineRules, setEngineRules] = useState<EngineRule[]>([]);
  const [batchUrls, setBatchUrls] = useState('');
  const [linkMode, setLinkMode] = useState<'one' | 'many'>('one');
  const [url, setUrl] = useState('');
  const [mediaPreview, setMediaPreview] = useState<MediaPreview | null>(null);
  const [selectedPlaylistEntries, setSelectedPlaylistEntries] = useState<number[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [libraryQuery, setLibraryQuery] = useState('');
  const [libraryFilter, setLibraryFilter] = useState<'all' | 'active' | 'delivered' | 'failed'>('all');
  const [libraryJobs, setLibraryJobs] = useState<Job[]>([]);
  const [libraryOffset, setLibraryOffset] = useState(0);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryHasMore, setLibraryHasMore] = useState(false);
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
  const [youGet, setYouGet] = useState<YouGetSettings>(youGetDefaults);
  const [youGetOverride, setYouGetOverride] = useState<YouGetSettings>(youGetDefaults);
  const [svt, setSvt] = useState<SvtSettings>(svtDefaults);
  const [svtOverride, setSvtOverride] = useState<SvtSettings>(svtDefaults);
  const [getSauce, setGetSauce] = useState<GetSauceSettings>(getSauceDefaults);
  const [getSauceOverride, setGetSauceOverride] = useState<GetSauceSettings>(getSauceDefaults);
  const [optionCatalog, setOptionCatalog] = useState<Record<Engine, OptionField[]>>({ 'yt-dlp': [], aria2c: [], 'gallery-dl': [], generic: [], 'you-get': [], 'svtplay-dl': [], 'get-sauce': [] });
  const [engineStatus, setEngineStatus] = useState<EngineStatus[]>([]);
  const [editingEngine, setEditingEngine] = useState<Engine>('yt-dlp');
  const [engineLoaded, setEngineLoaded] = useState<Partial<Record<Engine, boolean>>>({});
  const [engineLoadError, setEngineLoadError] = useState<Partial<Record<Engine, boolean>>>({});
  const [optionCatalogLoaded, setOptionCatalogLoaded] = useState<Partial<Record<Engine, boolean>>>({});
  const [engineSaveStatus, setEngineSaveStatus] = useState<Partial<Record<Engine, SaveState>>>({});
  const engineSaveQueues = useRef<Partial<Record<Engine, EngineAutosaveQueue<unknown>>>>({});
  const [galleryOptionsText, setGalleryOptionsText] = useState('{}');
  const [galleryOverrideOptionsText, setGalleryOverrideOptionsText] = useState('{}');
  const [cookies, setCookies] = useState<{ filename: string; size_bytes: number }[]>([]);
  const [mode, setMode] = useState<'local' | 'webdav'>('local');
  const [davUrl, setDavUrl] = useState('');
  const [davUser, setDavUser] = useState('');
  const [davPassword, setDavPassword] = useState('');
  const [davFolder, setDavFolder] = useState('Downloader');
  const [passwordConfigured, setPasswordConfigured] = useState(false);
  const [deliveryModeSaving, setDeliveryModeSaving] = useState(false);
  const [deliveryModeStatus, setDeliveryModeStatus] = useState('');
  const [deliveryFieldState, setDeliveryFieldState] = useState<SaveState>('loading');
  const [deliveryFieldError, setDeliveryFieldError] = useState('');
  const deliveryFieldQueue = useRef<EngineAutosaveQueue<DeliveryDraft> | null>(null);
  const deliverySaved = useRef<{mode: 'local' | 'webdav'; webdav_url: string; username: string; folder: string; password_configured: boolean} | null>(null);
  const [presetName, setPresetName] = useState('');
  const [presetEngine, setPresetEngine] = useState<Engine>('yt-dlp');
  const [presetDescription, setPresetDescription] = useState('');
  const [presetShared, setPresetShared] = useState(false);
  const [presetSettingsText, setPresetSettingsText] = useState('{}');
  const [editingPresetId, setEditingPresetId] = useState<string | null>(null);
  const [presetSaveState, setPresetSaveState] = useState<SaveState>('saved');
  const [presetValidationError, setPresetValidationError] = useState('');
  const presetSaveQueue = useRef<EngineAutosaveQueue<PresetDraft> | null>(null);
  const [ruleHost, setRuleHost] = useState('');
  const [rulePath, setRulePath] = useState('/');
  const [ruleEngine, setRuleEngine] = useState<Engine>('yt-dlp');
  const [rulePriority, setRulePriority] = useState('100');
  const [ruleDescription, setRuleDescription] = useState('');
  const [ruleEnabled, setRuleEnabled] = useState(true);
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [ruleSaveState, setRuleSaveState] = useState<SaveState>('saved');
  const [ruleValidationError, setRuleValidationError] = useState('');
  const ruleSaveQueue = useRef<EngineAutosaveQueue<EngineRuleDraft> | null>(null);
  const [ruleBusy, setRuleBusy] = useState(false);
  const [draggedRuleId, setDraggedRuleId] = useState<string | null>(null);
  const [ruleOverId, setRuleOverId] = useState<string | null>(null);
  const [rulePriorityEdited, setRulePriorityEdited] = useState(false);

  const headers = useMemo(() => ({ 'Content-Type': 'application/json', 'X-API-Key': apiKey }), [apiKey]);
  const request = useCallback(async (path: string, init: RequestInit = {}) => {
    if (!apiKey.trim()) throw new Error('Enter the API key to connect.');
    const response = await fetch(path, { ...init, headers: { ...headers, ...(init.headers ?? {}) } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(apiErrorMessage(data, response.status));
    return data;
  }, [apiKey, headers]);

  const autosaveQueue = useCallback((name: Engine) => {
    let queue = engineSaveQueues.current[name];
    if (!queue) {
      queue = new EngineAutosaveQueue<unknown>(
        value => request(API + '/engines/' + name + '/settings', {
          method: 'PUT', body: JSON.stringify({settings: value}),
        }),
        status => setEngineSaveStatus(previous => ({...previous, [name]: status})),
      );
      engineSaveQueues.current[name] = queue;
    }
    return queue;
  }, [request]);

  const hydrateEngine = useCallback((name: Engine, settings: unknown) => {
    autosaveQueue(name).hydrate(settings);
    setEngineLoaded(previous => ({...previous, [name]: true}));
    setEngineLoadError(previous => ({...previous, [name]: false}));
  }, [autosaveQueue]);

  // A credential change invalidates all outstanding writes from the prior service.
  useEffect(() => () => {
    Object.values(engineSaveQueues.current).forEach(queue => queue?.dispose());
    engineSaveQueues.current = {};
  }, [request]);

  useEffect(() => () => {
    presetSaveQueue.current?.dispose();
    presetSaveQueue.current = null;
    ruleSaveQueue.current?.dispose();
    ruleSaveQueue.current = null;
    deliveryFieldQueue.current?.dispose();
    deliveryFieldQueue.current = null;
  }, [request]);

  useEffect(() => {
    if (!editingPresetId || !presetSaveQueue.current) return;
    try {
      if (!presetName.trim()) throw new Error('Enter a preset name before changes can save.');
      const settings = parseJsonObject(presetSettingsText);
      setPresetValidationError('');
      presetSaveQueue.current.change({
        name: presetName.trim(), engine: presetEngine, settings,
        description: presetDescription, is_shared: presetShared,
      });
    } catch (error) {
      setPresetValidationError(
        error instanceof SyntaxError
          ? 'Invalid preset settings. Correct the advanced JSON before changes can save.'
          : error instanceof Error ? error.message : 'Invalid preset settings.',
      );
      presetSaveQueue.current.pause();
    }
  }, [editingPresetId, presetName, presetEngine, presetSettingsText, presetDescription, presetShared]);

  useEffect(() => {
    if (!editingRuleId || !ruleSaveQueue.current) return;
    const priority = Number(rulePriority);
    if (!ruleHost.trim() || !rulePath.startsWith('/') || !Number.isInteger(priority) ||
        rulePriority.trim() === '' || priority < 0 || priority > 1000) {
      setRuleValidationError('Enter a website, a path starting with /, and a priority from 0 to 1000.');
      ruleSaveQueue.current.pause();
      return;
    }
    setRuleValidationError('');
    ruleSaveQueue.current.change({host_suffix: ruleHost.trim(), path_prefix: rulePath,
      engine: ruleEngine, priority, enabled: ruleEnabled, description: ruleDescription});
  }, [editingRuleId, ruleHost, rulePath, ruleEngine, rulePriority, ruleEnabled, ruleDescription]);

  const refresh = useCallback(async () => {
    try {
      setJobs(await request(API + '/jobs?limit=200&standalone_only=true'));
    } catch (error) {
      if (apiKey) setMessage(error instanceof Error ? error.message : 'Could not load jobs');
    }
  }, [apiKey, request]);

  const fetchLibrary = useCallback(async (offset: number = 0) => {
    if (!apiKey) return;
    setLibraryLoading(true);
    try {
      const params = new URLSearchParams({
        limit: '200', offset: String(offset),
        standalone_only: 'true', state: libraryFilter, q: libraryQuery.trim(),
      });
      const rows = await request(API + '/jobs?' + params.toString()) as Job[];
      setLibraryJobs(previous => offset === 0 ? rows : [...previous, ...rows]);
      setLibraryOffset(offset);
      setLibraryHasMore(rows.length === 200);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not search download history.');
    } finally {
      setLibraryLoading(false);
    }
  }, [apiKey, libraryFilter, libraryQuery, request]);

  useEffect(() => {
    if (!apiKey || tab !== 'library') return;
    const timer = window.setTimeout(() => void fetchLibrary(0), 250);
    return () => window.clearTimeout(timer);
  }, [apiKey, tab, fetchLibrary]);

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
      hydrateEngine('yt-dlp', saved);
      setYt(saved); setYtOverride(saved);
    }).catch(() => setEngineLoadError(previous => ({...previous, ['yt-dlp']: true})));
    void request(API + '/engines/aria2c/settings').then(data => {
      const saved = { ...ariaDefaults, ...data.settings };
      hydrateEngine('aria2c', saved);
      setAria(saved); setAriaOverride(saved);
    }).catch(() => setEngineLoadError(previous => ({...previous, ['aria2c']: true})));
    void request(API + '/engines/you-get/settings').then(data => {
      const saved = { ...youGetDefaults, ...data.settings };
      hydrateEngine('you-get', saved);
      setYouGet(saved); setYouGetOverride(saved);
    }).catch(() => setEngineLoadError(previous => ({...previous, ['you-get']: true})));
    void request(API + '/engines/svtplay-dl/settings').then(data => {
      const saved = { ...svtDefaults, ...data.settings };
      hydrateEngine('svtplay-dl', saved);
      setSvt(saved); setSvtOverride(saved);
    }).catch(() => setEngineLoadError(previous => ({...previous, ['svtplay-dl']: true})));
    void request(API + '/engines/get-sauce/settings').then(data => {
      const saved = { ...getSauceDefaults, ...data.settings };
      hydrateEngine('get-sauce', saved);
      setGetSauce(saved); setGetSauceOverride(saved);
    }).catch(() => setEngineLoadError(previous => ({...previous, ['get-sauce']: true})));
    void request(API + '/engines/generic/settings').then(data => {
      const saved = { ...genericDefaults, ...data.settings };
      hydrateEngine('generic', saved);
      setGeneric(saved); setGenericOverride(saved);
    }).catch(() => setEngineLoadError(previous => ({...previous, ['generic']: true})));
    void request(API + '/engines/gallery-dl/settings').then(data => {
      const saved = { ...galleryDefaults, ...data.settings };
      hydrateEngine('gallery-dl', saved);
      setGallery(saved); setGalleryOverride(saved);
      setGalleryOptionsText(JSON.stringify(saved.options ?? {}, null, 2));
      setGalleryOverrideOptionsText(JSON.stringify(saved.options ?? {}, null, 2));
    }).catch(() => setEngineLoadError(previous => ({...previous, ['gallery-dl']: true})));
    void request(API + '/engines').then(data => { setEngineStatus(data); setConnected(true); }).catch(() => { setConnected(false); setMessage('Your connection needs attention. Reconnect in My settings.'); });
    for (const name of ['yt-dlp', 'aria2c', 'gallery-dl', 'generic', 'you-get', 'svtplay-dl', 'get-sauce'] as Engine[]) {
      void request(API + '/engines/' + name + '/options').then(data => {
        setOptionCatalog(current => ({ ...current, [name]: data.fields }));
        setOptionCatalogLoaded(previous => ({...previous, [name]: true}));
      }).catch(() => setMessage('Could not load the options for ' + name + '. Check the connection.'));
    }
    void request(API + '/cookies').then(setCookies).catch(error => setMessage(String(error)));
    void request(API + '/presets').then(setPresets).catch(error => setMessage(String(error)));
    void request(API + '/engine-rules').then(setEngineRules).catch(error => setMessage(String(error)));
  }, [apiKey, request, hydrateEngine]);

  useEffect(() => { engineSaveQueues.current['yt-dlp']?.change(yt); }, [yt]);
  useEffect(() => { engineSaveQueues.current.aria2c?.change(aria); }, [aria]);
  useEffect(() => { engineSaveQueues.current['you-get']?.change(youGet); }, [youGet]);
  useEffect(() => { engineSaveQueues.current['svtplay-dl']?.change(svt); }, [svt]);
  useEffect(() => { engineSaveQueues.current['get-sauce']?.change(getSauce); }, [getSauce]);
  useEffect(() => { engineSaveQueues.current.generic?.change(generic); }, [generic]);
  useEffect(() => {
    const queue = engineSaveQueues.current['gallery-dl'];
    if (!queue) return;
    try {
      const options = parseOptionsJson(galleryOptionsText);
      queue.change({...gallery, options});
    } catch {
      queue.pause();
    }
  }, [gallery, galleryOptionsText]);

  useEffect(() => {
    if (!apiKey || tab !== 'administration' || adminArea !== 'delivery') return;
    void request(API + '/delivery').then(data => {
      setMode(data.mode); setDavUrl(data.webdav_url ?? '');
      setDavUser(data.username ?? ''); setDavFolder(data.folder ?? 'Downloader');
      setPasswordConfigured(Boolean(data.password_configured)); setDeliveryDirty(false);
      deliverySaved.current = {mode: data.mode, webdav_url: data.webdav_url ?? '', username: data.username ?? '',
        folder: data.folder ?? 'Downloader', password_configured: Boolean(data.password_configured)};
      setDeliveryModeStatus('');
      deliveryFieldQueue.current?.dispose();
      const queue = new EngineAutosaveQueue<DeliveryDraft>(async draft => {
        const response = await request(API + '/delivery', {method: 'PUT', body: JSON.stringify(draft)});
        deliverySaved.current = {mode: draft.mode, webdav_url: draft.webdav_url ?? '',
          username: draft.username ?? '', folder: draft.folder,
          password_configured: Boolean(response.password_configured)};
        setPasswordConfigured(Boolean(response.password_configured));
        setDeliveryVerified(false);
      }, setDeliveryFieldState, 800);
      deliveryFieldQueue.current = queue;
      queue.hydrate({mode: data.mode, webdav_url: data.webdav_url ?? null,
        username: data.username ?? null, folder: data.folder ?? 'Downloader', app_password: null});
      setDeliveryFieldError('');
    }).catch(error => setMessage(String(error)));
  }, [apiKey, tab, adminArea, request]);

  useEffect(() => {
    const queue = deliveryFieldQueue.current;
    if (!queue || mode !== 'webdav' || !passwordConfigured || deliveryDirty || deliveryModeSaving) return;
    if (!/^https?:\/\//i.test(davUrl.trim()) || !davUser.trim() || !davFolder.trim()) {
      setDeliveryFieldError('Enter a valid WebDAV URL, username and destination folder before changes can save.');
      queue.pause();
      return;
    }
    setDeliveryFieldError('');
    queue.change({mode, webdav_url: davUrl.trim(), username: davUser.trim(),
      folder: davFolder.trim(), app_password: null});
  }, [mode, davUrl, davUser, davFolder, passwordConfigured, deliveryDirty, deliveryModeSaving]);

  useEffect(() => {
    if (!apiKey) { setHistoryChecked(false); return; }
    let live = true;
    void request(API + '/jobs?status=completed&limit=1')
      .then((records: Job[]) => {
        if (live) setFirstCompletedVerified(records.some(job =>
          Boolean(job.output_files?.length) && job.output_files.every(file => file.verified)));
      })
      .catch(() => { if (live) setFirstCompletedVerified(false); })
      .finally(() => { if (live) setHistoryChecked(true); });
    return () => { live = false; };
  }, [apiKey, request]);

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
    if (engine === 'you-get') return youGetOverride;
    if (engine === 'svtplay-dl') return svtOverride;
    if (engine === 'get-sauce') return getSauceOverride;
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

  function updateYouGetOption(key: string, value: string, global: boolean) {
    const setValue = global ? setYouGet : setYouGetOverride;
    setValue(previous => ({
      ...previous,
      [key]: key === 'no_proxy' ? value === 'true' : value,
    }));
  }

  function updateGetSauceOption(key: string, value: string, global: boolean) {
    const setValue = global ? setGetSauce : setGetSauceOverride;
    setValue(previous => ({
      ...previous,
      [key]: key === 'subdirectory' ? value === 'true' :
        ['stream_index', 'amount', 'workers', 'timeout_minutes'].includes(key) ? Number(value) : value,
    }));
  }

  function getSauceOptionValues(value: GetSauceSettings): Record<string, string> {
    return Object.fromEntries(Object.entries(value).map(([key, field]) => [key, String(field)]));
  }

  function updateSvtOption(key: string, value: string, global: boolean) {
    const setValue = global ? setSvt : setSvtOverride;
    setValue(previous => ({
      ...previous,
      [key]: ['subtitles', 'all_subtitles', 'all_episodes'].includes(key) ? value === 'true' : value,
    }));
  }

  function svtOptionValues(value: SvtSettings): Record<string, string> {
    return Object.fromEntries(Object.entries(value).map(([key, field]) => [key, String(field)]));
  }

  function youGetOptionValues(value: YouGetSettings): Record<string, string> {
    return Object.fromEntries(Object.entries(value).map(([key, field]) => [key, String(field)]));
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
    return status?.installed ? 'Installed · ' + (status.version || 'version unavailable') : 'Not installed';
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
      setSelected(job.id); setUrl(''); setMediaPreview(null); setMessage('Download started. Follow its progress below.'); await refresh();
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
      setMessage(response.created + ' downloads started.');
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not submit batch');
    } finally { setBusy(false); }
  }

  async function loadRoutingSettings() {
    setPresets(await request(API + '/presets'));
    setEngineRules(await request(API + '/engine-rules'));
  }

  function resetPresetEditor() {
    presetSaveQueue.current?.dispose();
    presetSaveQueue.current = null;
    setPresetSaveState('saved');
    setPresetValidationError('');
    setEditingPresetId(null);
    setPresetName('');
    setPresetEngine('yt-dlp');
    setPresetDescription('');
    setPresetShared(false);
    setPresetSettingsText('{}');
  }

  function editPreset(preset: Preset) {
    presetSaveQueue.current?.dispose();
    const queue = new EngineAutosaveQueue<PresetDraft>(
      async draft => {
        const saved = await request(API + '/presets/' + encodeURIComponent(preset.id), {
          method: 'PUT', body: JSON.stringify(draft),
        }) as Preset;
        setPresets(previous => previous.map(row => row.id === saved.id ? saved : row));
      },
      setPresetSaveState,
      800,
    );
    presetSaveQueue.current = queue;
    queue.hydrate({
      name: preset.name, engine: preset.engine,
      description: preset.description, settings: preset.settings,
      is_shared: preset.is_shared === true,
    });
    setPresetValidationError('');
    setEditingPresetId(preset.id);
    setPresetName(preset.name);
    setPresetEngine(preset.engine);
    setPresetDescription(preset.description);
    setPresetShared(preset.is_shared === true);
    setPresetSettingsText(JSON.stringify(preset.settings, null, 2));
    document.getElementById('preset-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function savePreset(event: React.FormEvent) {
    event.preventDefault();
    if (editingPresetId) return; // Existing entries use serialized autosave.
    try {
      const settings = parseJsonObject(presetSettingsText);
      await request(API + '/presets', {
        method: 'POST',
        body: JSON.stringify({ name: presetName, engine: presetEngine, description: presetDescription,
          settings, is_shared: presetShared }),
      });
      setPresets(await request(API + '/presets'));
      resetPresetEditor();
      setMessage('Preset created ✓ It is available for new downloads.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not create preset');
    }
  }

  async function deletePreset(id: string) {
    try {
      await request(API + '/presets/' + id, { method: 'DELETE' });
      setPresets(current => current.filter(item => item.id !== id));
      if (selectedPresetId === id) setSelectedPresetId('');
      if (editingPresetId === id) resetPresetEditor();
      setMessage('Preset deleted.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not delete preset');
    }
  }

  function resetRuleEditor() {
    ruleSaveQueue.current?.dispose();
    ruleSaveQueue.current = null;
    setRuleSaveState('saved');
    setRuleValidationError('');
    setEditingRuleId(null);
    setRuleHost('');
    setRulePath('/');
    setRuleEngine('yt-dlp');
    setRulePriority('100');
    setRulePriorityEdited(false);
    setRuleDescription('');
    setRuleEnabled(true);
  }

  function editEngineRule(rule: EngineRule) {
    ruleSaveQueue.current?.dispose();
    const queue = new EngineAutosaveQueue<EngineRuleDraft>(
      async draft => {
        const saved = await request(API + '/engine-rules/' + encodeURIComponent(rule.id), {
          method: 'PUT', body: JSON.stringify(draft),
        }) as EngineRule;
        setEngineRules(previous => previous.map(row => row.id === saved.id ? saved : row)
          .sort((left, right) => right.priority - left.priority));
      }, setRuleSaveState, 800,
    );
    ruleSaveQueue.current = queue;
    queue.hydrate({host_suffix: rule.host_suffix, path_prefix: rule.path_prefix,
      engine: rule.engine, priority: rule.priority, enabled: rule.enabled,
      description: rule.description});
    setRuleValidationError('');
    setEditingRuleId(rule.id);
    setRuleHost(rule.host_suffix);
    setRulePath(rule.path_prefix);
    setRuleEngine(rule.engine);
    setRulePriority(String(rule.priority));
    setRulePriorityEdited(true);
    setRuleDescription(rule.description);
    setRuleEnabled(rule.enabled);
    document.getElementById('rule-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function reloadRules() {
    setEngineRules(await request(API + '/engine-rules'));
  }

  async function saveEngineRule(event: React.FormEvent) {
    event.preventDefault();
    if (ruleBusy || editingRuleId) return; // Existing rules autosave; creation remains explicit.
    setRuleBusy(true);
    const editingId = editingRuleId;
    try {
      const created = await request(API + '/engine-rules' + (editingId ? '/' + editingId : ''), {
        method: editingId ? 'PUT' : 'POST',
        body: JSON.stringify({
          host_suffix: ruleHost, path_prefix: rulePath, engine: ruleEngine,
          priority: rulePriorityEdited ? Number(rulePriority) : 1000,
          enabled: ruleEnabled, description: ruleDescription,
        }),
      }) as EngineRule;
      // A normal new rule goes to the top, keeping the displayed order equal
      // to the matcher's numeric priority. Explicit Advanced priority bypasses it.
      if (!editingId && !rulePriorityEdited && engineRules.length) {
        const ids = [created.id, ...engineRules.map(item => item.id)];
        await request(API + '/engine-rules/order', {
          method: 'PUT', body: JSON.stringify({ids}),
        });
      }
      await reloadRules();
      resetRuleEditor();
      setMessage(editingId ? 'Rule saved ✓ New downloads use the updated rule.'
        : 'Rule created ✓ Automatic downloads now consider this site.');
    } catch (error) {
      await reloadRules().catch(() => undefined);
      setMessage(error instanceof Error ? error.message : 'Could not save the rule.');
    } finally { setRuleBusy(false); }
  }

  async function persistRuleOrder(next: EngineRule[]) {
    if (ruleBusy || !isReordered(engineRules, next)) return;
    setRuleBusy(true);
    setEngineRules(next);
    try {
      const saved = await request(API + '/engine-rules/order', {
        method: 'PUT', body: JSON.stringify({ids: next.map(item => item.id)}),
      }) as EngineRule[];
      setEngineRules(saved);
      setMessage('Saved ✓ Routing order updated. New downloads follow this order.');
    } catch (error) {
      await reloadRules().catch(() => undefined);
      setMessage(error instanceof Error ? error.message : 'Could not save routing order; changes were reloaded.');
    } finally {
      setRuleBusy(false);
      setDraggedRuleId(null);
      setRuleOverId(null);
    }
  }

  async function toggleEngineRule(rule: EngineRule, enabled: boolean) {
    if (ruleBusy) return;
    setRuleBusy(true);
    // Controlled checkboxes must reflect the user's click immediately.
    // Reconcile from the server on either success or failure below.
    setEngineRules(current => current.map(item =>
      item.id === rule.id ? {...item, enabled} : item));
    try {
      await request(API + '/engine-rules/' + rule.id, {
        method: 'PUT', body: JSON.stringify({
          host_suffix: rule.host_suffix, path_prefix: rule.path_prefix, engine: rule.engine,
          priority: rule.priority, enabled, description: rule.description,
        }),
      });
      await reloadRules();
      setMessage(enabled ? 'Saved ✓ Rule enabled for new downloads.' : 'Saved ✓ Rule disabled for new downloads.');
    } catch (error) {
      await reloadRules().catch(() => undefined);
      setMessage(error instanceof Error ? error.message : 'Could not change rule. Reloaded current settings.');
    } finally { setRuleBusy(false); }
  }

  async function deleteEngineRule(id: string) {
    if (ruleBusy || !window.confirm('Delete this site rule? Existing downloads are unaffected.')) return;
    setRuleBusy(true);
    try {
      await request(API + '/engine-rules/' + id, { method: 'DELETE' });
      setEngineRules(current => current.filter(item => item.id !== id));
      if (editingRuleId === id) resetRuleEditor();
      setMessage('Rule deleted. New downloads no longer use it.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not delete rule.');
    } finally { setRuleBusy(false); }
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

  async function jobAction(id: string, action: 'cancel' | 'retry' | 'redeliver') {
    try {
      await request(API + '/jobs/' + id + '/' + action, { method: 'POST' });
      if (action === 'redeliver') setMessage('Re-delivery queued using the existing verified download files.');
      await refresh();
      if (tab === 'library') await fetchLibrary(0);
    }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Job action failed'); }
  }

  async function retryFailedLibrary() {
    if (!window.confirm('Retry every failed or cancelled standalone download? This may download data again.')) return;
    setBusy(true);
    try {
      const result = await request(API + '/jobs/retry-failed', { method: 'POST' });
      setMessage(result.retried + ' failed downloads queued again.');
      await refresh(); await fetchLibrary(0);
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not retry failed downloads.'); }
    finally { setBusy(false); }
  }

  async function clearLibraryHistory() {
    if (!window.confirm('Forget all completed, failed and cancelled standalone job records? Downloaded files remain on disk, but forgotten jobs cannot be re-delivered.')) return;
    setBusy(true);
    try {
      const result = await request(API + '/jobs/history?confirm=true', { method: 'DELETE' });
      setMessage(result.deleted + ' history records cleared. Downloaded files were not deleted.');
      setSelected(null);
      await refresh(); await fetchLibrary(0);
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not clear history.'); }
    finally { setBusy(false); }
  }

  async function changeDeliveryMode(next: 'local' | 'webdav') {
    if (next === mode || deliveryModeSaving || (deliveryFieldState !== 'saved' && !deliveryDirty)) return;
    const saved = deliverySaved.current;
    setDeliveryVerified(false);
    // Never silently submit unsaved WebDAV credentials or enable an incomplete destination.
    if (deliveryDirty || !saved || (next === 'webdav' &&
        (!saved.webdav_url || !saved.username || !saved.password_configured))) {
      setMode(next);
      setDeliveryDirty(true);
      setDeliveryModeStatus('Complete the delivery configuration and save before testing.');
      return;
    }
    setMode(next);
    setDeliveryModeSaving(true);
    setDeliveryModeStatus('Saving delivery mode…');
    try {
      const result = await request(API + '/delivery', {
        method: 'PUT',
        body: JSON.stringify({mode: next, webdav_url: saved.webdav_url || null,
          username: saved.username || null, app_password: null, folder: saved.folder}),
      });
      deliverySaved.current = {...saved, mode: next, password_configured: Boolean(result.password_configured)};
      deliveryFieldQueue.current?.hydrate({mode: next, webdav_url: saved.webdav_url || null,
        username: saved.username || null, folder: saved.folder, app_password: null});
      setPasswordConfigured(Boolean(result.password_configured));
      setDeliveryModeStatus('Saved ✓ The new mode applies to new deliveries only.');
    } catch (error) {
      setMode(saved.mode);
      setDeliveryModeStatus(error instanceof Error ? 'Delivery mode not saved: ' + error.message : 'Delivery mode not saved. Try again.');
    } finally { setDeliveryModeSaving(false); }
  }

  async function saveDelivery(event: React.FormEvent) {
    event.preventDefault();
    if (deliveryModeSaving) return;
    try {
      const data = await request(API + '/delivery', {
        method: 'PUT',
        body: JSON.stringify({
          mode, webdav_url: davUrl || null, username: davUser || null,
          app_password: davPassword || null, folder: davFolder,
        }),
      });
      setPasswordConfigured(Boolean(data.password_configured));
      setDavPassword(''); setDeliveryDirty(false); setDeliveryVerified(false);
      deliveryFieldQueue.current?.hydrate({mode, webdav_url: davUrl || null,
        username: davUser || null, folder: davFolder, app_password: null});
      setDeliveryFieldError('');
      deliverySaved.current = {mode, webdav_url: davUrl, username: davUser,
        folder: davFolder, password_configured: Boolean(data.password_configured)};
      setDeliveryModeStatus('Saved ✓ Changes affect new deliveries only.');
      setMessage('Saved ✓ Test the delivery destination to confirm it works.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not save delivery settings');
    }
  }

  async function testDeliveryConnection() {
    if (deliveryDirty) {
      setMessage('Save the delivery settings before testing them.'); return;
    }
    setTestingDelivery(true); setDeliveryVerified(false);
    try {
      const tested = await request(API + '/delivery/test', {method: 'POST'});
      if (!tested.verified || !tested.cleaned_up) throw new Error('Delivery was not fully verified.');
      setDeliveryVerified(true);
      setMessage('✓ Delivery test passed: file readback and cleanup verified.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not verify the delivery destination.');
    } finally { setTestingDelivery(false); }
  }

  const verifiedDownload = firstCompletedVerified || jobs.some(job => job.status === 'completed'
    && job.output_files?.length && job.output_files.every(file => file.verified));
  let sourceHint = '';
  if (url.trim()) {
    try {
      const parsed = new URL(url.trim());
      if (['http:', 'https:', 'ftp:', 'sftp:'].includes(parsed.protocol) && parsed.hostname) {
        sourceHint = '✓ Link detected: ' + parsed.hostname.replace(/^www\./, '');
      } else if (parsed.protocol === 'magnet:') {
        sourceHint = '✓ Magnet link detected';
      } else {
        sourceHint = 'Enter a public HTTP(S), FTP, SFTP or magnet link.';
      }
    } catch {
      sourceHint = 'Enter a full link beginning with https:// or another supported protocol.';
    }
  }
  const shownJobs = tab === 'library' ? libraryJobs : jobs.slice(0, 10);
  const galleryOptionsValid = (() => {
    try { parseOptionsJson(galleryOptionsText); return true; }
    catch { return false; }
  })();

  return <div className="shell">
    <header className="topbar">
      <div className="brandmark">D</div>
      <div><div className="brand">Downloader Server</div><div className="subbrand">Server-side media downloads</div></div>
      <div className="top-actions"><span className={connected ? "status status-completed" : "status"}>{connected ? "Connected ✓" : "Not connected"}</span></div>
    </header>

    {message && <div className="notice" role="status">{message}</div>}
    <nav className="tabs" aria-label="Main navigation">
      {([['download','Download'],['library','Library'],['my-settings','My settings']] as const).map(([view,title]) =>
        <button key={view} className={tab === view ? 'active' : ''} onClick={() => navigate(view)}>{title}</button>)}
      {connected && <button className={tab === 'administration' ? 'active' : ''} onClick={() => navigate('administration')}>Administration</button>}
    </nav>
    {!connected && tab !== 'my-settings' && <section className="panel guidance" role="status">
      <h2>Connect your downloader to get started</h2>
      <p>The service needs an administrator API key before it can download links or show your history.</p>
      <button className="primary" onClick={() => navigate('my-settings')}>Connect in My settings</button>
    </section>}
    {connected && historyChecked && !verifiedDownload && <section className="panel first-run" aria-label="Set up Downloader">
      <h2>Get ready for your first download</h2>
      <p className="muted">Complete these steps, then try a small public test file. The guide disappears after your first verified download.</p>
      <ol>
        <li><strong>✓ Connected to Downloader</strong><p>Your administrator API key works.</p></li>
        <li><strong>{deliveryVerified ? '✓ ' : '② '}Choose where files go</strong>
          <p>Save the local folder or WebDAV destination, then run its actual delivery test.</p>
          <button className="secondary" type="button" onClick={() => {navigate('administration');setAdminArea('delivery');}}>
            {deliveryVerified ? 'Review delivery' : 'Set up and test delivery'}
          </button></li>
        <li><strong>{accessAcknowledged ? '✓ ' : '③ '}Decide who can download</strong>
          <p>Standalone Downloader currently uses one administrator API key. Only people with that key have access. Nextcloud user permissions are configured separately.</p>
          <label className="check"><input type="checkbox" checked={accessAcknowledged}
            onChange={event => {
              setAccessAcknowledged(event.target.checked);
              if (event.target.checked) localStorage.setItem('downloader-admin-access-ack', 'yes');
              else localStorage.removeItem('downloader-admin-access-ack');
            }} /> I understand who can access this service</label></li>
        <li><strong>④ Download something first</strong><p>A small public PDF tests the complete download and delivery flow.</p>
          <button className="primary" type="button" disabled={!deliveryVerified || !accessAcknowledged}
            onClick={() => {setEngine('generic');setUrl('https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf');navigate('download');}}>
            Try an example download
          </button></li>
      </ol>
    </section>}
    {tab === 'my-settings' && <section className="panel connection-settings">
      <h2>Service connection</h2>
      <p className="muted">Connect this standalone browser with the administrator API key. Your key stays in this browser.</p>
      <div className="keyrow">
        <label>API key<input type="password" value={connectionDraft} onChange={e => setConnectionDraft(e.target.value)} autoComplete="current-password" /></label>
        <button type="button" className="primary" disabled={checkingConnection} onClick={() => void checkConnection()}>{checkingConnection ? 'Checking…' : connected ? 'Check connection' : 'Connect'}</button>
        {connected && <button type="button" className="secondary" onClick={disconnect}>Disconnect</button>}
      </div><p role="status">{connected ? 'Connected ✓' : 'Not connected — enter your API key.'}</p>
    </section>}
    {tab === 'administration' && connected && <>
      <nav className="admin-nav" aria-label="Administration sections">
        {([['connection','Connection'],['delivery','Delivery'],['engines','Engines'],['automation','Automation'],['access','Who can download']] as const).map(([area,title]) =>
          <button key={area} className={adminArea === area ? 'active' : ''} onClick={() => setAdminArea(area)}>{title}</button>)}
      </nav>
      {adminArea === 'connection' && <section className="panel">
        <h2>Service connection</h2><p>Connected ✓ This administrator API key controls the standalone service.</p>
        <button className="secondary" onClick={() => void checkConnection()}>Check connection</button>
        <p className="muted">Developer tools and API examples:</p>
        <a href="/api-reference" target="_blank" rel="noreferrer">For developers: API reference</a>
      </section>}
      {adminArea === 'access' && <section className="panel">
        <h2>Who can download</h2>
        <p className="muted">Standalone access currently uses one administrator API key. Anyone with that key can administer the service. For individual user entitlements, use the Nextcloud integration.</p>
        <button className="secondary" onClick={() => navigate('my-settings')}>Manage this browser's connection</button>
      </section>}
    </>}
    {(tab === 'download' || tab === 'library') && connected && <div className={'layout' + (tab === 'library' ? ' library-layout' : '')}>
      {tab === 'download' && <>
      <section className="panel submit-panel">
        <div className="eyebrow">NEW DOWNLOAD</div><h2>What would you like to download?</h2>
        <p className="muted">Paste a link and your download will appear below.</p>
        <form onSubmit={event => {
          if (linkMode === 'many') { event.preventDefault(); void submitBatch(); }
          else void submit(event);
        }}>
          <div className="mode-switch" role="group" aria-label="Number of links">
            <button type="button" className={linkMode === 'one' ? 'active' : ''} onClick={() => setLinkMode('one')}>One link</button>
            <button type="button" className={linkMode === 'many' ? 'active' : ''} onClick={() => setLinkMode('many')}>Many links</button>
          </div>
          {linkMode === 'one' ? <div>
            <label>Link to download<div className="url-entry">
              <input type="url" className={sourceHint.startsWith('✓') ? 'valid-link' : ''} value={url} onChange={event => setUrl(event.target.value)} placeholder="https://example.com/video" required />
              <button className="secondary" type="button" onClick={() => void navigator.clipboard.readText().then(setUrl).catch(() =>
                setMessage('Clipboard access was blocked. Paste the link into the field instead.'))}>Paste</button>
            </div></label>
            {sourceHint && <p className={sourceHint.startsWith('✓') ? 'input-valid' : 'input-help'} role="status">{sourceHint}</p>}
          </div> : <div>
            <label>One link per line — up to 50<textarea rows={6} required value={batchUrls} onChange={event => setBatchUrls(event.target.value)}
              placeholder={'https://example.com/video-1\\nhttps://example.com/video-2'} /></label>
            <p className="muted">If any link is invalid, none of the downloads start.</p>
          </div>}
          <details className="download-options">
            <summary>Options <span className="muted">Engine, preset and download choices</span></summary>
          <label>Engine<select value={engine} onChange={event => { setEngine(event.target.value as EngineSelection); setSelectedPresetId(''); }}>
            <option value="auto">Automatic</option>
            {engineStatus.filter(item => item.installed).map(item =>
              <option key={item.name} value={item.name}>{item.name}</option>)}
          </select></label>
          <label>Option preset<select value={selectedPresetId} onChange={event => setSelectedPresetId(event.target.value)}>
            <option value="">No preset</option>
            {presets.filter(preset => engine === 'auto' || preset.engine === engine).map(preset => (
              <option key={preset.id} value={preset.id}>{preset.name} ({preset.engine})</option>
            ))}
          </select></label>
          {engine === 'yt-dlp' ? <div className="options-block">
            <div className="field-title">Per-download overrides</div>
            <QualityControl value={ytOverride.format} onChange={format => setYtOverride(value => ({ ...value, format }))} />
            <label>Subtitle languages<input placeholder="en,he,el" value={ytOverride.subtitles_lang} onChange={event => setYtOverride(value => ({ ...value, subtitles_lang: event.target.value }))} /></label>
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
            <details className="advanced-options"><summary>Advanced — site-specific gallery options</summary><p className="muted">Leave as default unless a site-specific extractor setting is required.</p><details className="advanced-options"><summary>Advanced — site-specific gallery options</summary><p className="muted">Leave as default unless a site-specific extractor setting is required.</p><label>Advanced extractor options (JSON object)<textarea rows={4} value={galleryOverrideOptionsText} onChange={event => setGalleryOverrideOptionsText(event.target.value)} /></label></details></details>
            <label>Cookie file<select value={galleryOverride.cookies_file ?? ''} onChange={event => setGalleryOverride(value => ({ ...value, cookies_file: event.target.value }))}>
              <option value="">No cookie file</option>{cookies.map(file => <option key={file.filename} value={file.filename}>{file.filename}</option>)}
            </select></label>
            <label>Proxy URL<input value={galleryOverride.proxy ?? ''} onChange={event => setGalleryOverride(value => ({ ...value, proxy: event.target.value }))} placeholder="Optional proxy" /></label>
          </div> : engine === 'you-get' ? <div className="options-block">
            <div className="field-title">you-get overrides</div>
            <CommonOptionFields fields={optionCatalog['you-get']} values={youGetOptionValues(youGetOverride)} onChange={(key, value) => updateYouGetOption(key, value, false)} />
          </div> : engine === 'get-sauce' ? <div className="options-block">
            <div className="field-title">get-sauce per-download overrides</div>
            <CommonOptionFields fields={optionCatalog['get-sauce']} values={getSauceOptionValues(getSauceOverride)} onChange={(key, value) => updateGetSauceOption(key, value, false)} />
          </div> : engine === 'svtplay-dl' ? <div className="options-block">
            <div className="field-title">svtplay-dl per-download overrides</div>
            <CommonOptionFields fields={optionCatalog['svtplay-dl']} values={svtOptionValues(svtOverride)} onChange={(key, value) => updateSvtOption(key, value, false)} />
          </div> : engine === 'generic' ? <div className="options-block">
            <div className="field-title">Direct download overrides</div>
            <CommonOptionFields fields={optionCatalog.generic} values={genericOptionValues(genericOverride)} onChange={(key, value) => updateGenericOption(key, value, false)} />
          </div> : <div className="options-block">
            <p className="muted">Automatic picks the right tool for the link. Change only if a download fails.</p>
          </div>}
          </details>
          <button className="primary download-submit" disabled={busy || !apiKey || (linkMode === 'one' ? !url.trim() || !sourceHint.startsWith('✓') : !batchUrls.trim())}>
            {busy ? 'Starting…' : linkMode === 'one' ? 'Download' : 'Add up to 50 links'}
          </button>
        </form>
      </section></>}

      <section className="panel queue-panel">
        <div className="section-heading"><div><div className="eyebrow">{tab === 'library' ? 'LIBRARY' : 'LIVE QUEUE'}</div><h2>{tab === 'library' ? 'Your downloads' : 'Recent downloads'}</h2></div><span className="count">{jobs.length} jobs</span></div>
        {tab === 'library' && <>
          <div className="library-filters">
            <label>Search downloads<input type="search" value={libraryQuery} onChange={e => setLibraryQuery(e.target.value)}
              placeholder="Title, URL or filename" maxLength={128} /></label>
            <label>Show<select value={libraryFilter} onChange={e => setLibraryFilter(e.target.value as typeof libraryFilter)}>
              <option value="all">All</option><option value="active">Active</option><option value="delivered">Delivered</option><option value="failed">Failed</option>
            </select></label>
          </div>
          <div className="library-bulk-actions">
            <button className="secondary" disabled={busy} type="button" onClick={() => void retryFailedLibrary()}>Retry all failed</button>
            <button className="danger" disabled={busy} type="button" onClick={() => void clearLibraryHistory()}>Clear history</button>
            <span className="muted">Standalone jobs only. Clearing history keeps downloaded files.</span>
          </div>
          {libraryLoading && <p className="muted" role="status">Searching your Library…</p>}
        </>}
        {tab === 'download' && <button className="secondary" onClick={() => navigate('library')}>See all downloads</button>}
        {!shownJobs.length ? <div className="empty">
          <p>{libraryLoading && tab === 'library' ? 'Searching your Library…'
            : tab === 'library' && (libraryQuery || libraryFilter !== 'all') ? 'No downloads match these filters.'
            : 'Nothing here yet — your finished downloads will live here.'}</p>
          <button className="secondary" onClick={() => tab === 'library' ?
            (setLibraryQuery(''), setLibraryFilter('all')) : document.querySelector<HTMLInputElement>('.url-entry input')?.focus()}>
            {tab === 'library' ? 'Clear filters' : 'Download something'}
          </button>
        </div> : <div className="job-list">
          {shownJobs.map(job => <JobCard key={job.id} job={job} expanded={selected === job.id}
            onToggle={() => setSelected(current => current === job.id ? null : job.id)}
            onAction={operation => void jobAction(job.id, operation)} />)}
        </div>}
        {tab === 'library' && libraryHasMore && <button type="button" className="secondary load-more"
          disabled={libraryLoading} onClick={() => void fetchLibrary(libraryOffset + 200)}>
          {libraryLoading ? 'Loading…' : 'Load more downloads'}
        </button>}
      </section>
    </div>}

    {tab === 'administration' && connected && adminArea === 'engines' && <div className="settings-layout engine-settings-layout">
      <nav className="panel engine-picker" aria-label="Download engines">
        <h2>Download engines</h2>
        <p className="muted">Choose an engine to edit its defaults. You usually do not need to change these.</p>
        {ENGINE_ORDER.map(name => {
          const status = engineStatus.find(item => item.name === name);
          return <button key={name} type="button" data-engine-select={name}
            className={editingEngine === name ? 'engine-choice active' : 'engine-choice'}
            aria-pressed={editingEngine === name}
            onClick={() => setEditingEngine(name)}>
            <strong>{ENGINE_DESCRIPTIONS[name].title}</strong>
            <span className="engine-choice-description">{ENGINE_DESCRIPTIONS[name].purpose}</span>
            <span className="engine-choice-meta">{name} · {status?.installed ? 'Ready' : status ? 'Not installed' : 'Checking availability…'}
              {status?.version ? ' · ' + status.version : ''}</span>
          </button>;
        })}
      </nav>
      <div className="engine-editor-area">
        {engineStatus.find(item => item.name === editingEngine)?.installed === false && <section className="panel" role="status">
          <h2>{ENGINE_DESCRIPTIONS[editingEngine].title}</h2>
          <p>Not installed. This engine cannot be configured until its executable is installed on the server.</p>
        </section>}
        {!engineLoaded[editingEngine] &&
          engineStatus.find(item => item.name === editingEngine)?.installed !== false && <section className="panel" role="status">
            <h2>{ENGINE_DESCRIPTIONS[editingEngine].title}</h2>
            <p>{engineLoadError[editingEngine] ? 'Could not load settings. Check the service connection and reload this page.'
              : 'Loading settings…'}</p>
          </section>}
        {engineLoaded[editingEngine] && engineStatus.find(item => item.name === editingEngine)?.installed !== false &&
          <div className="engine-save-state" role="status" aria-live="polite">
            {engineSaveStatus[editingEngine] === 'saved' ? 'Saved ✓ Changes affect new downloads.'
              : engineSaveStatus[editingEngine] === 'saving' ? 'Saving changes…'
              : engineSaveStatus[editingEngine] === 'pending' ? 'Changes will save automatically…'
              : engineSaveStatus[editingEngine] === 'error' ? editingEngine === 'gallery-dl' && !galleryOptionsValid
                ? 'Invalid advanced JSON. Correct the JSON before changes can save.'
                : 'Changes not saved — check your settings or connection.'
              : 'Loading settings…'}
            {engineSaveStatus[editingEngine] === 'error' && (editingEngine !== 'gallery-dl' || galleryOptionsValid) &&
              <button className="secondary" type="button"
              onClick={() => engineSaveQueues.current[editingEngine]?.retry()}>Retry saving</button>}
          </div>}
{engineLoaded[editingEngine] && engineStatus.find(item => item.name === editingEngine)?.installed !== false &&
          editingEngine !== 'yt-dlp' && editingEngine !== 'gallery-dl' &&
          !optionCatalogLoaded[editingEngine] && <section className="panel" role="status">
            <h3>Loading available options…</h3>
          </section>}
        {engineLoaded[editingEngine] && engineStatus.find(item => item.name === editingEngine)?.installed !== false &&
          editingEngine !== 'yt-dlp' && editingEngine !== 'gallery-dl' &&
          optionCatalogLoaded[editingEngine] && !optionCatalog[editingEngine].length &&
          <section className="panel" role="status">
            <h3>{ENGINE_DESCRIPTIONS[editingEngine].title} — Ready</h3>
            <p>There are no additional settings to configure for this engine.</p>
          </section>}
        {editingEngine === 'yt-dlp' && engineLoaded['yt-dlp'] &&
          engineStatus.find(item => item.name === 'yt-dlp')?.installed !== false &&
          <section className="panel engine-editor" data-engine="yt-dlp">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>yt-dlp</h2><p className="muted">{engineVersion('yt-dlp')}. Global defaults apply to new jobs; per-download options take precedence.</p>
        <QualityControl value={yt.format} onChange={format => setYt(value => ({ ...value, format }))} />
        <h3>Playlists</h3>
        <div className="checks"><label className="check"><input type="checkbox" checked={yt.allow_playlist} onChange={event => setYt(value => ({ ...value, allow_playlist: event.target.checked, playlist_items: event.target.checked ? value.playlist_items : '' }))} /> Download playlists by default</label></div><p className="muted">When off, a playlist link downloads only its first item.</p>
        {yt.allow_playlist && <label>Default playlist selection (optional)<input value={yt.playlist_items} onChange={event => setYt(value => ({ ...value, playlist_items: event.target.value }))} placeholder="e.g. 1-5 or leave empty for all" /></label>}

        <h3>Site access (cookies)</h3>
        <label>Default cookie file<select value={yt.cookies_file ?? ''} onChange={event => setYt(value => ({ ...value, cookies_file: event.target.value }))}><option value="">None</option>{cookies.map(file => <option key={file.filename} value={file.filename}>{file.filename}</option>)}</select></label>
        <label>Upload browser-exported cookies<input type="file" accept=".txt,.cookies,text/plain" onChange={event => { const file = event.target.files?.[0]; if (file) void uploadCookie(file); event.target.value = ''; }} /></label>
        <div className="muted">Some sites only show full quality to signed-in users. Upload a cookies file exported from your browser; it stays on the server, private.</div>
        <h3>Subtitles</h3>
        <label>Subtitle languages<input placeholder="en,he,el" value={yt.subtitles_lang} onChange={event => setYt(value => ({ ...value, subtitles_lang: event.target.value }))} /></label>
        <p className="muted">Enter subtitle language codes separated by commas when the site offers captions.</p>
        <div className="checks">
          <label className="check"><input type="checkbox" checked={yt.write_subtitles} onChange={event => setYt(value => ({ ...value, write_subtitles: event.target.checked }))} /> Download subtitles by default</label>
          <label className="check"><input type="checkbox" checked={yt.embed_subtitles} onChange={event => setYt(value => ({ ...value, embed_subtitles: event.target.checked }))} /> Embed subtitles by default</label>
        </div>
        <p className="muted">Download keeps a separate caption track; Embed includes supported captions in the media file.</p>
        <details className="advanced-options"><summary>Advanced — additional arguments</summary><p className="muted">Use only reviewed yt-dlp flags. Leave empty for normal downloads.</p><label>Additional arguments (one per line)<textarea rows={4} value={yt.extra_args.join('\n')} onChange={event => setYt(value => ({ ...value, extra_args: event.target.value.split('\n').map(item => item.trim()).filter(Boolean) }))} /></label></details>
        
      </section>}
{editingEngine === 'aria2c' && engineLoaded['aria2c'] &&
          engineStatus.find(item => item.name === 'aria2c')?.installed !== false &&
          optionCatalogLoaded['aria2c'] && optionCatalog['aria2c'].length > 0 &&
          <section className="panel engine-editor" data-engine="aria2c">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>aria2c</h2><p className="muted">{engineVersion('aria2c')}. These settings map to aria2 options; RPC access and output paths remain service-managed.</p>
        <CommonOptionFields fields={optionCatalog.aria2c} values={aria.options} onChange={(key, value) => updateAriaOption(key, value, true)} />
        <button className="secondary" onClick={() => setAria(ariaDefaults)}>Reset shown defaults</button>
        
      </section>}
{editingEngine === 'gallery-dl' && engineLoaded['gallery-dl'] &&
          engineStatus.find(item => item.name === 'gallery-dl')?.installed !== false &&
          <section className="panel engine-editor" data-engine="gallery-dl">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>gallery-dl</h2>
        <p className="muted">{engineVersion('gallery-dl')}. Common extractor options are graphical; site-specific options remain available in the advanced JSON editor. The service controls the output root.</p>
        <CommonOptionFields fields={optionCatalog['gallery-dl']} values={catalogValues(galleryOptionsText)} onChange={(key, value) => updateGalleryOption(key, value, true)} />
        <details className="advanced-options"><summary>Advanced — site-specific gallery options</summary>
          <p className="muted">Only edit JSON if this site needs settings not shown above. Invalid JSON is never saved.</p>
          <label>Advanced extractor options (JSON object)<textarea rows={8} value={galleryOptionsText} onChange={event => setGalleryOptionsText(event.target.value)} /></label>
        </details>
        <label>Default cookie file<select value={gallery.cookies_file ?? ''} onChange={event => setGallery(value => ({ ...value, cookies_file: event.target.value }))}>
          <option value="">None</option>{cookies.map(file => <option key={file.filename} value={file.filename}>{file.filename}</option>)}
        </select></label>
        <label>Default proxy URL<input value={gallery.proxy ?? ''} onChange={event => setGallery(value => ({ ...value, proxy: event.target.value }))} placeholder="Optional proxy" /></label>
        <button className="secondary" onClick={() => { setGallery(galleryDefaults); setGalleryOptionsText('{}'); }}>Reset shown defaults</button>
        
      </section>}
{editingEngine === 'generic' && engineLoaded['generic'] &&
          engineStatus.find(item => item.name === 'generic')?.installed !== false &&
          optionCatalogLoaded['generic'] && optionCatalog['generic'].length > 0 &&
          <section className="panel engine-editor" data-engine="generic">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>generic / curl</h2>
        <p className="muted">{engineVersion('generic')}. For direct HTTP(S) links. Redirects are disabled by default.</p>
        <CommonOptionFields fields={optionCatalog.generic} values={genericOptionValues(generic)} onChange={(key, value) => updateGenericOption(key, value, true)} />
        
      </section>}
{editingEngine === 'you-get' && engineLoaded['you-get'] &&
          engineStatus.find(item => item.name === 'you-get')?.installed !== false &&
          optionCatalogLoaded['you-get'] && optionCatalog['you-get'].length > 0 &&
          <section className="panel engine-editor" data-engine="you-get">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>you-get</h2>
        <p className="muted">{engineVersion('you-get')}. P1 fallback extractor with optional cookie file and format selection.</p>
        <CommonOptionFields fields={optionCatalog['you-get']} values={youGetOptionValues(youGet)} onChange={(key, value) => updateYouGetOption(key, value, true)} />
        
      </section>}
{editingEngine === 'svtplay-dl' && engineLoaded['svtplay-dl'] &&
          engineStatus.find(item => item.name === 'svtplay-dl')?.installed !== false &&
          optionCatalogLoaded['svtplay-dl'] && optionCatalog['svtplay-dl'].length > 0 &&
          <section className="panel engine-editor" data-engine="svtplay-dl">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>svtplay-dl</h2>
        <p className="muted">{engineVersion('svtplay-dl')}. Nordic video services; output path remains service-managed.</p>
        <CommonOptionFields fields={optionCatalog['svtplay-dl']} values={svtOptionValues(svt)} onChange={(key, value) => updateSvtOption(key, value, true)} />
        <button className="secondary" onClick={() => setSvt(svtDefaults)}>Reset shown defaults</button>
        
      </section>}
{editingEngine === 'get-sauce' && engineLoaded['get-sauce'] &&
          engineStatus.find(item => item.name === 'get-sauce')?.installed !== false &&
          optionCatalogLoaded['get-sauce'] && optionCatalog['get-sauce'].length > 0 &&
          <section className="panel engine-editor" data-engine="get-sauce">
        <div className="eyebrow">ENGINE DEFAULTS</div><h2>get-sauce</h2>
        <p className="muted">{engineVersion('get-sauce')}. Specialized media/gallery downloads, managed output directory.</p>
        <CommonOptionFields fields={optionCatalog['get-sauce']} values={getSauceOptionValues(getSauce)} onChange={(key, value) => updateGetSauceOption(key, value, true)} />
        <button className="secondary" onClick={() => setGetSauce(getSauceDefaults)}>Reset shown defaults</button>
        
      </section>}
      </div>
      <div className="hint panel"><strong>Advanced options</strong><p>Service-managed paths, cookie arguments, progress reporting, and process execution flags cannot be overridden.</p></div>
    </div>}

    {((tab === 'my-settings' && connected) || (tab === 'administration' && connected && adminArea === 'automation')) && <div className="settings-layout">
      {tab === "my-settings" && <><section className="panel" id="preset-editor">
        <div className="eyebrow">SAVED OPTIONS</div><h2>{editingPresetId ? 'Edit preset' : 'Create a preset'}</h2>
        <p className="muted">A preset stores engine-specific settings. Job-level overrides take precedence over preset settings.
          Changes to an existing preset save automatically and affect new downloads.</p>
        {editingPresetId && <div className="engine-save-state" role="status" aria-live="polite">
          {presetValidationError || (
            presetSaveState === 'saved' ? 'Saved ✓ Changes affect new downloads.'
              : presetSaveState === 'pending' ? 'Changes will save automatically…'
              : presetSaveState === 'saving' ? 'Saving changes…'
              : presetSaveState === 'error' ? 'Changes not saved. Check settings and retry.'
              : 'Loading preset…'
          )}
          {presetSaveState === 'error' && !presetValidationError &&
            <button type="button" className="secondary"
              onClick={() => presetSaveQueue.current?.retry()}>Retry saving</button>}
        </div>}
        <form onSubmit={savePreset}>
          <label>Preset name<input value={presetName} onChange={event => setPresetName(event.target.value)} required maxLength={120} /></label>
          <label>Engine<select value={presetEngine} onChange={event => {setPresetEngine(event.target.value as Engine); setPresetSettingsText('{}');}}>
            <option value="yt-dlp">yt-dlp</option><option value="aria2c">aria2c</option><option value="gallery-dl">gallery-dl</option><option value="generic">generic / curl</option><option value="you-get">you-get</option><option value="svtplay-dl">svtplay-dl</option><option value="get-sauce">get-sauce</option>
          </select></label>
          <label>Description<input value={presetDescription} onChange={event => setPresetDescription(event.target.value)} maxLength={1000} /></label>
          <label className="check"><input type="checkbox" checked={presetShared}
            onChange={event => setPresetShared(event.target.checked)} />
            Available to everyone (Nextcloud)</label>
          <p className="muted">When enabled, entitled Nextcloud Downloader users can choose this preset.
            Their personal presets remain private. Cookie files, proxy URLs, and custom CLI arguments
            cannot be included in a shared preset.</p>
          <PresetOptionEditor engine={presetEngine} settingsText={presetSettingsText}
            fields={optionCatalog[presetEngine]} onChange={setPresetSettingsText} onError={setMessage} />
          <details className="advanced-options"><summary>Edit as JSON (advanced)</summary>
            <p className="muted">Only use raw JSON for options not shown in the visual editor.</p>
            <label>Settings JSON<textarea rows={7} value={presetSettingsText}
              onChange={event => setPresetSettingsText(event.target.value)} /></label>
          </details>
          {!editingPresetId && <button className="primary">Create preset</button>}
          {editingPresetId && <button type="button" className="secondary"
            disabled={presetSaveState !== 'saved'} onClick={resetPresetEditor}>Finish editing</button>}
        </form>
      </section>
      <section className="panel">
        <div className="eyebrow">PRESET LIBRARY</div><h2>Saved presets</h2>
        {!presets.length ? <div className="empty">No presets saved.</div> : <div className="job-list">
          {presets.map(preset => <article key={preset.id} className="job">
            <div className="job-head"><div className="job-title"><span className="engine-pill">{preset.engine}</span>
              <strong>{preset.name}</strong></div><span className="status">{preset.is_shared ? 'Available to everyone' : 'Administrator only'}</span></div>
            {preset.description && <p className="muted">{preset.description}</p>}
            <details><summary>Advanced preset details</summary><pre className="log">{JSON.stringify(preset.settings, null, 2)}</pre></details>
            <div className="job-actions">
              <button className="small" onClick={() => editPreset(preset)}>Edit</button>
              <button className="small danger" onClick={() => void deletePreset(preset.id)}>Delete</button>
            </div>
          </article>)}
        </div>}
      </section>
      </>}
      {tab === "administration" && <>
        <section className="panel" id="rule-editor">
          <div className="eyebrow">AUTOMATIC ENGINE SELECTION</div>
          <h2>{editingRuleId ? 'Edit site rule' : 'Add a site rule'}</h2>
          <p className="muted">Choose a website and the downloader to use for links from that site.
            A site rule also matches its subdomains. Higher rules take precedence.</p>
          {editingRuleId && <div className="engine-save-state" role="status" aria-live="polite">
            {ruleValidationError || (ruleSaveState === 'saved' ? 'Saved ✓ Changes affect new downloads.'
              : ruleSaveState === 'pending' ? 'Changes will save automatically…'
              : ruleSaveState === 'saving' ? 'Saving changes…'
              : ruleSaveState === 'error' ? 'Changes not saved. Check the rule and retry.'
              : 'Loading rule…')}
            {ruleSaveState === 'error' && !ruleValidationError &&
              <button type="button" className="secondary" onClick={() => ruleSaveQueue.current?.retry()}>Retry saving</button>}
          </div>}
          <form onSubmit={saveEngineRule}>
            <label>Website<input value={ruleHost} onChange={event => setRuleHost(event.target.value)}
              placeholder="soundcloud.com" autoComplete="off" required maxLength={255} /></label>
            <label>Download with<select value={ruleEngine} onChange={event => setRuleEngine(event.target.value as Engine)}>
              <option value="yt-dlp">yt-dlp — video and audio</option>
              <option value="aria2c">aria2c — direct files and torrents</option>
              <option value="gallery-dl">gallery-dl — galleries</option>
              <option value="generic">Direct file (curl)</option>
              <option value="you-get">you-get — video fallback</option>
              <option value="svtplay-dl">svtplay-dl — Nordic television</option>
              <option value="get-sauce">get-sauce — specialized media</option>
            </select></label>
            <label>Note (optional)<input value={ruleDescription}
              onChange={event => setRuleDescription(event.target.value)} maxLength={255}
              placeholder="Example: use this for audio tracks" /></label>
            <label className="check"><input type="checkbox" checked={ruleEnabled}
              onChange={event => setRuleEnabled(event.target.checked)} /> Enable rule</label>
            <details className="advanced-options">
              <summary>Advanced — URL path and numeric priority</summary>
              <p className="muted">Normally the order of cards decides priority. Limit a rule to a URL path
                or enter a custom numeric priority only if necessary.</p>
              <label>URL path prefix<input value={rulePath}
                onChange={event => setRulePath(event.target.value)} placeholder="/" /></label>
              <label>Priority (0–1000)<input type="number" min="0" max="1000"
                value={rulePriority} onChange={event => {
                  setRulePriority(event.target.value); setRulePriorityEdited(true);
                }} /></label>
            </details>
            <div className="rule-form-actions">
              {!editingRuleId && <button className="primary" disabled={ruleBusy}>
                {ruleBusy ? 'Saving…' : 'Add site rule'}
              </button>}
              {editingRuleId && <button type="button" className="secondary"
                onClick={resetRuleEditor} disabled={ruleBusy || ruleSaveState !== 'saved'}>Finish editing</button>}
            </div>
          </form>
        </section>
        <section className="panel">
          <div className="eyebrow">ROUTING RULES</div><h2>Which downloader handles each site?</h2>
          <p className="muted">Drag the handle or use Move up / Move down to change order. The highest enabled matching
            rule is used for new downloads; existing jobs are not changed.</p>
          {ruleBusy && <p role="status" className="muted">Saving routing settings…</p>}
          {!engineRules.length
            ? <div className="empty">No site rules yet. Automatic mode uses yt-dlp by default,
                or aria2c for FTP and magnet links.</div>
            : <div className="rule-order-list" aria-label="Ordered site rules">
              {engineRules.map((rule, index) => <article key={rule.id}
                className={'rule-card' + (ruleOverId === rule.id ? ' drop-target' : '')}
                data-rule-id={rule.id}
                onDragOver={event => {
                  if (ruleBusy || !draggedRuleId) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = 'move';
                  if (ruleOverId !== rule.id) setRuleOverId(rule.id);
                }}
                onDragLeave={() => setRuleOverId(current => current === rule.id ? null : current)}
                onDrop={event => {
                  event.preventDefault();
                  const source = draggedRuleId;
                  setDraggedRuleId(null); setRuleOverId(null);
                  if (!source || ruleBusy) return;
                  const box = event.currentTarget.getBoundingClientRect();
                  const location = event.clientY > box.top + box.height / 2 ? 'after' : 'before';
                  void persistRuleOrder(moveRule(engineRules, source, rule.id, location));
                }}>
                <div className="rule-head">
                  <button type="button" className="rule-drag" draggable={!ruleBusy}
                    title="Drag to reorder"
                    aria-label={'Drag to reorder rule for ' + rule.host_suffix}
                    onDragStart={event => {
                      if (ruleBusy) {event.preventDefault();return;}
                      setDraggedRuleId(rule.id);
                      event.dataTransfer.effectAllowed = 'move';
                      event.dataTransfer.setData('text/plain', rule.id);
                    }}
                    onDragEnd={() => {setDraggedRuleId(null);setRuleOverId(null);}}>⋮⋮</button>
                  <div className="rule-label">
                    <strong>{rule.host_suffix} → {rule.engine}</strong>
                    {rule.description && <p className="muted">{rule.description}</p>}
                    {rule.path_prefix !== '/' && <small className="muted">Applies to path: {rule.path_prefix}</small>}
                  </div>
                  <label className="rule-enabled"><input type="checkbox" checked={rule.enabled}
                    disabled={ruleBusy}
                    onChange={event => void toggleEngineRule(rule, event.target.checked)} />
                    {rule.enabled ? 'Enabled' : 'Disabled'}</label>
                </div>
                <div className="rule-actions">
                  <button className="small secondary" disabled={ruleBusy || index === 0} type="button"
                    aria-label={'Move ' + rule.host_suffix + ' up'}
                    onClick={() => void persistRuleOrder(stepRule(engineRules, rule.id, -1))}>Move up</button>
                  <button className="small secondary" disabled={ruleBusy || index === engineRules.length - 1}
                    type="button" aria-label={'Move ' + rule.host_suffix + ' down'}
                    onClick={() => void persistRuleOrder(stepRule(engineRules, rule.id, 1))}>Move down</button>
                  <button className="small secondary" disabled={ruleBusy} type="button"
                    onClick={() => editEngineRule(rule)}>Edit</button>
                  <button className="small danger" disabled={ruleBusy} type="button"
                    onClick={() => void deleteEngineRule(rule.id)}>Delete</button>
                  <details className="rule-advanced"><summary>Advanced</summary>
                    <span>Priority {rule.priority} · ID {rule.id}</span>
                  </details>
                </div>
              </article>)}
            </div>}
        </section>
      </>}
    </div>}

    {tab === 'administration' && connected && adminArea === 'delivery' && <section className="panel delivery-panel">
      <div className="eyebrow">OUTPUT DESTINATION</div><h2>Where finished files go</h2>
      <p className="muted">Local folder keeps files on this server. WebDAV delivers finished files into Nextcloud Files or another WebDAV service. Changes affect new deliveries only.</p>
      <form onSubmit={saveDelivery}>
        <label>Delivery mode<select value={mode} disabled={deliveryModeSaving || (deliveryFieldState !== 'saved' && !deliveryDirty)} onChange={event => void changeDeliveryMode(event.target.value as 'local' | 'webdav')}><option value="local">Local folder</option><option value="webdav">WebDAV — Nextcloud Files</option></select></label>
        {mode === 'webdav' && <>
          <label>WebDAV base URL<input type="url" value={davUrl} onChange={event => {setDavUrl(event.target.value);if (!passwordConfigured) setDeliveryDirty(true);setDeliveryVerified(false);}} placeholder="https://cloud.example.com/remote.php/dav/files/username" required /></label>
          <label>Username<input value={davUser} onChange={event => {setDavUser(event.target.value);if (!passwordConfigured) setDeliveryDirty(true);setDeliveryVerified(false);}} required /></label>
          <label>App password<input type="password" value={davPassword} onChange={event => {setDavPassword(event.target.value);setDeliveryDirty(true);deliveryFieldQueue.current?.pause();setDeliveryVerified(false);}} placeholder={passwordConfigured ? 'Stored; leave blank to keep' : 'Enter app password'} autoComplete="new-password" /></label>
          <label>Destination folder<input value={davFolder} onChange={event => {setDavFolder(event.target.value);if (!passwordConfigured) setDeliveryDirty(true);setDeliveryVerified(false);}} /></label>
          <div className="secure-note">{passwordConfigured ? 'An app password is stored encrypted on the server.' : 'An app password is required.'} Credentials are never returned to the browser.</div>
        </>}
        {deliveryDirty && <button className="primary" disabled={deliveryModeSaving}>Save delivery settings</button>}
        <p className="muted" role="status" aria-live="polite">{deliveryModeStatus || (deliveryDirty ? 'Password changes require explicit saving.' : 'Delivery settings save automatically when the destination is configured.')}</p>
        {mode === 'webdav' && passwordConfigured && !deliveryDirty &&
          <div className="engine-save-state" role="status" aria-live="polite">
            {deliveryFieldError || (deliveryFieldState === 'saved' ? 'Saved ✓ Changes affect new deliveries.'
              : deliveryFieldState === 'pending' ? 'Changes will save automatically…'
              : deliveryFieldState === 'saving' ? 'Saving delivery changes…'
              : 'Changes not saved. Check the destination and retry.')}
            {deliveryFieldState === 'error' && !deliveryFieldError &&
              <button className="secondary" type="button"
                onClick={() => deliveryFieldQueue.current?.retry()}>Retry saving</button>}
          </div>}
        <button className="secondary" type="button" disabled={testingDelivery || deliveryDirty || deliveryModeSaving || deliveryFieldState !== 'saved'}
          onClick={() => void testDeliveryConnection()}>{testingDelivery ? 'Testing delivery…' : 'Test delivery'}</button>
        <p className="muted" role="status">{deliveryDirty ? 'Save changes before testing' :
          deliveryVerified ? '✓ Delivery verified using a temporary file' : 'Not tested in this browser session'}</p>
      </form>
    </section>}

    <footer>Downloader Server · standalone service</footer>
  </div>;
}
