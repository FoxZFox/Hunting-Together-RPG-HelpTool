// BotLevel99 control panel. Polls the local bot server and renders its state.
const $ = (sel) => document.querySelector(sel);

const SCREEN_NAMES = {
  hud: 'หน้าเกมปกติ',
  book: 'เปิด Adventure Book อยู่',
  bag: 'เปิดกระเป๋าอยู่',
  menu: 'เปิดเมนูอยู่',
  unknown: 'หน้าจอที่บอทไม่รู้จัก',
  offline: 'Roblox หรือ LDPlayer ไม่ตอบ',
};
const TASK_NAMES = { gear: 'กด Auto Gear', hunt: 'กด Hunt', scan: 'สแกนข้อมูลมอน', stats: 'อัพ Stats', cards: 'อ่านการ์ด' };
const STAT_NAMES = ['STR', 'AGI', 'VIT', 'INT', 'DEX', 'LUK'];
const KIND_NAMES = { Normal: 'ธรรมดา', Leader: 'Leader', 'Mini-Boss': 'Mini-Boss', Boss: 'บอส' };

const app = {
  online: false,
  running: false,
  stopping: false,
  task: null,
  status: {},
  logAfter: 0,
  cfg: null,
  monsters: new Map(),   // normalised name -> monster info (with area)
  draft: [],             // plan being edited
  dirty: false,
  statJob: null,         // class whose stat table is shown
  statDraft: [],         // that table being edited
  statDirty: false,
  newJobs: [],           // classes added in the page but not saved yet
  lastRouteKey: '',
  lastScrolledLevel: null,
  mode: 'level',         // what the start button runs: 'level' or 'boss' ([bot] mode)
  bossTargets: [],       // bosses picked in the boss window, in hunting order
  lastBossKey: '',
};

const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
const fmt = (n) => (n == null ? '–' : Number(n).toLocaleString('en-US'));
const lookup = (name) => app.monsters.get(norm(name));

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  return res.json();
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

function duration(seconds) {
  const m = Math.floor(seconds / 60);
  if (m < 1) return 'ไม่ถึงนาที';
  const h = Math.floor(m / 60);
  return h ? `${h} ชม. ${m % 60} นาที` : `${m} นาที`;
}

// ---- config ---------------------------------------------------------------
async function loadConfig() {
  app.cfg = await api('/api/config');
  app.monsters.clear();
  for (const area of app.cfg.areas || []) {
    for (const m of area.monsters) app.monsters.set(norm(m.name), { ...m, area: area.name });
  }
  if (!app.dirty) {
    app.draft = app.cfg.plan.map((s) => ({ ...s }));
    renderPlan();
  }
  if (!app.running) app.mode = app.cfg.mode || 'level';
  app.bossTargets = [...(app.cfg.boss?.targets || [])];
  app.lastBossKey = '';
  renderMonsters();
  fillSettings();
  fillBoss();
  renderBuild();
  applyMode();
  renderBossRoute();
  if (!app.statDirty) loadStatDraft(app.statJob);
  app.lastRouteKey = '';
  renderRoute();
}

// ---- polling --------------------------------------------------------------
async function poll() {
  try {
    const s = await api(`/api/state?after=${app.logAfter}`);
    const finishedTask = app.task && !s.task ? app.task : null;
    app.online = true;
    app.running = s.running;
    app.stopping = s.stopping;
    app.task = s.task;
    app.status = s.status;
    app.serverNow = s.now;
    app.steps = s.steps || [];
    appendLogs(s.logs);
    if (finishedTask === 'scan' || finishedTask === 'cards') await loadConfig();
  } catch {
    app.online = false;
  }
  renderTop();
  renderNow();
  renderRoute();
  renderBossRoute();
  renderStatNow();
  renderBossLive();
  renderSteps();
  updateLiveBits();
  setTimeout(poll, 1500);
}

// ---- top bar --------------------------------------------------------------
// ---- mode: level or boss -----------------------------------------------------
// The mode picks the top window, the "now" facts and the mode's own tab; the other
// tabs (log, steps, stats, monsters, settings) are shared by both modes.
const shownMode = () => (app.running && app.status.mode) || app.mode;

function applyMode() {
  const mode = shownMode();
  document.body.dataset.mode = mode;
  for (const node of document.querySelectorAll('[data-mode-only]')) node.hidden = node.dataset.modeOnly !== mode;
  for (const b of document.querySelectorAll('.mode-btn')) {
    b.setAttribute('aria-checked', b.dataset.mode === mode);
    b.disabled = !app.online || app.running || !!app.task;
  }
  const open = tabs.find((t) => t.getAttribute('aria-selected') === 'true');
  if (open?.hidden) selectTab(tabs.find((t) => t.classList.contains('tab-mode') && !t.hidden) || tabs[0]);
}

for (const b of document.querySelectorAll('.mode-btn')) {
  b.addEventListener('click', async () => {
    if (b.dataset.mode === app.mode || app.running || app.task) return;
    const before = app.mode;
    app.mode = b.dataset.mode;
    applyMode(); renderTop(); renderNow();
    const res = await api('/api/mode', { mode: app.mode }).catch(() => null);
    if (!res?.ok) {
      app.mode = before;
      applyMode(); renderTop(); renderNow();
      $('#run-text').textContent = res?.error || 'เปลี่ยนโหมดไม่สำเร็จ';
    }
  });
}

function renderTop() {
  const chip = $('#run-chip');
  const text = $('#run-text');
  const btn = $('#toggle-btn');
  const boss = shownMode() === 'boss';
  const startText = boss ? 'เริ่มล่าบอส' : 'เริ่มเก็บเลเวล';
  applyMode();
  document.body.classList.toggle('is-running', app.running && !app.stopping);
  btn.className = 'btn btn-big ' + (app.running ? 'btn-halt' : 'btn-go');
  btn.disabled = !app.online || app.stopping || !!app.task;
  if (!app.online) {
    chip.dataset.tone = 'bad';
    text.textContent = 'ติดต่อตัวบอทไม่ได้';
    btn.textContent = startText;
  } else if (app.stopping) {
    chip.dataset.tone = 'busy';
    text.textContent = 'กำลังหยุด รอให้ขั้นตอนนี้จบก่อน';
    btn.textContent = 'กำลังหยุด';
  } else if (app.running) {
    chip.dataset.tone = 'run';
    text.textContent = boss ? 'กำลังล่าบอส' : 'กำลังเก็บเลเวล';
    btn.textContent = 'หยุดบอท';
  } else if (app.task) {
    chip.dataset.tone = 'busy';
    text.textContent = `กำลัง${TASK_NAMES[app.task] || app.task}`;
    btn.textContent = startText;
  } else {
    chip.dataset.tone = app.status.finished ? 'run' : '';
    const done = app.status.finished && app.status.mode === shownMode();
    text.textContent = done ? (boss ? 'ได้การ์ดบอสครบแล้ว' : 'ถึงเลเวลเป้าหมายแล้ว') : 'บอทหยุดอยู่';
    btn.textContent = startText;
  }
}

