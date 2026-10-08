/**
 * The small pieces the report editor and the report viewer both draw: the back
 * button, the toolbar buttons, the shared labels and the inline note.
 */
import type { ReportLanguage } from '../api/types'
import { Icon } from './ui'

/**
 * The document's own furniture, in the language the document is written in.
 *
 * A report whose paragraphs are Persian and whose figure captions say "Figure 3"
 * is not a Persian report — it is an English product with Persian text pasted
 * into it, and that is exactly the seam a reader notices first. The prose is
 * pinned per report (`reports.language`); so is everything the page writes
 * around it.
 *
 * Deliberately small. Only what appears *inside the document* is here — the app
 * chrome above it (Back, Stop, the status chip) stays in the interface language,
 * because it is the application talking, not the report.
 */
const LABELS: Record<ReportLanguage, Record<string, string>> = {
  en: {
    eyebrow: 'Analytical report',
    dataSource: 'Data source',
    generated: 'Generated',
    model: 'Model',
    keyFigures: 'Key figures',
    figure: 'Figure',
    // A single number is a callout, not an exhibit, so it carries no figure
    // number — and the appendix, which lists every query, needs something to
    // head its entry with instead.
    headline: 'Headline figure',
    method: 'Method and data notes',
    methodBody:
      'Every figure in this document was produced by one query, run read-only '
      + 'against the data source above and validated before execution. The '
      + 'queries are listed here with the row counts they returned.',
    question: 'Question',
    rows: 'rows',
    row: 'row',
    capped: 'result capped',
    computed: 'computed',
    query: 'Query',
    hideQuery: 'Hide query',
    print: 'Print',
    edited: 'edited by you',
    retry: 'Retry',
    changeChart: 'Change chart',
    done: 'Done',
    edit: 'Edit',
    save: 'Save',
    saving: 'Saving…',
    cancel: 'Cancel',
    revert: 'Revert',
    queryChanged: 'query changed since the previous generation',
    queryChangedNote:
      'The statement behind this figure is not the one the previous '
      + 'generation ran, so the two numbers are not a like-for-like '
      + 'comparison.',
    asOf: 'This document reflects the data as it stood on',
    asOfTail:
      'Generating again writes a new run and leaves this one exactly as it is.',
  },
  fa: {
    eyebrow: 'گزارش تحلیلی',
    dataSource: 'منبع داده',
    generated: 'تاریخ تولید',
    model: 'مدل',
    keyFigures: 'شاخص‌های کلیدی',
    figure: 'شکل',
    headline: 'شاخص کلیدی',
    method: 'روش و ملاحظات داده',
    methodBody:
      'هر شکل این سند نتیجهٔ یک کوئری است که فقط‌خواندنی روی منبع دادهٔ بالا '
      + 'اجرا و پیش از اجرا اعتبارسنجی شده است. کوئری‌ها همراه با تعداد سطرهای '
      + 'بازگشتی در این بخش فهرست شده‌اند.',
    question: 'پرسش',
    rows: 'سطر',
    row: 'سطر',
    capped: 'نتیجه محدود شده',
    computed: 'محاسبه در',
    query: 'کوئری',
    hideQuery: 'بستن کوئری',
    print: 'چاپ',
    edited: 'ویرایش‌شده توسط شما',
    retry: 'اجرای دوباره',
    changeChart: 'تغییر نمودار',
    done: 'پایان',
    edit: 'ویرایش',
    save: 'ذخیره',
    saving: 'در حال ذخیره…',
    cancel: 'انصراف',
    revert: 'بازگردانی',
    queryChanged: 'کوئری نسبت به تولید قبلی تغییر کرده است',
    queryChangedNote:
      'عبارتی که این شکل از آن به دست آمده، همان عبارت تولید قبلی نیست؛ '
      + 'بنابراین این دو عدد قابل مقایسهٔ مستقیم نیستند.',
    asOf: 'این سند وضعیت داده‌ها را در تاریخ زیر نشان می‌دهد:',
    asOfTail: 'تولید دوبارهٔ گزارش، اجرای تازه‌ای می‌سازد و این اجرا دست‌نخورده می‌ماند.',
  },
}

/** The document's own labels, falling back to English for an unknown code. */
export function labelsFor(language: string): Record<string, string> {
  return LABELS[language as ReportLanguage] ?? LABELS.en
}

export function Note({ tone, children }: { tone: 'green' | 'amber' | 'red'; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 9,
        padding: '10px 12px',
        fontSize: 12.5,
        lineHeight: 1.55,
        color: 'var(--text2)',
        background: `var(--${tone}-bg)`,
        border: `1px solid var(--${tone}-border)`,
        borderRadius: 10,
      }}
    >
      <span aria-hidden style={{ display: 'flex', paddingTop: 1, color: `var(--${tone})`, flexShrink: 0 }}>
        {tone === 'green' ? <Icon.Check size={13} /> : <Icon.Alert size={13} />}
      </span>
      <span>{children}</span>
    </div>
  )
}

/** The header's ghost buttons, one notch tighter than the default. */
export const toolbarBtn: React.CSSProperties = { fontSize: 12.5, padding: '7px 12px' }

/** `.rm-dash-header` carries the padding and the tone; the layout is inline,
 *  the same way the dashboard's own header declares it. */
export const headerStyle: React.CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  gap: 10,
  borderBottom: '1px solid var(--border)',
  flexShrink: 0,
}

/** The back arrow, identical in both halves of the feature. */
export const backButton: React.CSSProperties = {
  display: 'flex',
  width: 30,
  height: 30,
  flexShrink: 0,
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: 8,
  border: 'none',
  background: 'transparent',
  color: 'var(--text-dim)',
  cursor: 'pointer',
  ['--rm-hover-bg' as string]: 'var(--panel-alt)',
}
