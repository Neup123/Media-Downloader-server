import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const ui = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8');

test('routing rows offer four compact accessible action controls', () => {
  const start = ui.indexOf('<div className="rule-actions"');
  const end = ui.indexOf('<details className="rule-advanced"', start);
  assert.ok(start > 0 && end > start);
  const toolbar = ui.slice(start, end);
  assert.equal((toolbar.match(/className="small (?:secondary|danger) rule-icon"/g) || []).length, 4);
  for (const action of ['up', 'down', 'Edit ', 'Delete ']) {
    assert.match(toolbar, new RegExp('aria-label=\\{[^}]*' + action));
    assert.match(toolbar, new RegExp('title=\\{[^}]*' + action));
  }
  assert.equal((toolbar.match(/aria-hidden="true"/g) || []).length, 4);
  assert.match(toolbar, /persistRuleOrder\(stepRule\(engineRules, rule.id, -1\)\)/);
  assert.match(toolbar, /persistRuleOrder\(stepRule\(engineRules, rule.id, 1\)\)/);
  assert.match(toolbar, /editEngineRule\(rule\)/);
  assert.match(toolbar, /deleteEngineRule\(rule.id\)/);
});

test('routing actions keep minimum touch targets and visible focus at mobile widths', () => {
  assert.match(styles, /\.rule-actions \.rule-icon\{[^}]*width:44px/);
  assert.match(styles, /\.rule-actions \.rule-icon\{[^}]*min-height:44px/);
  assert.match(styles, /\.rule-actions \.rule-icon:focus-visible\{[^}]*outline/);
  assert.doesNotMatch(styles, /\.rule-actions button\{flex:1 1 calc\(50%/);
});

test('finite engine catalog options are real selects, not free text', () => {
  assert.match(ui, /field\.type === 'enum' && field\.choices\?\.length/);
  assert.match(ui, /field\.choices\.map\(choice => <option/);
  assert.match(ui, /<option value="">Engine default<\/option>/);
});