$('#toggle-btn').addEventListener('click', async () => {
  const btn = $('#toggle-btn');
  if (!app.running && shownMode() === 'boss' && !app.bossTargets.length) {
    $('#run-text').textContent = 'เลือกบอสที่จะล่าก่อน (แผงบอสที่จะล่า)';
    return;
  }
  btn.disabled = true;
  const res = await api(app.running ? '/api/stop' : '/api/start', {}).catch(() => null);
  if (res && !res.ok && res.error) $('#run-text').textContent = res.error;
});

// ---- now ------------------------------------------------------------------
function currentStep(level) {
  const plan = app.cfg?.plan || [];
  return plan.findIndex((p) => level >= p.from && level <= p.to);
}

function nextStep(level) {
  const plan = app.cfg?.plan || [];
  return plan.filter((p) => p.from > level).sort((a, b) => a.from - b.from)[0];
}

function renderNow() {
  const st = app.status || {};
  const level = st.level;
  $('#level').textContent = level ?? '–';
  const started = st.started_at && app.running;
  $('#f-time').textContent = started ? duration(app.serverNow - st.started_at) : '–';
  $('#f-rejoins').textContent = st.restarts
    ? `${st.rejoins || 0} ครั้ง (เปิด LDPlayer ใหม่ ${st.restarts})`
    : `${st.rejoins || 0} ครั้ง`;
  $('#screen-state').textContent = st.screen ? SCREEN_NAMES[st.screen] || st.screen : '';
  if (shownMode() === 'boss') return renderNowBoss(st);

  // while running show what the bot hunted; while stopped show what the plan says
  const target = (app.running && st.target?.length ? st.target : st.plan_target) || [];
  $('#now-title').textContent = 'ตอนนี้';
  $('#now-window-label')?.remove();
  $('.now-target .label').textContent = app.running ? 'กำลังตี' : 'ตามแผนจะตี';
  $('#target-name').textContent = target[1] || (level ? 'ไม่มีแผนสำหรับเลเวลนี้' : '–');
  $('#target-area').textContent = target[0] || '';

  const exp = st.exp == null ? null : Math.max(0, Math.min(1, st.exp));
  $('#exp-fill').style.width = exp == null ? '0' : `${(exp * 100).toFixed(1)}%`;
  $('#exp-text').textContent = exp == null ? 'EXP –' : `EXP ${(exp * 100).toFixed(1)}%`;

  const next = level ? nextStep(level) : null;
  const nextBox = $('#next-step');
  nextBox.replaceChildren();
  if (next) {
    nextBox.append('ถัดไปตอน Lv.', String(next.from), ' ', el('strong', { text: next.monster }));
  } else if (level) {
    nextBox.textContent = 'ช่วงสุดท้ายของแผนแล้ว';
  }

  $('#f-start').textContent = st.start_level ? `Lv.${st.start_level}` : '–';
  $('#f-gained').textContent = st.start_level && level ? `${level - st.start_level} เลเวล` : '–';
  $('#f-hunts').textContent = `${st.hunts || 0} ครั้ง`;
  $('#f-dismantled').textContent = `${st.dismantled || 0} ชิ้น`;
}

function renderNowBoss(st) {
  const running = app.running && st.mode === 'boss';
  const left = app.bossTargets.filter((b) => !(st.boss_done || []).includes(b));
  const current = running && st.target?.length ? st.target[1] : left[0];
  $('.now-target .label').textContent = running ? 'กำลังล่า' : 'จะล่า';
  $('#target-name').textContent = current || 'ยังไม่ได้เลือกบอส';
  $('#target-area').textContent = current ? lookup(current)?.area || '' : 'กดเลือกบอสที่แผง "บอสที่จะล่า" ด้านบน';
  const next = left[left.indexOf(current) + 1];
  const nextBox = $('#next-step');
  nextBox.replaceChildren();
  if (next) nextBox.append('ถัดไป ', el('strong', { text: next }), ' (ตอนได้การ์ดตัวนี้แล้ว)');
  else if (current) nextBox.textContent = 'ตัวสุดท้ายในรายการ';
  const kills = st.kills || 0;
  const ran = running ? app.serverNow - st.started_at : 0;
  $('#f-kills').textContent = st.deaths ? `${kills} ตัว · ตาย ${st.deaths}` : `${kills} ตัว`;
  $('#f-per-kill').textContent = kills && ran ? secs(ran / kills) : '–';
  $('#f-hops').textContent = st.timed_hops ? `${st.hops || 0} ครั้ง · ตามเวลา ${st.timed_hops}` : `${st.hops || 0} ครั้ง`;
  $('#f-cards').textContent = `${(st.cards || []).length} ใบ`;
  const due = st.next_due;
  $('#f-timers').textContent = st.timers
    ? `${st.timers} ห้อง · เร็วสุด ${due <= 0 ? 'ตอนนี้' : `${Math.floor(due / 60)}:${String(due % 60).padStart(2, '0')}`}`
    : '–';
}

$('#gear-btn').addEventListener('click', async () => {
  await api('/api/gear', {}).catch(() => null);
});

// things that depend on whether the bot is busy
function updateLiveBits() {
  const busy = !app.online || app.running || !!app.task;
  $('#gear-btn').disabled = busy;
  $('#scan-btn').disabled = busy;
  $('#stats-run').disabled = busy;
  $('#cards-read').disabled = busy;
  markStatRows();
  $('#busy-hint').hidden = !app.running;
  for (const b of document.querySelectorAll('.hunt-btn')) b.disabled = busy;

  const level = app.status?.level;
  const idx = level ? currentStep(level) : -1;
  document.querySelectorAll('.plan-row').forEach((row, i) => {
    row.classList.toggle('is-now', !app.dirty && i === idx);
  });
  const target = norm((app.status?.target || app.status?.plan_target || [])[1]);
  document.querySelectorAll('#monsters tr').forEach((tr) => {
    tr.classList.toggle('is-target', tr.dataset.name === target);
  });
}

// ---- route map ------------------------------------------------------------
// The tiles are rebuilt only when the plan or the level changes; EXP ticks just
// move the progress bar, so the strip keeps its scroll position.
function renderRoute() {
  const plan = app.cfg?.plan || [];
  const level = app.status?.level;
  const exp = app.status?.exp || 0;
  const key = JSON.stringify([plan, level]);
  const route = $('#route');
  if (key !== app.lastRouteKey) {
    app.lastRouteKey = key;
    buildRoute(route, plan, level);
  }
  const step = plan[level ? currentStep(level) : -1];
  const bar = route.querySelector('.stop-progress');
  if (step && bar) {
    const pct = Math.min(100, ((level - step.from + exp) / (step.to - step.from + 1)) * 100);
    bar.firstChild.style.width = `${pct}%`;
    bar.setAttribute('aria-label', `ผ่านช่วงนี้ไป ${pct.toFixed(0)}%`);
  }
}

