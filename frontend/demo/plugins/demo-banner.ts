/**
 * What the demo adds to `index.html`: the demo notice, and two small fixes a
 * sub-path host needs. Injected at build time, so no component changes.
 *
 * **The notice.** A strip above the app saying what this is — sample data, no
 * live database, no model — with a link to the source. It can be dismissed and
 * comes back on reload, on purpose: nobody should leave believing they queried
 * a real database. It is drawn in the app's own tokens (`--sidebar-bg`,
 * `--amber-*`, `--accent`, `--on-accent`), which `applyTheme` sets on `:root`,
 * so it follows the theme toggle; the fallbacks cover the moment before React
 * has applied them. The shell's two `100vh` boxes are shortened by the strip's
 * height so nothing in the app sits underneath it.
 *
 * **Root-absolute images.** `Logo` asks for `/brand.png` and the Creators page
 * for `/team/<name>.png` as plain strings, which a build cannot rewrite. On a
 * host that serves the app from `/DataMind/`, those are requests to another
 * site. The head script prefixes the base onto an `<img>` source that names a
 * file in `public/` — and nothing else. Stylesheet `url()`s and `<link>`s are
 * rewritten by Vite itself.
 */
import fs from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

export interface BannerOptions {
  frontendRoot: string
  sourceUrl: string
  /** Shown in the notice: the day the data runs to. */
  asOf: string
}

function escape(text: string): string {
  return text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
}

const STYLE = `
#dm-demo {
  --dm-surface: var(--sidebar-bg, oklch(0.14 0.01 250));
  --dm-tint: var(--amber-bg, oklch(0.8 0.15 80 / 0.1));
  --dm-edge: var(--amber-border, oklch(0.8 0.15 80 / 0.35));
  --dm-text: var(--text, oklch(0.93 0.01 250));
  --dm-strong: var(--text-strong, oklch(0.95 0.01 250));
  --dm-dim: var(--text-dim, oklch(0.65 0.015 250));
  --dm-accent: var(--accent, oklch(0.7 0.15 250));
  --dm-on-accent: var(--on-accent, oklch(0.14 0.01 250));
  position: relative;
  z-index: 60;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 36px;
  padding: 6px 12px 6px 16px;
  box-sizing: border-box;
  background: linear-gradient(var(--dm-tint), var(--dm-tint)), var(--dm-surface);
  border-bottom: 1px solid var(--dm-edge);
  color: var(--dm-text);
  font: 500 12.5px/1.45 Inter, system-ui, sans-serif;
}
:root[data-theme='light'] #dm-demo {
  --dm-surface: var(--sidebar-bg, oklch(0.96 0.011 80));
  --dm-tint: var(--amber-bg, oklch(0.62 0.16 66 / 0.13));
  --dm-edge: var(--amber-border, oklch(0.62 0.16 66 / 0.35));
  --dm-text: var(--text, oklch(0.24 0.013 70));
  --dm-strong: var(--text-strong, oklch(0.15 0.014 68));
  --dm-dim: var(--text-dim, oklch(0.5 0.013 74));
  --dm-accent: var(--accent, oklch(0.52 0.19 315));
  --dm-on-accent: var(--on-accent, oklch(0.99 0.004 320));
}
#dm-demo[hidden] { display: none; }
#dm-demo .dm-pill {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--dm-accent);
  color: var(--dm-on-accent);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
#dm-demo .dm-text { flex: 1; min-width: 0; }
#dm-demo .dm-text strong { color: var(--dm-strong); font-weight: 650; }
#dm-demo .dm-more { color: var(--dm-dim); }
#dm-demo a {
  flex-shrink: 0;
  color: var(--dm-accent);
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 2px;
  border-radius: 4px;
}
#dm-demo a:focus-visible, #dm-demo button:focus-visible {
  outline: 2px solid var(--dm-accent);
  outline-offset: 2px;
}
#dm-demo button {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--dm-dim);
  font: inherit;
  font-size: 17px;
  line-height: 1;
  cursor: pointer;
}
#dm-demo button:hover { color: var(--dm-strong); background: var(--dm-tint); }
@media (max-width: 640px) {
  #dm-demo { gap: 8px; padding-left: 12px; }
  #dm-demo .dm-more { display: none; }
}
/* The shell sizes itself to the viewport inline; it has to share it now. */
.rm-app, .rm-app-row { height: calc(100vh - var(--dm-demo-h, 0px)) !important; }
.rm-auth { min-height: calc(100vh - var(--dm-demo-h, 0px)) !important; }
@media print { #dm-demo { display: none !important; } }
`

