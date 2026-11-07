/**
 * English is the base product UI language. Hebrew is explicitly supported.
 * Untranslated strings fall back to English; no other locales are enabled.
 * Locale choice applies to UI only, never to URLs or API data.
 */
export type Locale = 'en' | 'he';

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
  'Hebrew': 'עברית',
  'Delivered ✓ Verified': 'הועבר ✓ אומת',
  'Completed — checking delivery': 'הושלם — בודקים העברה',
  'Failed — ': 'נכשל — ',
  'Check the log and try again': 'בדקו את יומן הפעולות ונסו שוב',
  'Cancelled': 'בוטל',
  'Downloading': 'מוריד',
  'Delivering to your destination': 'מעביר אל יעד הקבצים',
  'Queued': 'ממתין בתור',
  'Cancel': 'ביטול',
  'Try again': 'ניסיון חוזר',
  'Re-deliver': 'העברה מחדש',
  'Open in Files': 'פתיחה בקבצים',
  'Details': 'פרטים',
  'Hide details': 'הסתרת פרטים',
  'Details for ': 'פרטים על ',
  'Download progress for ': 'התקדמות הורדת ',
  'downloaded': 'הורדו',
  'Delivered files': 'קבצים שהועברו',
  '✓ Verified': '✓ אומת',
  'Not verified': 'לא אומת',
  'Technical log (advanced)': 'יומן טכני (מתקדם)',
  'No log output yet.': 'עדיין אין רישום ביומן.',
};

export const LOCALES: readonly Locale[] = ['en', 'he'];

export function normalizeLocale(value: string | null | undefined): Locale {
  const code = (value || '').toLowerCase().split(/[-_]/, 1)[0];
  // "iw" is a legacy browser/OS language identifier for Hebrew.
  return code === 'he' || code === 'iw' ? 'he' : 'en';
}

export function isRtl(locale: Locale): boolean {
  return locale === 'he';
}

export function translate(locale: Locale, english: string): string {
  return locale === 'he' ? (he[english] ?? english) : english;
}