function buildRoute(route, plan, level) {
  const keepScroll = route.scrollLeft;
  route.replaceChildren();
  if (!plan.length) {
    route.append(el('p', { class: 'route-empty', text: 'ยังไม่มีแผนการตี เพิ่มได้ที่แท็บแผนการตีด้านล่าง' }));
    $('#route-note').textContent = '';
    return;
  }
  const nowIdx = level ? currentStep(level) : -1;
  let nowTile = null;
  plan.forEach((step, i) => {
    const m = lookup(step.monster);
    const isNow = i === nowIdx;
    const isDone = !isNow && level != null && level > step.to;
    const tile = el('div', {
      class: `stop${isNow ? ' is-now' : ''}${isDone ? ' is-done' : ''}`,
      'data-el': m?.element || null,
    },
    el('p', { class: 'stop-range', text: `Lv.${step.from}–${step.to}` }),
    el('p', { class: 'stop-name', text: step.monster }),
    el('p', { class: 'stop-area', text: m ? m.area : 'ไม่พบในข้อมูลมอน' }));
    if (isDone) {
      tile.append(el('span', { class: 'stop-check', 'aria-label': 'ผ่านแล้ว' }, checkIcon()));
    }
    if (isNow) {
      tile.append(el('div', { class: 'stop-progress', role: 'img' }, el('span')));
      tile.append(el('div', { class: 'pin', 'aria-hidden': 'true' },
        el('span', { class: 'pin-badge', text: `Lv.${level}` }),
        el('span', { class: 'pin-tail' })));
      nowTile = tile;
    }
    route.append(tile);
  });
  $('#route-note').textContent = nowIdx >= 0 ? `ช่วงที่ ${nowIdx + 1} จาก ${plan.length}` : `${plan.length} ช่วง`;
  route.scrollLeft = keepScroll;
  if (nowTile && app.lastScrolledLevel !== level) {
    // jump straight there on first load, glide when the level changes later
    const smooth = app.lastScrolledLevel != null && !matchMedia('(prefers-reduced-motion: reduce)').matches;
    app.lastScrolledLevel = level;
    const left = nowTile.offsetLeft - route.clientWidth / 2 + nowTile.offsetWidth / 2;
    route.scrollTo({ left, behavior: smooth ? 'smooth' : 'auto' });
  }
}

function checkIcon() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('width', '12');
  svg.setAttribute('height', '12');
  svg.innerHTML = '<path d="M3 8.5 6.5 12 13 4.5" fill="none" stroke="#172a55" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>';
  return svg;
}

// ---- log --------------------------------------------------------------------
function logKind(msg) {
  if (/ไม่สำเร็จ|ไม่เจอ|ค้าง|ปัญหา|ไม่รู้จัก|ไม่ได้|ไม่ตรง|ข้อผิดพลาด|เข้าเกมใหม่|ไม่ขยับ/.test(msg)) return 'bad';
  if (/^ไปตี/.test(msg)) return 'hunt';
  if (/^เลเวล/.test(msg)) return 'level';
  if (/^อัพ Stats|^Stats ตอนนี้/.test(msg)) return 'stats';
  if (/^ได้การ์ด|^ฆ่า /.test(msg)) return 'level';
  if (/^ไปห้องที่จำเวลาไว้|รออยู่ในห้อง/.test(msg)) return 'stats';
  if (/ย้ายห้อง|^เข้าห้องที่มีคน/.test(msg)) return 'gear';
  if (/Auto Gear|ย่อยของ/.test(msg)) return 'gear';
  return '';
}

function appendLogs(lines) {
  if (!lines?.length) return;
  const list = $('#log');
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 30;
  list.querySelector('.log-empty')?.remove();
  for (const [id, time, msg] of lines) {
    list.append(el('li', { 'data-kind': logKind(msg) || null }, el('time', { text: time }), el('span', { text: msg })));
    app.logAfter = Math.max(app.logAfter, id);
  }
  while (list.children.length > 400) list.firstChild.remove();
  if (atBottom) list.scrollTop = list.scrollHeight;
}

// ---- plan editor ------------------------------------------------------------
function monsterSelect(value, onChange) {
  const select = el('select', { 'aria-label': 'มอน', onchange: (e) => onChange(e.target.value) });
  for (const area of app.cfg?.areas || []) {
    const group = el('optgroup', { label: area.name });
    for (const m of area.monsters) {
      const kind = m.kind !== 'Normal' ? `, ${KIND_NAMES[m.kind] || m.kind}` : '';
      group.append(el('option', { value: m.name, text: `${m.name} (Lv.${m.level ?? '?'}${kind})` }));
    }
    select.append(group);
  }
  if (value && !lookup(value)) select.prepend(el('option', { value, text: `${value} (ไม่พบในข้อมูล)` }));
  select.value = lookup(value)?.name || value;
  return select;
}

function renderPlan() {
  const list = $('#plan');
  list.replaceChildren();
  app.draft.forEach((step, i) => {
    const row = el('li', { class: 'plan-row', 'data-el': lookup(step.monster)?.element || null });
    const num = (key, label) => el('input', {
      type: 'number', min: 1, max: 200, value: step[key], 'aria-label': label,
      oninput: (e) => { step[key] = e.target.value === '' ? '' : Number(e.target.value); markDirty(); },
    });
    row.append(
      num('from', `แถว ${i + 1} จากเลเวล`),
      num('to', `แถว ${i + 1} ถึงเลเวล`),
      monsterSelect(step.monster, (v) => {
        step.monster = v;
        row.dataset.el = lookup(v)?.element || '';
        markDirty();
      }),
      el('button', {
        class: 'btn btn-ghost btn-small', type: 'button', text: 'ลบ', 'aria-label': `ลบแถว ${i + 1}`,
        onclick: () => { app.draft.splice(i, 1); markDirty(); renderPlan(); },
      }),
    );
    list.append(row);
  });
}

function markDirty() {
  app.dirty = true;
  $('#plan-save').disabled = false;
  $('#plan-reset').disabled = false;
  setMsg('#plan-msg', 'ยังไม่ได้บันทึก');
  updateLiveBits();
}

function setMsg(sel, text, tone) {
  const node = $(sel);
  node.textContent = text;
  if (tone) node.dataset.tone = tone; else delete node.dataset.tone;
}

