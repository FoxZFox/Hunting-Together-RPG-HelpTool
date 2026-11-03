// RoomSpoof — in-page panel for Hunting Together RPG.
// Lists public servers ranked by ping / player count, joins the best one, and
// remembers every server joined through the panel so it is skipped until its
// cooldown runs out.
(() => {
  'use strict';

  const PLACE_ID = '117483759937632';
  if (!/\/games\/117483759937632(\/|$)/.test(location.pathname)) return;
  if (window.__roomspoof) return;
  window.__roomspoof = true;

  const API = `https://games.roblox.com/v1/games/${PLACE_ID}/servers/0`;
  const LIST_LIMIT = 60; // rows rendered in the panel
  const CACHE_TTL = 45_000; // Auto Join reuses a list fetched this recently
  const PLAYER_WEIGHT_MS = 20; // balanced mode: one extra player "costs" 20 ms of ping
  const RETRY_WAITS = [5_000, 15_000, 30_000]; // backoff after HTTP 429 (limit is ~3 req/min)

  const DEFAULTS = {
    sortMode: 'balanced',
    maxPing: 0,
    maxPlayers: 0,
    pages: 1,
    cooldownMin: 15,
    showCooling: false,
  };
  const LIMITS = {
    maxPing: [0, 2000],
    maxPlayers: [0, 50],
    pages: [1, 5],
    cooldownMin: [0, 180],
  };

  let settings = { ...DEFAULTS };
  let cooldowns = {}; // serverId -> time it was joined (ms)
  let servers = [];
  let fetchedAt = 0;
  let busy = false;
  let view = { minimized: false, hidden: false, left: null, top: null };
  let status = { text: '', tone: '', until: 0 };

  // ---------------------------------------------------------------- storage

  const store = {
    get: (keys) => chrome.storage.local.get(keys),
    set: (obj) => chrome.storage.local.set(obj).catch(() => {}),
  };

  const saveSettings = () => store.set({ settings });
  const saveCooldowns = () => store.set({ cooldowns });
  const saveView = () => store.set({ view });

  // --------------------------------------------------------------- cooldown

  function cooldownLeft(id) {
    const joinedAt = cooldowns[id];
    if (!joinedAt) return 0;
    return Math.max(0, joinedAt + settings.cooldownMin * 60_000 - Date.now());
  }

  function pruneCooldowns() {
    let changed = false;
    for (const id of Object.keys(cooldowns)) {
      if (!cooldownLeft(id)) {
        delete cooldowns[id];
        changed = true;
      }
    }
    if (changed) saveCooldowns();
  }

  // ---------------------------------------------------------------- servers

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function waitWithCountdown(ms) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const sec = Math.ceil((end - Date.now()) / 1000);
      setStatus(`Roblox จำกัดความถี่ (429) — ลองใหม่ใน ${sec} วิ`, 'warn', Infinity);
      await sleep(Math.min(1000, end - Date.now()));
    }
  }

  async function fetchPage(cursor) {
    const url = new URL(API);
    url.searchParams.set('sortOrder', 'Asc'); // fewest players first
    url.searchParams.set('excludeFullGames', 'true');
    url.searchParams.set('limit', '100');
    if (cursor) url.searchParams.set('cursor', cursor);

    for (let attempt = 0; ; attempt++) {
      const res = await fetch(url, { credentials: 'include' });
      if (res.ok) return res.json();
      if (res.status === 429 && attempt < RETRY_WAITS.length) {
        await waitWithCountdown(RETRY_WAITS[attempt]);
        continue;
      }
      throw new Error(`HTTP ${res.status}`);
    }
  }

  async function loadServers() {
    const byId = new Map();
    let cursor = null;
    for (let page = 1; page <= settings.pages; page++) {
      setStatus(`กำลังดึงรายชื่อห้อง... (หน้า ${page}/${settings.pages})`, '', Infinity);
      let data;
      try {
        data = await fetchPage(cursor);
      } catch (err) {
        if (byId.size) break; // keep the pages we already have
        throw err;
      }
      for (const s of data.data || []) {
        byId.set(s.id, {
          id: s.id,
          playing: s.playing ?? 0,
          max: s.maxPlayers,
          ping: typeof s.ping === 'number' ? s.ping : null,
          fps: typeof s.fps === 'number' ? s.fps : null,
        });
      }
      cursor = data.nextPageCursor;
      if (!cursor) break;
    }
    servers = [...byId.values()];
    fetchedAt = Date.now();
  }

  // Open servers that pass the filters, best first. Cooldown is not applied here.
  function rankServers(list) {
    const pingOf = (s) => (s.ping == null ? Infinity : s.ping);
    const passes = (s) =>
      s.playing < s.max &&
      (!settings.maxPing || (s.ping != null && s.ping <= settings.maxPing)) &&
      (!settings.maxPlayers || s.playing <= settings.maxPlayers);

    const byPing = (a, b) => pingOf(a) - pingOf(b) || a.playing - b.playing;
    const byPlayers = (a, b) => a.playing - b.playing || pingOf(a) - pingOf(b);
    const score = (s) => pingOf(s) + s.playing * PLAYER_WEIGHT_MS;
    const byScore = (a, b) => score(a) - score(b) || byPing(a, b);

    const cmp = { ping: byPing, players: byPlayers }[settings.sortMode] || byScore;
    return list.filter(passes).sort(cmp);
  }

  // ------------------------------------------------------------------ joins

  async function join(server) {
    cooldowns[server.id] = Date.now();
    saveCooldowns();
    document.dispatchEvent(
      new CustomEvent('roomspoof:join', {
        detail: JSON.stringify({ placeId: PLACE_ID, gameId: server.id }),
      })
    );
    const ping = server.ping == null ? '—' : `${server.ping}ms`;
    setStatus(
      `กำลังเข้าห้อง ${server.id.slice(0, 8)} · ${server.playing}/${server.max} คน · ping ${ping}`,
      'ok',
      8000
    );
  }

  async function refresh() {
    if (busy) return;
    setBusy(true);
    try {
      await loadServers();
      setStatus(`โหลดแล้ว ${servers.length} ห้อง`, 'ok');
    } catch (err) {
      setStatus(`โหลดรายชื่อห้องไม่สำเร็จ (${err.message})`, 'err', 10_000);
    } finally {
      setBusy(false);
    }
  }

  async function autoJoin() {
    if (busy) return;
    setBusy(true);
    try {
      if (!servers.length || Date.now() - fetchedAt > CACHE_TTL) await loadServers();
      const best = rankServers(servers).find((s) => !cooldownLeft(s.id));
      if (best) await join(best);
      else setStatus('ไม่มีห้องที่ผ่านเงื่อนไข — ลองผ่อนตัวกรองหรือรอคูลดาวน์', 'warn', 8000);
    } catch (err) {
      setStatus(`Auto Join ไม่สำเร็จ (${err.message})`, 'err', 10_000);
    } finally {
      setBusy(false);
    }
  }

  // --------------------------------------------------------------------- UI

  const CSS = `
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    button { font: inherit; color: inherit; cursor: pointer; }
    button:disabled { cursor: default; }

    .wrap {
      --bg: #0e1115;
      --surface: #161a20;
      --surface-2: #1d2229;
      --line: #272e37;
      --text: #e8ecf1;
      --muted: #8a94a2;
      --accent: #34d17f;
      --accent-ink: #052414;
      --warn: #f2c14e;
      --bad: #ff6b6b;
      font: 13px/1.45 "Builder Sans", "Noto Sans Thai", "Segoe UI", system-ui, sans-serif;
      color: var(--text);
    }

    .panel {
      display: flex; flex-direction: column;
      width: 340px; max-height: calc(100vh - 40px);
      background: var(--bg);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: 0 18px 48px rgba(0, 0, 0, .5), 0 2px 6px rgba(0, 0, 0, .3);
      overflow: hidden;
    }
    .panel > * { flex-shrink: 0; }

    .head {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      padding: 10px 10px 10px 14px;
      background: linear-gradient(180deg, #171c23, var(--bg));
      border-bottom: 1px solid var(--line);
      cursor: grab; user-select: none; touch-action: none;
    }
    .head:active { cursor: grabbing; }
    .brand { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .brand b { display: block; font-size: 14px; letter-spacing: .2px; }
    .brand small { display: block; color: var(--muted); font-size: 11px; }
    .dot {
      width: 9px; height: 9px; border-radius: 50%; flex: none;
      background: var(--accent); box-shadow: 0 0 0 3px rgba(52, 209, 127, .18);
    }
    .icons { display: flex; gap: 4px; }
    .icon {
      width: 28px; height: 28px; border-radius: 8px; border: 0;
      background: transparent; color: var(--muted); font-size: 15px; line-height: 1;
    }
    .icon:hover, .icon[aria-pressed="true"] { background: var(--surface-2); color: var(--text); }

    .settings {
      display: grid; grid-template-columns: 1fr 110px; gap: 8px 10px; align-items: center;
      padding: 12px 14px; background: var(--surface); border-bottom: 1px solid var(--line);
      flex-shrink: 1; min-height: 60px; overflow-y: auto;
    }
    .settings label { color: var(--muted); font-size: 12px; }
    .settings label small { display: block; font-size: 10.5px; opacity: .8; }
    .settings input, .settings select {
      width: 100%; height: 30px; padding: 0 8px; border-radius: 8px;
      border: 1px solid var(--line); background: var(--bg); color: var(--text); font: inherit;
    }
    .settings input:focus, .settings select:focus { outline: 2px solid rgba(52, 209, 127, .45); outline-offset: 0; }

    .actions { display: flex; gap: 8px; padding: 12px 14px 8px; }
    .primary {
      flex: 1; height: 40px; border: 0; border-radius: 10px;
      background: var(--accent); color: var(--accent-ink); font-weight: 700; font-size: 14px;
      box-shadow: 0 6px 18px rgba(52, 209, 127, .25);
      transition: transform .08s ease, filter .15s ease;
    }
    .primary:hover:not(:disabled) { filter: brightness(1.08); }
    .primary:active:not(:disabled) { transform: translateY(1px); }
    .primary:disabled { opacity: .55; box-shadow: none; }
    .ghost {
      width: 40px; height: 40px; border-radius: 10px;
      border: 1px solid var(--line); background: var(--surface); font-size: 17px;
    }
    .ghost:hover:not(:disabled) { background: var(--surface-2); }
    .ghost:disabled { opacity: .55; }
    .spin { display: inline-block; animation: spin .9s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }

    .status {
      margin: 0 14px 10px; min-height: 18px; font-size: 12px; color: var(--muted);
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .status[data-tone="ok"] { color: var(--accent); }
    .status[data-tone="warn"] { color: var(--warn); }
    .status[data-tone="err"] { color: var(--bad); }

    .cols, .row {
      display: grid; grid-template-columns: 22px 1fr 52px 34px 78px; align-items: center; gap: 6px;
    }
    .cols {
      padding: 6px 14px; font-size: 11px; color: var(--muted);
      border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); background: var(--surface);
    }
    .cols span:nth-child(3), .cols span:nth-child(4) { text-align: right; }

    .list {
      list-style: none; margin: 0; padding: 4px 0;
      flex: 1 1 auto; min-height: 72px; max-height: 300px; overflow-y: auto;
    }
    .list::-webkit-scrollbar { width: 8px; }
    .list::-webkit-scrollbar-thumb { background: var(--line); border-radius: 8px; }
    .row { padding: 5px 14px; border-left: 3px solid transparent; }
    .row:hover { background: var(--surface); }
    .row.best { background: rgba(52, 209, 127, .08); border-left-color: var(--accent); }
    .row.cooling { opacity: .5; }
    .rank { color: var(--muted); font-size: 11px; text-align: center; }
    .best .rank { color: var(--accent); }
    .players { display: flex; align-items: center; gap: 8px; font-variant-numeric: tabular-nums; }
    .players small { color: var(--muted); }
    .bar { flex: 1; max-width: 70px; height: 5px; border-radius: 3px; background: var(--surface-2); overflow: hidden; }
    .bar i { display: block; height: 100%; background: #5b8def; border-radius: 3px; }
    .ping, .fps { text-align: right; font-variant-numeric: tabular-nums; }
    .fps { color: var(--muted); font-size: 12px; }
    .good { color: var(--accent); }
    .mid { color: var(--warn); }
    .bad { color: var(--bad); }
    .none { color: var(--muted); }
    .join {
      height: 26px; border-radius: 7px; border: 1px solid var(--line);
      background: var(--surface-2); font-size: 12px; font-variant-numeric: tabular-nums;
    }
    .join:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
    .join:disabled { color: var(--muted); background: transparent; }
    .empty { padding: 18px 14px; text-align: center; color: var(--muted); font-size: 12px; }

    .foot {
      display: flex; align-items: center; gap: 10px; padding: 9px 14px;
      border-top: 1px solid var(--line); background: var(--surface); font-size: 11.5px; color: var(--muted);
    }
    .foot label { display: flex; align-items: center; gap: 5px; margin-left: auto; cursor: pointer; }
    .foot input { accent-color: var(--accent); margin: 0; }
    .link { border: 0; background: none; padding: 0; color: var(--muted); text-decoration: underline; font-size: 11.5px; }
    .link:hover { color: var(--text); }

    .pill {
      display: flex; align-items: center; gap: 8px; height: 38px; padding: 0 16px 0 13px;
      border-radius: 999px; border: 1px solid var(--line); background: var(--bg); color: var(--text);
      font-weight: 600; box-shadow: 0 10px 28px rgba(0, 0, 0, .45);
    }
    .pill:hover { background: var(--surface); }
  `;

  const HTML = `
    <div class="wrap">
      <div class="panel">
        <header class="head">
          <div class="brand">
            <span class="dot"></span>
            <div><b>RoomSpoof</b><small>Hunting Together RPG</small></div>
          </div>
          <div class="icons">
            <button class="icon" data-act="settings" title="ตั้งค่า" aria-pressed="false">⚙</button>
            <button class="icon" data-act="minimize" title="ย่อ">–</button>
          </div>
        </header>

        <section class="settings" hidden>
          <label for="rs-sort">เรียงห้องตาม</label>
          <select id="rs-sort" data-set="sortMode">
            <option value="balanced">สมดุล</option>
            <option value="ping">Ping ต่ำสุดก่อน</option>
            <option value="players">คนน้อยสุดก่อน</option>
          </select>
          <label for="rs-ping">Ping สูงสุด (ms)<small>0 = ไม่จำกัด</small></label>
          <input id="rs-ping" type="number" data-set="maxPing" step="10">
          <label for="rs-players">จำนวนคนสูงสุด<small>0 = ไม่จำกัด</small></label>
          <input id="rs-players" type="number" data-set="maxPlayers">
          <label for="rs-pages">จำนวนหน้าที่สแกน<small>หน้าละ 100 ห้อง (Roblox ให้ ~3 ครั้ง/นาที)</small></label>
          <input id="rs-pages" type="number" data-set="pages">
          <label for="rs-cd">คูลดาวน์ต่อห้อง (นาที)<small>0 = ปิด</small></label>
          <input id="rs-cd" type="number" data-set="cooldownMin">
        </section>

        <div class="actions">
          <button class="primary" data-act="auto">⚡ Auto Join ห้องที่ดีที่สุด</button>
          <button class="ghost" data-act="refresh" title="รีเฟรชรายชื่อห้อง"><span>↻</span></button>
        </div>
        <div class="status"></div>

        <div class="cols">
          <span>#</span><span>ผู้เล่น</span>
          <span title="ค่า ping เฉลี่ยของผู้เล่นในห้องนั้น (จาก Roblox)">Ping</span>
          <span>FPS</span><span></span>
        </div>
        <ol class="list"></ol>

        <footer class="foot">
          <span class="cd-count"></span>
          <button class="link" data-act="clear">ล้าง</button>
          <label><input type="checkbox" data-set="showCooling">แสดงห้องติดคูลดาวน์</label>
        </footer>
      </div>
      <button class="pill" hidden><span class="dot"></span>RoomSpoof</button>
    </div>
  `;

  const host = document.createElement('div');
  host.id = 'roomspoof-root';
  host.style.cssText = 'all: initial; position: fixed; right: 20px; bottom: 20px; z-index: 2147483000;';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>${CSS}</style>${HTML}`;

  const $ = (sel) => root.querySelector(sel);
  const els = {
    panel: $('.panel'),
    head: $('.head'),
    settings: $('.settings'),
    settingsBtn: $('[data-act="settings"]'),
    auto: $('[data-act="auto"]'),
    refresh: $('[data-act="refresh"]'),
    status: $('.status'),
    list: $('.list'),
    cdCount: $('.cd-count'),
    pill: $('.pill'),
  };

  function el(tag, attrs = {}, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    node.append(...kids);
    return node;
  }

  const mmss = (ms) => {
    const s = Math.ceil(ms / 1000);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };

  const pingTone = (p) => (p == null ? 'none' : p < 100 ? 'good' : p < 200 ? 'mid' : 'bad');

  function ago(t) {
    const s = Math.round((Date.now() - t) / 1000);
    return s < 60 ? `${s} วิที่แล้ว` : `${Math.floor(s / 60)} นาทีที่แล้ว`;
  }

  function setStatus(text, tone = '', hold = 5000) {
    status = { text, tone, until: Date.now() + hold };
    renderStatus();
  }

  function renderStatus() {
    let { text, tone } = status;
    if (!busy && Date.now() > status.until) {
      tone = '';
      text = fetchedAt
        ? `อัปเดต ${ago(fetchedAt)} · ${servers.length} ห้อง`
        : 'กด ↻ เพื่อโหลดรายชื่อห้อง';
    }
    els.status.textContent = text;
    els.status.dataset.tone = tone;
    els.status.title = text;
  }

  function setBusy(on) {
    busy = on;
    els.auto.disabled = on;
    els.refresh.disabled = on;
    els.refresh.firstElementChild.classList.toggle('spin', on);
    renderList();
    renderStatus();
  }

  function row(s, rank, isBest) {
    const left = cooldownLeft(s.id);
    const li = el('li', { class: `row${isBest ? ' best' : ''}${left ? ' cooling' : ''}`, title: s.id });
    const fill = Math.min(100, Math.round((s.playing / s.max) * 100));
    const players = el(
      'span',
      { class: 'players' },
      el('span', { class: 'bar' }, el('i', { style: `width:${fill}%` })),
      el('span', {}, el('b', {}, String(s.playing)), el('small', {}, `/${s.max}`))
    );
    const btn = left
      ? el('button', { class: 'join', 'data-cd': s.id, disabled: '' }, `⏳ ${mmss(left)}`)
      : el('button', { class: 'join', 'data-join': s.id }, 'เข้า');
    li.append(
      el('span', { class: 'rank' }, isBest ? '★' : String(rank)),
      players,
      el('span', { class: `ping ${pingTone(s.ping)}` }, s.ping == null ? '—' : String(s.ping)),
      el('span', { class: 'fps' }, s.fps == null ? '—' : String(Math.round(s.fps))),
      btn
    );
    return li;
  }

  function renderList() {
    pruneCooldowns();
    const ranked = rankServers(servers);
    const ready = ranked.filter((s) => !cooldownLeft(s.id));
    const rows = settings.showCooling ? ranked : ready;
    const bestId = ready[0] && ready[0].id;

    if (rows.length) {
      els.list.replaceChildren(
        ...rows.slice(0, LIST_LIMIT).map((s, i) => row(s, i + 1, s.id === bestId))
      );
    } else {
      const msg = busy && !fetchedAt
        ? 'กำลังโหลด...'
        : !fetchedAt
          ? 'ยังไม่ได้โหลดรายชื่อห้อง'
          : !servers.length
            ? 'ไม่พบห้องสาธารณะ'
            : 'ไม่มีห้องที่ผ่านตัวกรอง/คูลดาวน์';
      els.list.replaceChildren(el('li', { class: 'empty' }, msg));
    }

    const n = Object.keys(cooldowns).length;
    els.cdCount.textContent = settings.cooldownMin
      ? `คูลดาวน์ ${n} ห้อง (${settings.cooldownMin} นาที)`
      : 'ปิดคูลดาวน์';
  }

  function fillForm() {
    for (const input of root.querySelectorAll('[data-set]')) {
      const v = settings[input.dataset.set];
      if (input.type === 'checkbox') input.checked = !!v;
      else input.value = v;
    }
  }

  function applyView() {
    host.style.display = view.hidden ? 'none' : 'block';
    els.panel.hidden = view.minimized;
    els.pill.hidden = !view.minimized;
    if (view.left != null) requestAnimationFrame(() => place(view.left, view.top));
  }

  function place(left, top) {
    const r = host.getBoundingClientRect();
    left = Math.max(0, Math.min(left, innerWidth - r.width));
    top = Math.max(0, Math.min(top, innerHeight - r.height));
    view.left = left;
    view.top = top;
    Object.assign(host.style, { left: `${left}px`, top: `${top}px`, right: 'auto', bottom: 'auto' });
  }

  function makeDraggable(handle) {
    handle.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || e.target.closest('button')) return;
      const r = host.getBoundingClientRect();
      const dx = e.clientX - r.left;
      const dy = e.clientY - r.top;
      handle.setPointerCapture(e.pointerId);
      const move = (ev) => place(ev.clientX - dx, ev.clientY - dy);
      const up = () => {
        handle.removeEventListener('pointermove', move);
        saveView();
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('lostpointercapture', up, { once: true });
    });
  }

  function bindEvents() {
    els.auto.addEventListener('click', autoJoin);
    els.refresh.addEventListener('click', refresh);

    els.settingsBtn.addEventListener('click', () => {
      els.settings.hidden = !els.settings.hidden;
      els.settingsBtn.setAttribute('aria-pressed', String(!els.settings.hidden));
      if (view.left != null) place(view.left, view.top);
    });

    $('[data-act="minimize"]').addEventListener('click', () => {
      view.minimized = true;
      applyView();
      saveView();
    });
    els.pill.addEventListener('click', () => {
      view.minimized = false;
      applyView();
      saveView();
    });

    $('[data-act="clear"]').addEventListener('click', () => {
      cooldowns = {};
      saveCooldowns();
      renderList();
      setStatus('ล้างคูลดาวน์ทั้งหมดแล้ว', 'ok');
    });

    els.list.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-join]');
      if (!btn || busy) return;
      const server = servers.find((s) => s.id === btn.dataset.join);
      if (server) join(server).then(renderList);
    });

    for (const input of root.querySelectorAll('[data-set]')) {
      input.addEventListener('change', () => {
        const key = input.dataset.set;
        let v;
        if (input.type === 'checkbox') v = input.checked;
        else if (input.type === 'number') {
          const [lo, hi] = LIMITS[key];
          v = Math.round(Number(input.value));
          v = Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : DEFAULTS[key];
          input.value = v;
        } else v = input.value;
        settings[key] = v;
        saveSettings();
        renderList();
      });
    }

    makeDraggable(els.head);
    addEventListener('resize', () => {
      if (view.left != null) place(view.left, view.top);
    });

    // Keep several open tabs of the game page in sync.
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      if (changes.cooldowns) cooldowns = changes.cooldowns.newValue || {};
      if (changes.settings) {
        settings = { ...DEFAULTS, ...changes.settings.newValue };
        fillForm();
      }
      if (changes.cooldowns || changes.settings) renderList();
    });

    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.type === 'roomspoof:toggle') {
        view.hidden = !view.hidden;
        applyView();
        saveView();
      }
    });
  }

  function tick() {
    let expired = false;
    for (const btn of els.list.querySelectorAll('[data-cd]')) {
      const left = cooldownLeft(btn.dataset.cd);
      if (left) btn.textContent = `⏳ ${mmss(left)}`;
      else expired = true;
    }
    if (expired) renderList();
    renderStatus();
  }

  async function init() {
    const saved = await store.get(['settings', 'cooldowns', 'view']);
    settings = { ...DEFAULTS, ...saved.settings };
    cooldowns = saved.cooldowns || {};
    view = { ...view, ...saved.view };

    document.documentElement.appendChild(host);
    fillForm();
    bindEvents();
    applyView();
    renderList();
    renderStatus();
    setInterval(tick, 1000);
    refresh();
  }

  init();
})();
