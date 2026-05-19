(() => {
  'use strict';

  const TODAY = new Date('2026-05-18T12:00:00-04:00');
  const TZ = 'America/New_York';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const el = (tag, attrs = {}, children = []) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v;
      else if (k === 'on') for (const [ev, fn] of Object.entries(v)) e.addEventListener(ev, fn);
      else if (v != null) e.setAttribute(k, v);
    }
    for (const c of [].concat(children)) {
      if (c == null) continue;
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return e;
  };

  const STORE_KEY = 'nycevents-state-v1';
  const state = loadState();

  function loadState() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) return Object.assign(defaultState(), JSON.parse(raw));
    } catch (e) {}
    return defaultState();
  }
  function defaultState() {
    return {
      borough: 'manhattan',
      selectedHood: null,
      sheetExpanded: false,
      interests: {},
      saved: {},
      tabFilters: {},
      activeMonth: null,
    };
  }
  function saveState() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) {}
  }

  const data = {
    events: null,
    hoods: null,
    schema: null,
  };

  async function loadAll() {
    const [ev, hd, sc] = await Promise.all([
      fetch('data/events.json').then(r => r.json()),
      fetch('data/neighborhoods.json').then(r => r.json()),
      fetch('data/interests-schema.json').then(r => r.json()),
    ]);
    data.events = ev.events;
    data.meta = { generated_at: ev.generated_at };
    data.hoods = hd;
    data.schema = sc;
  }

  // === Categorization & helpers ===
  const CATS = [
    { id: 'outabout',   label: 'Out & About' },
    { id: 'stagesound', label: 'Stage & Sound' },
    { id: 'mindeye',    label: 'Mind & Eye' },
  ];

  function etParts(d) {
    const f = new Intl.DateTimeFormat('en-US', {
      timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false, weekday: 'short'
    });
    const o = {};
    for (const p of f.formatToParts(d)) if (p.type !== 'literal') o[p.type] = p.value;
    return o;
  }
  function etDayKey(d) { const p = etParts(d); return `${p.year}-${p.month}-${p.day}`; }
  function etMonthKey(d) { const p = etParts(d); return `${p.year}-${p.month}`; }
  function fmtDate(iso) {
    const d = new Date(iso);
    const dKey = etDayKey(d);
    const todayKey = etDayKey(TODAY);
    const tomKey = etDayKey(new Date(TODAY.getTime() + 86400000));
    const time = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit' }).format(d);
    if (dKey === todayKey) return `Tonight ${time}`;
    if (dKey === tomKey) return `Tomorrow ${time}`;
    const diffDays = Math.floor((d - TODAY) / 86400000);
    if (diffDays > 1 && diffDays < 7) {
      const wd = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short' }).format(d);
      return `${wd} ${time}`;
    }
    const md = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric' }).format(d);
    return `${md} · ${time}`;
  }
  function fmtRange(startIso, endIso) {
    const opts = { timeZone: TZ, month: 'short', day: 'numeric' };
    const sMon = new Intl.DateTimeFormat('en-US', opts).format(new Date(startIso));
    const eMon = new Intl.DateTimeFormat('en-US', opts).format(new Date(endIso));
    return `${sMon} — ${eMon}`;
  }
  function fmtETDay(d) {
    return new Intl.DateTimeFormat('en-US', { timeZone: TZ, day: 'numeric' }).format(d);
  }
  function fmtETMonth(d, opts) {
    return new Intl.DateTimeFormat('en-US', { timeZone: TZ, ...opts }).format(d);
  }
  function isRunningNow(ev) {
    const s = new Date(ev.start), e = new Date(ev.end);
    return s <= TODAY && TODAY <= e;
  }
  function isThisWeek(ev) {
    const s = new Date(ev.start);
    const weekOut = new Date(TODAY.getTime() + 7 * 86400000);
    return s >= TODAY && s <= weekOut;
  }
  function isUpcoming(ev) {
    const s = new Date(ev.start);
    return s > TODAY;
  }
  function priceClass(p) { return p === 'free' ? 'free' : ''; }
  function priceLabel(p) { return p === 'free' ? 'Free' : p; }

  function eventMatchesInterests(ev) {
    if (!ev.subcategory) return false;
    const v = state.interests[ev.subcategory];
    if (v === 'yes') return true;
    // tag-level
    for (const t of (ev.tags || [])) {
      if (state.interests['g-' + t] === 'yes') return true;
      if (state.interests['m-' + t] === 'yes') return true;
      if (state.interests['t-' + t] === 'yes') return true;
    }
    return false;
  }
  function eventHidden(ev) {
    if (ev.subcategory && state.interests[ev.subcategory] === 'no') return true;
    return false;
  }

  // === Filtering ===
  function visibleEvents() {
    const items = data.events.filter(ev => !eventHidden(ev));
    if (state.selectedHood) return items.filter(e => e.neighborhood === state.selectedHood);
    return items;
  }

  // === Routing ===
  function currentTab() {
    const h = location.hash || '#/now';
    if (h.startsWith('#/upcoming')) return 'upcoming';
    if (h.startsWith('#/saved')) return 'saved';
    if (h.startsWith('#/settings')) return 'settings';
    return 'now';
  }

  function render() {
    const tab = currentTab();
    $$('#bottomtabs a').forEach(a => a.classList.toggle('active', a.dataset.tab === tab));
    const app = $('#app');
    app.innerHTML = '';
    if (tab === 'now') renderNow(app);
    else if (tab === 'upcoming') renderUpcoming(app);
    else if (tab === 'saved') renderSaved(app);
    else if (tab === 'settings') renderSettings(app);
    $('#boroughToggle').textContent = state.borough === 'manhattan' ? 'Manhattan' : 'Brooklyn/Q';
  }

  // === Tab 1: Now ===
  function renderNow(root) {
    const wrap = el('div', { class: 'maparea' });
    wrap.appendChild(renderMap());
    wrap.appendChild(renderSheet());
    root.appendChild(wrap);
  }

  function renderMap() {
    const hd = data.hoods[state.borough];
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'map');
    svg.setAttribute('viewBox', hd.viewBox);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

    const events = data.events.filter(e =>
      e.borough === state.borough &&
      (isThisWeek(e) || isRunningNow(e)) &&
      !eventHidden(e)
    );
    const countsByHood = {};
    for (const ev of events) {
      countsByHood[ev.neighborhood] = (countsByHood[ev.neighborhood] || 0) + 1;
    }

    for (const h of hd.hoods) {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', h.path);
      path.setAttribute('class', 'hood' + (state.selectedHood === h.id ? ' active' : ''));
      path.addEventListener('click', () => {
        state.selectedHood = state.selectedHood === h.id ? null : h.id;
        state.sheetExpanded = true;
        saveState(); render();
      });
      svg.appendChild(path);

      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', h.labelX);
      label.setAttribute('y', h.labelY);
      label.setAttribute('class', 'hood-label' + (state.selectedHood === h.id ? ' active' : ''));
      label.textContent = h.name;
      svg.appendChild(label);

      const cnt = countsByHood[h.id] || 0;
      if (cnt > 0) {
        const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dot.setAttribute('cx', h.labelX + 26);
        dot.setAttribute('cy', h.labelY - 4);
        dot.setAttribute('r', Math.min(2 + cnt * 0.6, 5));
        dot.setAttribute('class', 'hood-dot');
        svg.appendChild(dot);
      }
    }
    return svg;
  }

  function renderSheet() {
    const sheet = el('div', { class: 'sheet' + (state.sheetExpanded ? ' expanded' : '') });
    const handle = el('div', { class: 'sheet-handle' });
    handle.addEventListener('click', () => {
      state.sheetExpanded = !state.sheetExpanded;
      saveState();
      sheet.classList.toggle('expanded', state.sheetExpanded);
    });
    // basic drag-to-expand
    let dragY = null;
    handle.addEventListener('touchstart', (e) => { dragY = e.touches[0].clientY; });
    handle.addEventListener('touchmove', (e) => {
      if (dragY == null) return;
      const dy = e.touches[0].clientY - dragY;
      if (dy < -30) { state.sheetExpanded = true; sheet.classList.add('expanded'); dragY = null; saveState(); }
      else if (dy > 30) { state.sheetExpanded = false; sheet.classList.remove('expanded'); dragY = null; saveState(); }
    });
    sheet.appendChild(handle);

    const hood = state.selectedHood
      ? data.hoods[state.borough].hoods.find(h => h.id === state.selectedHood)
      : null;
    const headerName = hood ? hood.name : 'All ' + (state.borough === 'manhattan' ? 'Manhattan' : 'Brooklyn / Queens');

    const header = el('div', { class: 'sheet-header' }, [
      el('div', { class: 'hood-name' }, headerName),
      hood ? el('button', {
        class: 'clearbtn',
        on: { click: () => { state.selectedHood = null; saveState(); render(); } }
      }, 'Clear ✕') : null,
    ]);
    sheet.appendChild(header);

    const body = el('div', { class: 'sheet-body' });
    const pool = visibleEvents().filter(e =>
      e.borough === state.borough && (isThisWeek(e) || isRunningNow(e))
    );

    for (const c of CATS) {
      const inCat = pool.filter(e => e.category === c.id);
      // sort: interest-matched first, then by start
      inCat.sort((a, b) => {
        const am = eventMatchesInterests(a), bm = eventMatchesInterests(b);
        if (am !== bm) return am ? -1 : 1;
        return new Date(a.start) - new Date(b.start);
      });
      const section = el('div', { class: 'section' });
      section.appendChild(el('div', { class: 'section-head' }, [
        el('h2', {}, c.label),
        el('span', { class: 'count' }, String(inCat.length)),
      ]));
      if (inCat.length === 0) {
        section.appendChild(el('div', { class: 'section-empty' }, 'Nothing this week.'));
      } else {
        const cards = el('div', { class: 'cards' });
        for (const ev of inCat) cards.appendChild(renderCard(ev));
        section.appendChild(cards);
      }
      body.appendChild(section);
    }
    sheet.appendChild(body);
    return sheet;
  }

  function isMultiDay(ev) {
    return etDayKey(new Date(ev.start)) !== etDayKey(new Date(ev.end));
  }

  function fmtWhen(ev) {
    if (!isMultiDay(ev)) return fmtDate(ev.start);
    // multi-day: show as range; if currently running, prefix "Now through"
    const range = fmtRange(ev.start, ev.end);
    return isRunningNow(ev) ? `Now — ${new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric' }).format(new Date(ev.end))}` : range;
  }

  function renderCard(ev) {
    const matched = eventMatchesInterests(ev);
    const isSaved = !!state.saved[ev.id];
    const card = el('div', { class: 'card' + (matched ? ' match' : '') });
    if (matched) card.appendChild(el('span', { class: 'match-badge' }, 'PICK'));
    card.appendChild(el('div', { class: 'title' }, ev.title));
    card.appendChild(el('div', { class: 'when' }, fmtWhen(ev)));
    const venueLine = `${hoodName(ev.neighborhood, ev.borough)} · ${ev.venue}`;
    card.appendChild(el('div', { class: 'meta' }, venueLine));
    const actions = el('div', { class: 'actions' }, [
      el('span', { class: 'price ' + priceClass(ev.price) }, priceLabel(ev.price)),
      el('button', {
        class: 'star' + (isSaved ? ' on' : ''),
        on: { click: (e) => { e.stopPropagation(); toggleSaved(ev.id); render(); } }
      }, isSaved ? '★ Saved' : '☆ Interested'),
    ]);
    card.appendChild(actions);
    card.addEventListener('click', () => openDetail(ev));
    return card;
  }

  function hoodName(id, borough) {
    const h = (data.hoods[borough] || data.hoods.manhattan).hoods.find(h => h.id === id);
    return h ? h.name : id;
  }

  function toggleSaved(id) {
    if (state.saved[id]) delete state.saved[id];
    else state.saved[id] = true;
    saveState();
  }

  // === Tab 2: Upcoming ===
  function renderUpcoming(root) {
    const wrap = el('div', { class: 'timeline-wrap' });

    // Month strip
    const now = new Date(TODAY);
    const months = [];
    for (let i = 0; i < 9; i++) {
      const m = new Date(now.getFullYear(), now.getMonth() + i, 1);
      months.push(m);
    }
    if (!state.activeMonth) state.activeMonth = etMonthKey(months[0]);
    const monthStrip = el('div', { class: 'month-strip' });
    for (const m of months) {
      const key = etMonthKey(m);
      const chip = el('button', {
        class: 'mchip' + (state.activeMonth === key ? ' active' : ''),
        on: { click: () => { state.activeMonth = key; saveState(); render(); } }
      }, fmtETMonth(m, { month: 'short' }) + ' ' + fmtETMonth(m, { year: 'numeric' }));
      monthStrip.appendChild(chip);
    }
    wrap.appendChild(monthStrip);

    // Category strip
    if (!state.tabFilters.upcomingCats) state.tabFilters.upcomingCats = {};
    const catStrip = el('div', { class: 'cat-strip' });
    const allActive = Object.keys(state.tabFilters.upcomingCats).length === 0;
    catStrip.appendChild(el('button', {
      class: 'cchip' + (allActive ? ' active' : ''),
      on: { click: () => { state.tabFilters.upcomingCats = {}; saveState(); render(); } }
    }, 'All'));
    for (const c of CATS) {
      const on = !!state.tabFilters.upcomingCats[c.id];
      catStrip.appendChild(el('button', {
        class: 'cchip' + (on ? ' active' : ''),
        on: { click: () => {
          if (on) delete state.tabFilters.upcomingCats[c.id];
          else state.tabFilters.upcomingCats[c.id] = true;
          saveState(); render();
        }}
      }, c.label));
    }
    wrap.appendChild(catStrip);

    // Timeline body
    const body = el('div', { class: 'timeline-body' });
    const upcoming = data.events
      .filter(e => isUpcoming(e) && !eventHidden(e))
      .filter(e => {
        const cats = state.tabFilters.upcomingCats;
        if (Object.keys(cats).length === 0) return true;
        return !!cats[e.category];
      });

    // group by month (ET)
    const groups = new Map();
    for (const ev of upcoming) {
      const k = etMonthKey(new Date(ev.start));
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(ev);
    }
    const sortedKeys = Array.from(groups.keys()).sort();
    for (const k of sortedKeys) {
      const list = groups.get(k).sort((a, b) => {
        if (a.tentpole !== b.tentpole) return a.tentpole ? -1 : 1;
        const am = eventMatchesInterests(a), bm = eventMatchesInterests(b);
        if (am !== bm) return am ? -1 : 1;
        return new Date(a.start) - new Date(b.start);
      });
      // build month label from the key (year-month) using UTC noon to avoid TZ drift
      const [yy, mm] = k.split('-');
      const monthDate = new Date(Date.UTC(+yy, +mm - 1, 15, 12));
      const monthLabel = fmtETMonth(monthDate, { month: 'long' }) + ' ' + fmtETMonth(monthDate, { year: 'numeric' });
      const group = el('div', { class: 'month-group', id: 'mg-' + k });
      group.appendChild(el('h2', {}, monthLabel));
      for (const ev of list) group.appendChild(renderTLRow(ev));
      body.appendChild(group);
    }
    wrap.appendChild(body);
    root.appendChild(wrap);

    // scroll to active month
    setTimeout(() => {
      const target = $('#mg-' + state.activeMonth);
      if (target) body.scrollTop = target.offsetTop - 4;
    }, 0);
  }

  function renderTLRow(ev) {
    const d = new Date(ev.start);
    const isSaved = !!state.saved[ev.id];
    const matched = eventMatchesInterests(ev);
    const row = el('div', { class: 'tl-row' + (ev.tentpole ? ' tentpole' : '') });
    row.appendChild(el('div', { class: 'tl-date' }, [
      el('div', { class: 'd' }, fmtETDay(d)),
      el('div', { class: 'm' }, fmtETMonth(d, { month: 'short' })),
    ]));
    const main = el('div', { class: 'tl-main' });
    main.appendChild(el('div', { class: 't' }, ev.title));
    main.appendChild(el('div', { class: 's' }, `${hoodName(ev.neighborhood, ev.borough)} · ${ev.venue}`));
    const badges = el('div', { class: 'badges' });
    if (ev.tentpole) badges.appendChild(el('span', { class: 'tag tent' }, 'Tentpole'));
    if (matched) badges.appendChild(el('span', { class: 'tag' }, 'Match'));
    if (isMultiDay(ev)) {
      const endD = fmtETMonth(new Date(ev.end), { month: 'short' }) + ' ' + fmtETDay(new Date(ev.end));
      badges.appendChild(el('span', { class: 'tag' }, '→ ' + endD));
    }
    badges.appendChild(el('span', { class: 'tag' }, priceLabel(ev.price)));
    main.appendChild(badges);
    row.appendChild(main);
    row.appendChild(el('button', {
      class: 'tl-star' + (isSaved ? ' on' : ''),
      on: { click: (e) => { e.stopPropagation(); toggleSaved(ev.id); render(); } }
    }, isSaved ? '★' : '☆'));
    row.addEventListener('click', () => openDetail(ev));
    return row;
  }

  // === Tab 3: Saved ===
  function renderSaved(root) {
    const ids = Object.keys(state.saved);
    if (ids.length === 0) {
      root.appendChild(el('div', { class: 'saved-empty' }, [
        el('div', { class: 'emoji' }, '⭐'),
        el('div', {}, 'Nothing saved yet.'),
        el('div', { class: 's', html: 'Tap <b>☆ Interested</b> on any event to save it here.' }),
      ]));
      return;
    }
    const items = data.events.filter(e => state.saved[e.id]).sort((a, b) => new Date(a.start) - new Date(b.start));
    const body = el('div', { class: 'timeline-body' });
    for (const ev of items) body.appendChild(renderTLRow(ev));
    root.appendChild(body);
  }

  // === Settings ===
  function renderSettings(root) {
    const wrap = el('div', { class: 'settings' });

    wrap.appendChild(el('h2', {}, 'About'));
    const meta = data.meta || {};
    const lastSync = meta.generated_at || '—';
    let lastSyncLabel = lastSync;
    try {
      lastSyncLabel = new Intl.DateTimeFormat('en-US', {
        timeZone: TZ, dateStyle: 'medium', timeStyle: 'short'
      }).format(new Date(lastSync));
    } catch (e) {}
    wrap.appendChild(el('div', { class: 'about-line' },
      `NYC Events — daily curated picks. ${data.events.length} events tracked.`));
    wrap.appendChild(el('div', { class: 'about-line muted' },
      `Last sync: ${lastSyncLabel} (ET).`));

    // PWA install hint — only relevant outside standalone
    if (!matchMedia('(display-mode: standalone)').matches) {
      wrap.appendChild(el('h2', {}, 'Install to Home Screen'));
      wrap.appendChild(el('div', { class: 'install-hint', html:
        'For a full-screen, app-like experience:<br><br>' +
        '<b>1.</b> Tap the <b>Share</b> button at the bottom of Safari (square with up-arrow)<br>' +
        '<b>2.</b> Scroll down and tap <b>Add to Home Screen</b><br>' +
        '<b>3.</b> Tap <b>Add</b> in the top-right<br><br>' +
        'The icon will appear on your Home Screen and open the app full-screen with no browser bars.'
      }));
    }

    wrap.appendChild(el('h2', {}, 'Interests'));
    wrap.appendChild(el('div', { class: 'about-line muted', html:
      'Tap 👍 for things you want to see more of, 👎 to hide. The <b>PICK</b> badge surfaces your matches.'
    }));

    for (const sec of data.schema.sections) {
      wrap.appendChild(el('h2', {}, sec.name));
      for (const item of sec.items) {
        const cur = state.interests[item.id];
        const row = el('div', { class: 'toggle-row' });
        row.appendChild(el('div', { class: 'label' }, item.label));
        const tri = el('div', { class: 'tri' });
        const mk = (val, glyph) => {
          const onCls = cur === val ? (val === 'yes' ? ' on-yes' : ' on-no') : '';
          return el('button', {
            class: onCls,
            on: { click: () => {
              state.interests[item.id] = cur === val ? null : val;
              if (!state.interests[item.id]) delete state.interests[item.id];
              saveState(); render();
            }}
          }, glyph);
        };
        tri.appendChild(mk('yes', '👍'));
        tri.appendChild(mk('no', '👎'));
        row.appendChild(tri);
        wrap.appendChild(row);
      }
    }

    root.appendChild(wrap);
  }

  // === Detail modal ===
  function openDetail(ev) {
    const backdrop = el('div', { class: 'modal-backdrop' });
    const modal = el('div', { class: 'modal' });
    modal.appendChild(el('button', { class: 'close', on: { click: () => backdrop.remove() } }, '✕'));
    modal.appendChild(el('h2', {}, ev.title));
    const ds = new Date(ev.start), de = new Date(ev.end);
    const sameDay = ds.toDateString() === de.toDateString();
    const dateLine = sameDay ? fmtDate(ev.start) : fmtRange(ev.start, ev.end);
    modal.appendChild(el('div', { class: 'meta' }, [
      dateLine + ' · ' + hoodName(ev.neighborhood, ev.borough) + ' · ' + ev.venue + ' · ' + priceLabel(ev.price)
    ]));
    modal.appendChild(el('div', { class: 'desc' }, ev.description || ''));
    const acts = el('div', { class: 'modal-actions' });
    const isSaved = !!state.saved[ev.id];
    acts.appendChild(el('button', {
      class: 'primary',
      on: { click: () => { toggleSaved(ev.id); backdrop.remove(); render(); } }
    }, isSaved ? '★ Saved' : '☆ Interested'));
    acts.appendChild(el('button', {
      on: { click: () => backdrop.remove() }
    }, 'Close'));
    modal.appendChild(acts);
    if (ev.sources && ev.sources.length) {
      modal.appendChild(el('div', { class: 'src-list' }, [
        el('div', { class: 'meta' }, 'Sources'),
        ...ev.sources.map(s => el('a', { href: s.url, target: '_blank' }, s.name + ' ↗'))
      ]));
    }
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) backdrop.remove(); });
    document.body.appendChild(backdrop);
  }

  // === Wire up nav ===
  function init() {
    $('#boroughToggle').addEventListener('click', () => {
      state.borough = state.borough === 'manhattan' ? 'outer' : 'manhattan';
      state.selectedHood = null;
      saveState(); render();
    });
    $('#settingsBtn').addEventListener('click', () => { location.hash = '#/settings'; });
    window.addEventListener('hashchange', render);
  }

  // === Boot ===
  loadAll().then(() => { init(); render(); }).catch(err => {
    $('#app').innerHTML = `<div style="padding:24px;color:#c75a5a">Failed to load data: ${err}</div>`;
  });
})();