$('#plan-add').addEventListener('click', () => {
  const last = app.draft[app.draft.length - 1];
  const from = last ? Math.min(200, Number(last.to) + 1) : 1;
  app.draft.push({ from, to: Math.min(200, from + 3), monster: last?.monster || app.cfg?.areas?.[0]?.monsters?.[0]?.name || '' });
  markDirty();
  renderPlan();
  $('#plan').lastElementChild?.querySelector('input')?.focus();
});

$('#plan-reset').addEventListener('click', () => {
  app.draft = app.cfg.plan.map((s) => ({ ...s }));
  app.dirty = false;
  $('#plan-save').disabled = true;
  $('#plan-reset').disabled = true;
  setMsg('#plan-msg', '');
  renderPlan();
  updateLiveBits();
});

$('#plan-save').addEventListener('click', async () => {
  const res = await api('/api/plan', { plan: app.draft }).catch(() => null);
  if (!res?.ok) {
    setMsg('#plan-msg', res?.error || 'บันทึกไม่สำเร็จ ติดต่อตัวบอทไม่ได้', 'bad');
    const m = /แถวที่ (\d+)/.exec(res?.error || '');
    document.querySelectorAll('.plan-row').forEach((r, i) => r.classList.toggle('is-bad', m && i === Number(m[1]) - 1));
    return;
  }
  app.dirty = false;
  $('#plan-save').disabled = true;
  $('#plan-reset').disabled = true;
  await loadConfig();
  setMsg('#plan-msg', app.running ? 'บันทึกแผนแล้ว บอทใช้แผนใหม่ในรอบถัดไป' : 'บันทึกแผนแล้ว', 'ok');
});

// ---- stat table -------------------------------------------------------------
const sameJob = (a, b) => !!a && !!b && a.toLowerCase() === b.toLowerCase();

function statJobs() {
  const names = Object.keys(app.cfg?.stats || {});
  for (const j of [app.status?.job, ...app.newJobs]) {
    if (j && !names.some((n) => sameJob(n, j))) names.push(j);
  }
  return names;
}

function tableFor(job) {
  const tables = app.cfg?.stats || {};
  const key = Object.keys(tables).find((n) => sameJob(n, job));
  return key ? tables[key] : null;
}

function loadStatDraft(job) {
  const jobs = statJobs();
  app.statJob = jobs.find((j) => sameJob(j, job)) || jobs.find((j) => sameJob(j, app.status?.job)) || jobs[0] || null;
  app.statDraft = (tableFor(app.statJob) || []).map((r) => ({ ...r }));
  app.statDirty = false;
  $('#stat-save').disabled = true;
  $('#stat-reset').disabled = true;
  renderJobSelect();
  renderStatTable();
}

function renderJobSelect() {
  const select = $('#job-select');
  select.replaceChildren();
  for (const j of statJobs()) {
    select.append(el('option', { value: j, text: tableFor(j) ? j : `${j} (ยังไม่มีตาราง)` }));
  }
  if (!select.children.length) select.append(el('option', { value: '', text: 'ยังไม่มีอาชีพ' }));
  select.value = app.statJob || '';
}

function renderStatTable() {
  const list = $('#stat-table');
  list.replaceChildren();
  app.statDraft.forEach((row, i) => {
    const li = el('li', { class: 'stat-row' });
    const num = (key, label) => el('input', {
      type: 'number', min: 1, max: 200, value: row[key] ?? '', placeholder: key === 'level' ? 'Lv.' : key,
      'aria-label': label, 'data-stat': key === 'level' ? null : key,
      oninput: (e) => {
        if (e.target.value === '') delete row[key]; else row[key] = Number(e.target.value);
        statMarkDirty();
      },
    });
    li.append(num('level', `แถว ${i + 1} ถึงเลเวล`));
    for (const s of STAT_NAMES) li.append(num(s, `แถว ${i + 1} ${s}`));
    li.append(el('button', {
      class: 'btn btn-ghost btn-small', type: 'button', text: 'ลบ', 'aria-label': `ลบแถว ${i + 1}`,
      onclick: () => { app.statDraft.splice(i, 1); statMarkDirty(); renderStatTable(); },
    }));
    list.append(li);
  });
  if (!app.statDraft.length) {
    list.append(el('li', {
      class: 'log-empty',
      text: app.statJob
        ? `ยังไม่มีตารางของ ${app.statJob} กดเพิ่มแถวเพื่อเริ่ม ถ้าไม่มีตาราง บอทจะไม่ยุ่งกับ Stats ของอาชีพนี้`
        : 'เพิ่มอาชีพก่อน แล้วค่อยเพิ่มแถว',
    }));
  }
  markStatRows();
}

// rows already reached are dimmed; the first one not reached is what the bot works on now
function markStatRows() {
  const st = app.status || {};
  const mine = !app.statDirty && st.stats && sameJob(st.job, app.statJob);
  let found = false;
  document.querySelectorAll('.stat-row').forEach((li, i) => {
    const row = app.statDraft[i] || {};
    const done = mine && STAT_NAMES.every((s) => !row[s] || st.stats[s] >= row[s]);
    const now = mine && !done && !found;
    found ||= now;
    li.classList.toggle('is-done', !!done);
    li.classList.toggle('is-now', !!now);
  });
}

