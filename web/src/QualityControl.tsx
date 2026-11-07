import { VIDEO_QUALITY, qualityFromFormat, formatFromQuality } from './ui-options';
export function QualityControl({value,onChange}: {value:string;onChange:(format:string)=>void}) {
  const quality = qualityFromFormat(value);
  return <div className="quality-control">
    <label>Quality
      <select value={quality} onChange={event => {
        const preset = formatFromQuality(event.target.value);
        if (preset !== null) onChange(preset);
      }}>
        {VIDEO_QUALITY.map(entry => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
        {quality === 'custom' && <option value="custom">Custom format (advanced)</option>}
      </select>
    </label>
    <p className="muted">Choose the best available quality, or limit video height.</p>
    <details className="advanced-quality">
      <summary>Advanced — custom format selector</summary>
      <label>Format selector<input value={value} onChange={event => onChange(event.target.value)} /></label>
      <p className="muted">Only change this if you understand yt-dlp format expressions.</p>
    </details>
  </div>;
}
