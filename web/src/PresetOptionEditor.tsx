import { QualityControl } from './QualityControl';
import { validPresetSettings } from './ui-options';

type Field = {key:string;label:string;category:string;type:string;description?:string;minimum?:number;maximum?:number};
type Props = {
  engine:string; settingsText:string; fields:Field[];
  onChange:(text:string)=>void; onError:(message:string)=>void;
};
export function PresetOptionEditor({engine,settingsText,fields,onChange,onError}:Props) {
  let settings:Record<string,unknown> = {};
  try {
    const value:unknown = JSON.parse(settingsText);
    if (!validPresetSettings(value)) throw new Error('Settings must be an object');
    settings = value;
  } catch {
    return <p className="error-box">Advanced JSON is invalid. Correct it to use the visual editor.</p>;
  }
  const nested = engine === 'aria2c' || engine === 'gallery-dl';
  const options:Record<string,unknown> = nested && validPresetSettings(settings.options)
    ? settings.options : settings;
  const change = (key:string,raw:unknown) => {
    const next:Record<string,unknown> = {...options};
    if (raw === '' || raw === undefined) delete next[key];
    else next[key] = raw;
    const update = nested ? {...settings,options:next} : next;
    try { onChange(JSON.stringify(update,null,2)); } catch { onError('Could not update this option.'); }
  };
  if (engine === 'yt-dlp') return <div className="preset-visual">
    <QualityControl value={String(settings.format ?? 'bv*+ba/b')} onChange={value => change('format',value)} />
    <label>Subtitle languages<input value={String(settings.subtitles_lang ?? '')}
      onChange={e => change('subtitles_lang',e.target.value)} placeholder="en,he,el" /></label>
    <p className="muted">Language codes separated by commas. Use only if subtitles are available.</p>
    <label className="check"><input type="checkbox" checked={settings.write_subtitles === true}
      onChange={e => change('write_subtitles',e.target.checked)} /> Download subtitles</label>
    <label className="check"><input type="checkbox" checked={settings.embed_subtitles === true}
      onChange={e => change('embed_subtitles',e.target.checked)} /> Embed subtitles in video</label>
    <label className="check"><input type="checkbox" checked={settings.allow_playlist === true}
      onChange={e => change('allow_playlist',e.target.checked)} /> Download playlists</label>
    <p className="muted">When off, a playlist link downloads only its first item.</p>
  </div>;
  return <div className="preset-visual">
    {!fields.length ? <p className="muted">No additional options are needed for this engine.</p> :
      fields.map(field => <label key={field.key} title={field.description ?? ''}>{field.label}
        {field.type === 'boolean' ? <select value={options[field.key] === undefined ? '' : String(options[field.key])}
          onChange={e => change(field.key,e.target.value === '' ? '' : (nested ? e.target.value : e.target.value === 'true'))}>
          <option value="">Engine default</option><option value="true">Yes</option><option value="false">No</option>
        </select> :
          <input type={field.type === 'integer' ? 'number' : 'text'}
            min={field.minimum} max={field.maximum} value={String(options[field.key] ?? '')}
            onChange={e => change(field.key,e.target.value === '' ? '' :
              (field.type === 'integer' && !nested ? Number(e.target.value) : e.target.value))} />}
        {field.description && <span className="muted option-help">{field.description}</span>}
      </label>)}
  </div>;
}