function renderStatNow() {
  const st = app.status || {};
  const box = $('#stat-now');
  const key = JSON.stringify([st.job, st.stats]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren();
  if (!st.job && !st.stats) {
    box.append(el('span', { class: 'stat-now-empty', text: 'บอทยังไม่ได้อ่าน Stats ของตัวละคร จะขึ้นตรงนี้หลังบอทเปิดหน้า Stats ครั้งแรก' }));
  } else {
    box.append(el('span', { class: 'stat-now-label', text: `${st.job || 'อาชีพ ?'} ตอนนี้` }));
    for (const s of STAT_NAMES) {
      box.append(el('span', { class: 'stat-chip' }, el('b', { 'data-stat': s, text: s }), String(st.stats?.[s] ?? '–')));
    }
  }
  // a class the bot has just seen joins the picker
  if (app.statDirty) renderJobSelect(); else loadStatDraft(app.statJob);
}

function statMarkDirty() {
  app.statDirty = true;
  $('#stat-save').disabled = false;
  $('#stat-reset').disabled = false;
  setMsg('#stat-msg', 'ยังไม่ได้บันทึก');
  markStatRows();
}

$('#job-select').addEventListener('change', (e) => {
  if (app.statDirty) {
    e.target.value = app.statJob;
    return setMsg('#stat-msg', 'บันทึกหรือยกเลิกที่แก้ก่อนเปลี่ยนอาชีพ', 'bad');
  }
  setMsg('#stat-msg', '');
  loadStatDraft(e.target.value);
});

$('#job-add').addEventListener('click', () => {
  const input = $('#job-new');
  const name = input.value.trim().replace(/\s+/g, ' ');
  if (!/^[A-Za-z][A-Za-z \-]*$/.test(name)) {
    return setMsg('#stat-msg', 'ใส่ชื่ออาชีพเป็นภาษาอังกฤษตามที่เขียนในกระเป๋า เช่น Cleric', 'bad');
  }
  if (app.statDirty) return setMsg('#stat-msg', 'บันทึกหรือยกเลิกที่แก้ก่อนเพิ่มอาชีพ', 'bad');
  if (!statJobs().some((j) => sameJob(j, name))) app.newJobs.push(name);
  input.value = '';
  setMsg('#stat-msg', '');
  loadStatDraft(name);
});

$('#stat-add').addEventListener('click', () => {
  if (!app.statJob) return setMsg('#stat-msg', 'เพิ่มอาชีพก่อน', 'bad');
  const last = app.statDraft[app.statDraft.length - 1];
  app.statDraft.push(last ? { ...last, level: Math.min(200, Number(last.level || 0) + 10) } : { level: 10 });
  statMarkDirty();
  renderStatTable();
  $('#stat-table').lastElementChild?.querySelector('input')?.focus();
});

$('#stat-reset').addEventListener('click', () => {
  setMsg('#stat-msg', '');
  loadStatDraft(app.statJob);
});

$('#stat-save').addEventListener('click', async () => {
  const job = app.statJob;
  const res = await api('/api/stats', { job, rows: app.statDraft }).catch(() => null);
  if (!res?.ok) {
    setMsg('#stat-msg', res?.error || 'บันทึกไม่สำเร็จ ติดต่อตัวบอทไม่ได้', 'bad');
    const m = /แถวที่ (\d+)/.exec(res?.error || '');
    document.querySelectorAll('.stat-row').forEach((r, i) => r.classList.toggle('is-bad', !!m && i === Number(m[1]) - 1));
    return;
  }
  app.statDirty = false;
  app.newJobs = app.newJobs.filter((j) => !sameJob(j, job));
  await loadConfig();
  setMsg('#stat-msg', app.running ? 'บันทึกแล้ว บอทจะอัพตามตารางใหม่ในรอบถัดไป' : 'บันทึกตารางแล้ว', 'ok');
});

$('#stats-run').addEventListener('click', () => api('/api/stats/run', {}).catch(() => null));

// ---- boss card farm ---------------------------------------------------------
function fillBoss() {
  const cfg = app.cfg?.boss || {};
  const form = $('#boss-form');
  form.elements.appear_s.value = cfg.appear_s ?? 6;
  form.elements.wait_respawn_s.value = cfg.wait_respawn_s ?? 0;
  form.elements.after_kill_s.value = cfg.after_kill_s ?? 2;
  form.elements.zoom_out_clicks.value = cfg.zoom_out_clicks ?? 10;
  form.elements.until_card.checked = cfg.until_card ?? true;
  form.elements.follow_timers.checked = cfg.follow_timers ?? true;
  form.elements.timed_wait_s.value = cfg.timed_wait_s ?? 40;
  form.elements.server_min_players.value = cfg.server_min_players ?? 6;
  form.elements.server_pick.value = cfg.server_pick || 'fewest';
  form.elements.server_cooldown_min.value = cfg.server_cooldown_min ?? 12;
  const skip = cfg.server_skip || [0, 9];         // stored 0-based, shown as ranks from 1
  form.elements.skip_min.value = Math.min(...skip) + 1;
  form.elements.skip_max.value = Math.max(...skip) + 1;
}

function bossValues() {
  const form = $('#boss-form');
  return {
    targets: [...app.bossTargets],
    appear_s: Number(form.elements.appear_s.value),
    wait_respawn_s: Number(form.elements.wait_respawn_s.value),
    after_kill_s: Number(form.elements.after_kill_s.value),
    zoom_out_clicks: Number(form.elements.zoom_out_clicks.value),
    until_card: form.elements.until_card.checked,
    follow_timers: form.elements.follow_timers.checked,
    timed_wait_s: Number(form.elements.timed_wait_s.value),
    server_min_players: Number(form.elements.server_min_players.value),
    server_pick: form.elements.server_pick.value,
    server_cooldown_min: Number(form.elements.server_cooldown_min.value),
    server_skip: [Number(form.elements.skip_min.value) - 1, Number(form.elements.skip_max.value) - 1],
  };
}

async function saveBoss() {
  const res = await api('/api/boss', { boss: bossValues() }).catch(() => null);
  if (!res?.ok) {
    setMsg('#boss-msg', res?.error || 'บันทึกไม่สำเร็จ ติดต่อตัวบอทไม่ได้', 'bad');
    return false;
  }
  await loadConfig();
  return true;
}

function renderBossLive() {
  const cards = app.status?.cards || [];
  const log = $('#card-log');
  if (log.dataset.count === String(cards.length)) return;
  log.dataset.count = cards.length;
  log.replaceChildren(...cards.slice().reverse().map(([time, name, mine]) =>
    el('li', { class: mine ? 'is-boss-card' : null }, el('time', { text: time }),
      el('span', { text: mine ? `${name} (การ์ดบอสที่ล่าอยู่)` : name }))));
  if (!cards.length) log.append(el('li', { class: 'log-empty', text: 'ยังไม่ได้การ์ด' }));
}

// The boss window: every world boss as a tile. Picked ones carry their hunting order;
// clicking a tile picks or unpicks it and saves right away.
function renderBossRoute() {
  const route = $('#boss-route');
  const bosses = app.cfg?.bosses || [];
  const st = app.status || {};
  const current = app.running && st.mode === 'boss' ? st.target?.[1] : null;
  const key = JSON.stringify([bosses.map((b) => b.name), app.bossTargets, st.boss_done, current]);
  if (key === app.lastBossKey) return;
  app.lastBossKey = key;
  route.replaceChildren();
  if (!bosses.length) {
    route.append(el('p', { class: 'route-empty', text: 'ยังไม่มีข้อมูลบอส กดสแกนข้อมูลมอนใหม่ในแท็บมอนทั้งหมดก่อน' }));
    return;
  }
  const done = st.boss_done || [];
  for (const b of bosses) {
    const order = app.bossTargets.indexOf(b.name);
    const isDone = done.includes(b.name);
    const isNow = b.name === current;
    const tile = el('button', {
      type: 'button', 'aria-pressed': order >= 0 ? 'true' : 'false', 'data-el': lookup(b.name)?.element || null,
      class: `stop boss-tile${order >= 0 ? ' is-picked' : ''}${isNow ? ' is-now' : ''}${isDone ? ' is-done' : ''}`,
      onclick: () => toggleBoss(b.name),
    },
    el('p', { class: 'stop-range', text: `Lv.${b.level ?? '?'}` }),
    el('p', { class: 'stop-name', text: b.name }),
    el('p', { class: 'stop-area', text: isDone ? 'ได้การ์ดแล้ว' : order >= 0 ? `ล่าเป็นลำดับที่ ${order + 1}` : 'กดเพื่อเลือก' }));
    if (order >= 0 && !isDone) tile.append(el('span', { class: 'boss-order', text: String(order + 1) }));
    if (isDone) tile.append(el('span', { class: 'stop-check', 'aria-label': 'ได้การ์ดแล้ว' }, checkIcon()));
    if (isNow) {
      tile.append(el('div', { class: 'pin', 'aria-hidden': 'true' },
        el('span', { class: 'pin-badge', text: 'กำลังล่า' }), el('span', { class: 'pin-tail' })));
    }
    route.append(tile);
  }
  $('#boss-route-note').textContent = app.bossTargets.length
    ? `เลือกไว้ ${app.bossTargets.length} ตัว · กดที่บอสเพื่อเลือกหรือเลิกเลือก`
    : 'กดที่บอสเพื่อเลือกตัวที่จะล่า';
}

async function toggleBoss(name) {
  const i = app.bossTargets.indexOf(name);
  if (i >= 0) app.bossTargets.splice(i, 1); else app.bossTargets.push(name);
  renderBossRoute();
  renderNow();
  if (!(await saveBoss())) $('#boss-route-note').textContent = 'บันทึกไม่สำเร็จ ลองใหม่อีกครั้ง';
}

$('#boss-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (await saveBoss()) setMsg('#boss-msg', app.running ? 'บันทึกแล้ว บอทใช้ค่าใหม่ในรอบถัดไป' : 'บันทึกแล้ว', 'ok');
});


