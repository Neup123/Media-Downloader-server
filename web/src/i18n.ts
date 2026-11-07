/**
 * User-facing English copy is the stable source key. Locale tables may be
 * extended without changing API payloads, persisted job records or engine IDs.
 * Unknown/unfinished entries fall back to English rather than showing keys.
 */
export type Locale = 'en' | 'el' | 'he';

const el: Record<string, string> = {
  'Download': 'Λήψη',
  'Library': 'Βιβλιοθήκη',
  'My settings': 'Οι ρυθμίσεις μου',
  'Administration': 'Διαχείριση',
  'Connection': 'Σύνδεση',
  'Delivery': 'Παράδοση',
  'Engines': 'Μηχανές λήψης',
  'Automation': 'Αυτοματισμοί',
  'Who can download': 'Ποιος μπορεί να κάνει λήψεις',
  'What would you like to download?': 'Τι θέλετε να κατεβάσετε;',
  'Paste a link and your download will appear below.': 'Επικολλήστε έναν σύνδεσμο και παρακολουθήστε τη λήψη παρακάτω.',
  'One link': 'Ένας σύνδεσμος',
  'Many links': 'Πολλοί σύνδεσμοι',
  'Link to download': 'Σύνδεσμος για λήψη',
  'Paste': 'Επικόλληση',
  'Options': 'Επιλογές',
  'Add up to 50 links': 'Προσθήκη έως 50 συνδέσμων',
  'Recent downloads': 'Πρόσφατες λήψεις',
  'Your downloads': 'Οι λήψεις σας',
  'See all downloads': 'Προβολή όλων των λήψεων',
  'Connect': 'Σύνδεση',
  'Connected ✓': 'Συνδεδεμένο ✓',
  'Not connected': 'Χωρίς σύνδεση',
  'Language': 'Γλώσσα',
  'English': 'Αγγλικά',
  'Greek': 'Ελληνικά',
  'Hebrew': 'Εβραϊκά',
};
const he: Record<string, string> = {
  'Download': 'הורדה',
  'Library': 'ספריית ההורדות',
  'My settings': 'ההגדרות שלי',
  'Administration': 'ניהול',
  'Connection': 'חיבור',
  'Delivery': 'העברת קבצים',
  'Engines': 'מנועי הורדה',
  'Automation': 'אוטומציה',
  'Who can download': 'מי יכול להוריד',
  'What would you like to download?': 'מה תרצו להוריד?',
  'Paste a link and your download will appear below.': 'הדביקו קישור ועקבו אחר ההורדה כאן.',
  'One link': 'קישור אחד',
  'Many links': 'קישורים רבים',
  'Link to download': 'קישור להורדה',
  'Paste': 'הדבקה',
  'Options': 'אפשרויות',
  'Add up to 50 links': 'הוספת עד 50 קישורים',
  'Recent downloads': 'הורדות אחרונות',
  'Your downloads': 'ההורדות שלי',
  'See all downloads': 'הצגת כל ההורדות',
  'Connect': 'חיבור',
  'Connected ✓': 'מחובר ✓',
  'Not connected': 'לא מחובר',
  'Language': 'שפה',
  'English': 'אנגלית',
  'Greek': 'יוונית',
  'Hebrew': 'עברית',
};

export const LOCALES: readonly Locale[] = ['en', 'el', 'he'];
export function normalizeLocale(value: string | null | undefined): Locale {
  const code = (value || '').toLowerCase().split(/[-_]/, 1)[0];
  return code === 'he' || code === 'iw' ? 'he' : code === 'el' ? 'el' : 'en';
}
export function isRtl(locale: Locale): boolean { return locale === 'he'; }
export function translate(locale: Locale, english: string): string {
  const dictionary: Record<string, string> = locale === 'he' ? he : locale === 'el' ? el : {};
  return dictionary[english] ?? english;
}
