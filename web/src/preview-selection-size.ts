/** Honest media-selection aggregate: never invent size for flat extractor entries. */
export type SizedPreviewEntry = {
  index: number;
  size_bytes?: number | null;
  size_estimate_bytes?: number | null;
};

function labelBytes(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  const units = ['KiB','MiB','GiB','TiB'];
  let value = bytes, unit = -1;
  do {value /= 1024;unit++;} while(value >= 1024 && unit < units.length-1);
  return value.toFixed(value < 10 ? 1 : 0) + ' ' + units[unit];
}

export function previewSelectionSummary(entries: SizedPreviewEntry[], chosen: number[]): string {
  const selected = new Set(chosen);
  let count=0, withSize=0, approximations=0, unknown=0, bytes=0;
  for(const item of entries) {
    if(!selected.has(item.index))continue;
    count++;
    const exact = item.size_bytes;
    const estimated = item.size_estimate_bytes;
    const isExact = Number.isSafeInteger(exact) && typeof exact === 'number' && exact >= 0;
    const isEstimate = !isExact && Number.isSafeInteger(estimated) && typeof estimated === 'number' && estimated >= 0;
    const amount = isExact ? exact! : isEstimate ? estimated! : null;
    if(amount === null || !Number.isSafeInteger(bytes + amount)){
      unknown++;
      continue;
    }
    bytes += amount;withSize++;
    if(isEstimate)approximations++;
  }
  const head = count + ' of ' + entries.length + ' items selected';
  if(!count)return head + ' · 0 B selected';
  const chunks=[head];
  if(withSize) chunks.push((approximations ? 'About ' : '') + labelBytes(bytes) + ' for ' + withSize + ' sized item' + (withSize===1?'':'s'));
  if(unknown) chunks.push('size unknown for ' + unknown + ' item' + (unknown===1?'':'s'));
  return chunks.join(' · ');
}