// ---- build ------------------------------------------------------------------
// data/builds.json ranks each class's Extra passives and cards. "Full" is the top of each
// list; "now" is the best of what this account owns (cards.json) and has unlocked
// (an Extra of another class needs that class at Lv.99; a class never gets its own).
const CARD_FROM = {
  Boss: () => 'บอสโลก (โหมดล่าบอส)',
  'Mini-Boss': () => 'กล่องทองจากการตีมอน',
  Leader: (area) => `Leader ที่ ${area} (เกิดทุก 5 นาที)`,
  General: (area) => `มอนปกติที่ ${area}`,
};

function buildData() {
  const cfg = app.cfg || {};
  const job = cfg.build?.class || Object.keys(cfg.builds || {})[0];
  const done = cfg.build?.classes_99 || [];
  const cards = new Map();
  for (const a of cfg.cards?.areas || []) for (const c of a.cards) cards.set(c.monster, { ...c, area: a.area });
  const extras = new Map((cfg.extras?.extras || []).map((e) => [e.name, e]));
  const allowed = (e) => !!e && e.class !== job && !e.class_only;
  const unlocked = (e) => allowed(e) && done.includes(e.class);
  return { cfg, job, done, build: cfg.builds?.[job], cards, extras, allowed, unlocked };
}

function renderBuild() {
  const d = buildData();
  const pick = $('#build-class');
  pick.replaceChildren(...(d.cfg.classes || []).map((c) => el('option', { value: c, text: d.cfg.builds?.[c] ? c : `${c} (ยังไม่มี build)` })));
  pick.value = d.job || '';
  $('#class-99').replaceChildren(...(d.cfg.classes || []).map((c) => el('label', { class: 'check' },
    el('input', { type: 'checkbox', value: c, checked: d.done.includes(c) }), c)));
  const read = d.cfg.cards?.owned_read || (d.cfg.cards?.read || '').slice(0, 10);
  $('#cards-read-note').textContent = read ? `การ์ดที่มี: อ่านล่าสุด ${read}` : '';
  const b = d.build;
  $('#build-cols').hidden = !b;
  $('#build-summary').textContent = b ? b.summary : `ยังไม่มี build ของ ${d.job || 'อาชีพนี้'}`;
  if (!b) return;
  const owned = (name) => d.cards.get(name)?.owned || 0;
  const exList = b.extras.filter((x) => d.allowed(d.extras.get(x.name)));

  // full: the top of each list
  const fullStats = b.stats.full;
  $('#build-full').replaceChildren(
    statBlock(fullStats),
    slotList('Extra 6 ช่อง', exList.slice(0, 6).map((x) => extraRow(d, x.name, x.why))),
    slotList('การ์ด 8 ใบ', b.cards.slice(0, 8).map((c) => cardRow(d, c.monster, c.why, c.gain))));

  // now: the best of what is owned / unlocked
  const exNow = exList.filter((x) => d.unlocked(d.extras.get(x.name))).slice(0, 6);
  const nextEx = exList.find((x) => !d.unlocked(d.extras.get(x.name)));
  const exRows = exNow.map((x) => extraRow(d, x.name, x.why));
  while (exRows.length < 6) {
    exRows.push(el('li', { class: 'slot is-empty' }, el('span', { class: 'slot-name', text: 'ว่าง' }),
      el('span', { class: 'slot-why', text: nextEx ? `ยังไม่มี Extra ที่ช่วย ${d.job} เพิ่ม เอา ${d.extras.get(nextEx.name)?.class} ถึง Lv.99 จะได้ ${nextEx.name}` : 'ไม่มี Extra ที่ช่วยเพิ่มแล้ว' })));
  }
  const cardNow = b.cards_now.filter((n) => owned(n)).slice(0, 8);
  const why = (n) => b.cards.find((c) => c.monster === n) || { why: b.card_notes?.[n] };
  const hunt = b.cards.filter((c) => !owned(c.monster)).slice(0, 4);
  const nowStats = owned('Whitefang Sabertooth') && b.stats.whitefang ? b.stats.whitefang : b.stats.now;
  $('#build-now').replaceChildren(
    statBlock(nowStats),
    slotList(`Extra ${exNow.length}/6 ช่อง`, exRows),
    slotList(`การ์ด ${cardNow.length}/8 ใบ`, cardNow.map((n) => cardRow(d, n, why(n).why, why(n).gain))),
    hunt.length ? slotList('การ์ดที่ควรหาต่อ', hunt.map((c) => cardRow(d, c.monster, c.why, c.gain))) : null);

  $('#build-assumes').replaceChildren(...(b.assumes || []).map((t) => el('li', { text: t })));
  $('#build-sets').textContent = b.sets || '';
}

function statBlock(s) {
  return el('div', { class: 'build-stats' },
    el('div', { class: 'stat-now' }, ...['STR', 'AGI', 'VIT', 'INT', 'DEX', 'LUK'].filter((k) => s[k]).map((k) =>
      el('span', { class: 'stat-chip' }, el('b', { 'data-stat': k, text: k }), String(s[k])))),
    el('p', { class: 'field-help', text: s.note || '' }));
}

function slotList(title, rows) {
  return el('div', { class: 'slot-group' }, el('p', { class: 'slot-title', text: title }), el('ol', { class: 'slots' }, ...rows));
}