function headScript(base: string, publicEntries: string[]): string {
  // Built from the files actually in public/, so a new asset folder is covered
  // without anyone remembering this list exists.
  const pattern = publicEntries.map((e) => e.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
  return `
(function () {
  try {
    var theme = localStorage.getItem('raymand.theme');
    if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme);
  } catch (e) {}
  var base = ${JSON.stringify(base)};
  if (base === '/' || !${JSON.stringify(pattern)}) return;
  var local = new RegExp(${JSON.stringify(`^/(?:${pattern})(?:[/?#]|$)`)});
  var fix = function (value) {
    return typeof value === 'string' && local.test(value) ? base + value.slice(1) : value;
  };
  var described = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
  if (described && described.set) {
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
      configurable: true, enumerable: described.enumerable, get: described.get,
      set: function (value) { described.set.call(this, fix(value)); }
    });
  }
  var setAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    return setAttribute.call(this, name, this instanceof HTMLImageElement && name === 'src' ? fix(value) : value);
  };
})();`
}

const BODY_SCRIPT = `
(function () {
  var banner = document.getElementById('dm-demo');
  if (!banner) return;
  var root = document.documentElement;
  var measure = function () {
    root.style.setProperty('--dm-demo-h', banner.hidden ? '0px' : banner.offsetHeight + 'px');
  };
  measure();
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(measure).observe(banner);
  banner.querySelector('button').addEventListener('click', function () {
    banner.hidden = true;
    measure();
  });
})();`

export function demoBanner({ frontendRoot, sourceUrl, asOf }: BannerOptions): Plugin {
  let base = '/'
  const publicDir = path.join(frontendRoot, 'public')
  const publicEntries = fs.existsSync(publicDir) ? fs.readdirSync(publicDir) : []
  const day = new Date(`${asOf}T00:00:00Z`).toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
  })

  return {
    name: 'datamind-demo-banner',
    configResolved(config) {
      base = config.base
    },
    transformIndexHtml(html) {
      return {
        html: html.replace(/<title>([^<]*)<\/title>/, '<title>$1 · Demo</title>'),
        tags: [
          { tag: 'script', children: headScript(base, publicEntries), injectTo: 'head-prepend' },
          { tag: 'style', children: STYLE, injectTo: 'head' },
          {
            tag: 'aside',
            attrs: { id: 'dm-demo', 'aria-label': 'About this demo' },
            children: [
              { tag: 'span', attrs: { class: 'dm-pill' }, children: 'Demo' },
              {
                tag: 'span',
                attrs: { class: 'dm-text' },
                children:
                  `<strong>Sample data, no live database.</strong> `
                  + `<span class="dm-more">Every answer is a recorded run of the real pipeline over data as of ${escape(day)}; nothing you type reaches a database or a model.</span>`,
              },
              {
                tag: 'a',
                attrs: { href: sourceUrl, target: '_blank', rel: 'noopener noreferrer' },
                children: 'View source',
              },
              {
                tag: 'button',
                attrs: { type: 'button', 'aria-label': 'Hide this notice until the page is reloaded', title: 'Hide until reload' },
                children: '×',
              },
            ],
            injectTo: 'body-prepend',
          },
          { tag: 'script', children: BODY_SCRIPT, injectTo: 'body' },
        ],
      }
    },
  }
}