function extraRow(d, name, why) {
  const e = d.extras.get(name) || {};
  const ok = d.unlocked(e);
  return el('li', { class: `slot${ok ? '' : ' is-missing'}` },
    el('span', { class: 'slot-name', text: name }),
    el('span', { class: 'slot-tag', 'data-ok': ok ? '1' : null, text: ok ? `${e.class} ✓` : `ต้องมี ${e.class} Lv.99` }),
    el('span', { class: 'slot-why', text: why || e.effect || '' }));
}

function cardRow(d, name, why, gain) {
  const c = d.cards.get(name) || {};
  const have = c.owned || 0;
  const stats = [c.stats, c.ability, c.class_boost].filter(Boolean).join(' · ');
  return el('li', { class: `slot${have ? '' : ' is-missing'}` },
    el('span', { class: 'slot-name' }, name, c.type && c.type !== 'General' ? el('span', { class: 'tag', 'data-kind': c.type, text: c.type }) : null),
    el('span', { class: 'slot-tag', 'data-ok': have ? '1' : null, text: have ? `มี ${have} ใบ` : `ยังไม่มี · ${(CARD_FROM[c.type] || CARD_FROM.General)(c.area)}` }),
    el('span', { class: 'slot-why', text: [why, gain ? `ดาเมจ ${gain}` : null].filter(Boolean).join(' · ') }),
    stats ? el('span', { class: 'slot-stats', text: stats }) : null);
}

async function saveBuild(change) {
  const cur = app.cfg?.build || {};
  const build = { class: cur.class, classes_99: [...(cur.classes_99 || [])], ...change };
  const res = await api('/api/build', { build }).catch(() => null);
  if (!res?.ok) return setMsg('#cards-read-note', res?.error || 'บันทึกไม่สำเร็จ', 'bad');
  await loadConfig();
}

$('#build-class').addEventListener('change', (e) => saveBuild({ class: e.target.value }));
$('#class-99').addEventListener('change', () => saveBuild({
  classes_99: [...document.querySelectorAll('#class-99 input:checked')].map((i) => i.value),
}));
$('#cards-read').addEventListener('click', () => api('/api/cards/read', {}).catch(() => null));

// ---- monsters ---------------------------------------------------------------
function renderMonsters() {
  const body = $('#monsters');
  body.replaceChildren();
  for (const area of app.cfg?.areas || []) {
    for (const m of area.monsters) {
      const ratio = m.hp && m.exp ? (m.exp / m.hp).toFixed(2) : '–';
      const tr = el('tr', { 'data-name': norm(m.name), 'data-kind': m.kind, 'data-search': `${m.name} ${area.name}`.toLowerCase() },
        el('td', {}, el('span', { class: 'el-dot', 'data-el': m.element || null, title: m.element || '' }),
          el('span', { class: 'monster-name', text: m.name })),
        el('td', { text: area.name }),
        el('td', { class: 'num', text: m.level ?? '–' }),
        el('td', { class: 'num', text: fmt(m.hp) }),
        el('td', { class: 'num', text: fmt(m.exp) }),
        el('td', { class: 'num', text: ratio }),
        el('td', {}, el('span', { class: 'tag', 'data-kind': m.kind, text: KIND_NAMES[m.kind] || m.kind })),
        el('td', {}, el('button', {
          class: 'btn btn-ghost btn-small hunt-btn', type: 'button', text: 'ไปตี',
          'aria-label': `ไปตี ${m.name}`, onclick: () => api('/api/hunt', { monster: m.name }).catch(() => null),
        })));
      body.append(tr);
    }
  }
  if (!body.children.length) {
    body.append(el('tr', {}, el('td', { colspan: 8, class: 'log-empty', text: 'ยังไม่มีข้อมูลมอน กดสแกนข้อมูลมอนใหม่ เกมต้องเปิดอยู่ที่หน้าเกมปกติ' })));
  }
  filterMonsters();
}

function filterMonsters() {
  const q = $('#monster-search').value.trim().toLowerCase();
  const onlyNormal = $('#only-normal').checked;
  for (const tr of document.querySelectorAll('#monsters tr[data-name]')) {
    const show = (!q || tr.dataset.search.includes(q)) && (!onlyNormal || tr.dataset.kind === 'Normal');
    tr.hidden = !show;
  }
}

$('#monster-search').addEventListener('input', filterMonsters);
$('#only-normal').addEventListener('change', filterMonsters);
$('#scan-btn').addEventListener('click', () => api('/api/scan', {}).catch(() => null));

// ---- settings ---------------------------------------------------------------
function fillSettings() {
  const form = $('#settings');
  const s = app.cfg?.settings || {};
  for (const [k, v] of Object.entries(s)) {
    const input = form.elements[k];
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = !!v; else input.value = v;
  }
}

$('#settings').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const settings = {};
  for (const input of form.elements) {
    if (!input.name) continue;
    settings[input.name] = input.type === 'checkbox' ? input.checked : Number(input.value);
  }
  const res = await api('/api/settings', { settings }).catch(() => null);
  if (!res?.ok) return setMsg('#settings-msg', res?.error || 'บันทึกไม่สำเร็จ ติดต่อตัวบอทไม่ได้', 'bad');
  await loadConfig();
  setMsg('#settings-msg', 'บันทึกการตั้งค่าแล้ว', 'ok');
});

// ---- tabs -------------------------------------------------------------------
const tabs = [...document.querySelectorAll('.tab')];
function selectTab(tab) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute('aria-selected', on);
    t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
  }
  try { localStorage.setItem('botlevel99-tab', tab.id); } catch {}
  if (tab.id === 'tab-steps') setTimeout(renderSteps);   // also runs at load, before renderSteps' helpers exist
}
tabs.forEach((t) => {
  t.addEventListener('click', () => selectTab(t));
  t.addEventListener('keydown', (e) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    const shown = tabs.filter((x) => !x.hidden);      // the other mode's tab is hidden
    const next = shown[(shown.indexOf(t) + step + shown.length) % shown.length];
    selectTab(next);
    next.focus();
  });
});
try {
  const saved = document.getElementById(localStorage.getItem('botlevel99-tab'));
  selectTab(saved && tabs.includes(saved) ? saved : tabs[0]);
} catch { selectTab(tabs[0]); }

// ---- step timeline ----------------------------------------------------------
const secs = (s) => (s < 10 ? s.toFixed(1) : Math.round(s)) + ' วิ';
const clock = (t) => new Date(t * 1000).toLocaleTimeString('en-GB');

function renderSteps() {
  if ($('#panel-steps').hidden) return;
  const steps = app.steps || [];
  const now = app.serverNow || Date.now() / 1000;
  const list = $('#steps');
  list.replaceChildren(...steps.slice().reverse().map((s) => {
    const dur = s.s ?? now - s.t;
    const tone = dur > 20 ? 'bad' : dur > 10 ? 'warn' : null;
    return el('li', { class: s.s == null ? 'is-now' : null, 'data-tone': tone },
      el('time', { text: clock(s.t) }),
      el('span', { class: 'step-name' }, s.label, s.detail ? el('small', { text: ` ${s.detail}` }) : null),
      el('span', { class: 'step-bar' }, el('span', { style: `width:${Math.min(100, (dur / 30) * 100)}%` })),
      el('span', { class: 'step-secs', text: s.s == null ? `${secs(dur)}…` : secs(dur) }));
  }));
  if (!steps.length) list.append(el('li', { class: 'log-empty', text: 'ยังไม่มีขั้นตอน กดเริ่มบอทแล้วจะเห็นตรงนี้' }));

  const groups = new Map();
  for (const s of steps) {
    if (s.s == null) continue;
    const g = groups.get(s.label) || { n: 0, total: 0, max: 0 };
    g.n += 1; g.total += s.s; g.max = Math.max(g.max, s.s);
    groups.set(s.label, g);
  }
  $('#step-sum').replaceChildren(...[...groups].sort((a, b) => b[1].total - a[1].total).map(([label, g]) =>
    el('tr', {}, el('td', { text: label }), el('td', { class: 'num', text: g.n }),
      el('td', { class: 'num', text: secs(g.total / g.n) }), el('td', { class: 'num', text: secs(g.max) }),
      el('td', { class: 'num', text: secs(g.total) }))));
}

// ---- overlay: what the bot looked at, read and tapped -------------------------
// Items come from /api/trace with the server's clock; each screenshot says when it was
// taken, so the boxes drawn on it are the ones from around that moment.
app.trace = [];
app.traceAfter = 0;
app.shotAt = 0;
const SVG = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs = {}, text) => {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
};

async function pollTrace() {
  if (!document.hidden && $('#overlay-on').checked && app.online) {
    try {
      const res = await api(`/api/trace?after=${app.traceAfter}`);
      if (res.trace.length) {
        app.trace.push(...res.trace);
        app.traceAfter = res.trace[res.trace.length - 1].id;
        if (app.trace.length > 400) app.trace.splice(0, app.trace.length - 400);
      }
      app.traceNow = res.now;
    } catch {}
    drawOverlay();
  }
  setTimeout(pollTrace, 700);
}

function drawOverlay() {
  const layer = $('#overlay');
  layer.replaceChildren();
  const stepBox = $('#screen-step');
  if (!$('#overlay-on').checked || !app.shotAt) { stepBox.hidden = true; return; }
  const at = app.shotAt;
  const recent = app.trace.filter((t) => t.t >= at - 1.6 && t.t <= at + 0.4);
  const latest = new Map();         // the same box looked at again: keep the newest only
  for (const t of recent) latest.set(`${t.kind}|${t.label}|${t.rect || t.xy || ''}`, t);
  for (const t of latest.values()) {
    if (t.kind === 'look' || t.kind === 'read') {
      const [x, y, w, h] = t.rect;
      const cls = t.kind === 'read' ? 'ov-read' : t.ok ? 'ov-ok' : 'ov-miss';
      const text = t.kind === 'read' ? `${t.label}: ${t.text || '–'}` : `${t.label} ${t.score}`;
      layer.append(svg('rect', { x, y, width: w, height: h, class: `ov-box ${cls}` }),
        svg('rect', { x, y: Math.max(0, y - 26), width: Math.min(560, text.length * 11 + 14), height: 24, class: `ov-tag ${cls}` }),
        svg('text', { x: x + 7, y: Math.max(0, y - 26) + 18, class: 'ov-text' }, text));
    } else if (t.kind === 'tap') {
      const [x, y] = t.xy;
      layer.append(svg('circle', { cx: x, cy: y, r: 30, class: 'ov-tap' }), svg('circle', { cx: x, cy: y, r: 6, class: 'ov-dot' }));
    } else if (t.kind === 'swipe') {
      layer.append(svg('line', { x1: t.a[0], y1: t.a[1], x2: t.b[0], y2: t.b[1], class: 'ov-swipe' }),
        svg('circle', { cx: t.b[0], cy: t.b[1], r: 10, class: 'ov-dot' }));
    } else if (t.kind === 'key') {
      layer.append(svg('rect', { x: 1110, y: 640, width: 150, height: 56, rx: 10, class: 'ov-key' }),
        svg('text', { x: 1185, y: 678, class: 'ov-key-text' }, `กด ${t.label}`));
    }
  }
  const steps = app.steps || [];
  const cur = steps.length ? steps[steps.length - 1] : null;
  stepBox.hidden = !cur || !(app.running || app.task);
  if (cur) {
    const dur = (cur.s ?? (app.traceNow || app.serverNow) - cur.t) || 0;
    stepBox.textContent = `${cur.label}${cur.detail ? ` · ${cur.detail}` : ''} · ${secs(Math.max(0, dur))}`;
  }
}

$('#overlay-on').addEventListener('change', () => {
  try { localStorage.setItem('botlevel99-overlay', $('#overlay-on').checked ? '1' : '0'); } catch {}
  drawOverlay();
});
$('#screen-wide').addEventListener('click', () => {
  const wide = document.body.classList.toggle('screen-wide');
  $('#screen-wide').setAttribute('aria-pressed', wide);
  $('#screen-wide').textContent = wide ? 'ย่อ' : 'ขยาย';
});
try { $('#overlay-on').checked = localStorage.getItem('botlevel99-overlay') !== '0'; } catch {}

// ---- live screen ------------------------------------------------------------
async function refreshScreen() {
  const busy = app.running || app.task;
  if (document.hidden) return setTimeout(refreshScreen, 2000);
  try {
    const res = await fetch(`/api/screen.jpg?t=${Date.now()}`);
    if (!res.ok) throw new Error(res.status);
    const shotAt = Number(res.headers.get('X-Shot-At')) || 0;
    const url = URL.createObjectURL(await res.blob());
    const img = $('#screen-img');
    img.onload = () => URL.revokeObjectURL(url);
    img.src = url;
    $('.screen').classList.add('has-image');
    app.shotAt = shotAt;
    drawOverlay();
  } catch {
    $('.screen').classList.remove('has-image');
  }
  setTimeout(refreshScreen, busy ? 800 : 2000);
}

// ---- start ------------------------------------------------------------------
$('#log').append(el('li', { class: 'log-empty', text: 'ยังไม่มีบันทึก กดเริ่มบอทแล้วจะเห็นว่าบอททำอะไรอยู่ตรงนี้' }));
loadConfig().catch(() => {}).finally(() => { poll(); refreshScreen(); pollTrace(); });
