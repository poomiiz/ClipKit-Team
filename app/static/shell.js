// ClipKit app shell: draws the sidebar on every page and marks where you are.
(() => {
  if (window !== window.top) return;   // embedded inside the editor: no second sidebar
  const I = {
    home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
    clip: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9l5 3-5 3z"/>',
    cover: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    motion: '<path d="M4 7h10M4 12h16M4 17h7"/><path d="M18 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1z"/>',
    drafts: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z"/>',
  };
  window.CLIPKIT_ICONS = I;
  const svg = k => `<svg viewBox="0 0 24 24">${I[k]}</svg>`;
  const NAV = [
    // starting or continuing a clip lives on the home tiles; the sidebar only moves between pages
    ['', [['home', 'หน้าแรก', '/video-editor.html']]],
  ];
  const here = location.pathname + location.hash;
  const isOn = href => href === here || (href === location.pathname && !location.hash && !href.includes('#'));
  const aside = document.createElement('nav');
  aside.className = 'sb';
  aside.innerHTML = `<a class="sb-brand" href="/video-editor.html"><span class="sb-logo">${svg('clip').replace('<svg', '<svg stroke="#fff" fill="none" stroke-width="2"')}</span>
      <span class="sb-name">ClipKit<small>Video → CapCut</small></span></a>` +
    NAV.map(([g, items]) => (g ? `<div class="sb-group">${g}</div>` : '<div style="height:8px"></div>') + items.map(([k, label, href]) =>
      `<a class="sb-item${isOn(href) ? ' on' : ''}" href="${href}">${svg(k)}<span>${label}</span></a>`).join('')).join('') +
    '<div class="sb-group">ไฟล์ดิบ</div><div class="sb-projects" id="sbProjects"><div class="sb-empty">กำลังอ่าน…</div></div>' +
    `<a class="sb-item" href="#" id="sbBug"><svg viewBox="0 0 24 24"><path d="M8 8a4 4 0 018 0v6a4 4 0 01-8 0z"/><path d="M4 12h4M16 12h4M5 6l3 2M19 6l-3 2M5 19l3-2M19 19l-3-2"/></svg><span>แจ้งปัญหา</span></a>` +
    `<a class="sb-item${isOn('/settings.html') ? ' on' : ''}" href="/settings.html">${svg('settings')}<span>ตั้งค่า</span></a>` +
    '<div class="sb-foot" id="sbVer">ClipKit</div>';
  document.body.prepend(aside);
  // raw files on the left, the projects cut from each one underneath (click a raw file = pick its stories again)
  window.ckLoadProjects = async () => {
    const box = document.getElementById('sbProjects');
    let open = {};
    try { open = JSON.parse(localStorage.getItem('ck_sb_open') || '{}'); } catch {}
    try {
      const r = await fetch('/api/kit/raw-groups');
      if (!r.ok) throw new Error(r.status);
      const {groups} = await r.json();
      box.innerHTML = groups.length ? '' : '<div class="sb-empty">ยังไม่มีงาน · ลากไฟล์ดิบมาวางที่หน้าแรก</div>';
      groups.forEach(g => {
        const wrap = document.createElement('div');
        wrap.className = 'sb-raw' + (open[g.raw] ? ' open' : '');
        wrap.innerHTML = `<div class="sb-raw-h"><button class="tw" title="กาง/หุบ">▸</button><a class="rn"></a><span class="cnt">${g.projects.length}</span></div><div class="sb-raw-b"></div>`;
        const rn = wrap.querySelector('.rn');
        rn.textContent = g.name;
        rn.title = g.raw ? (g.exists ? g.raw : g.raw + ' (ไม่พบไฟล์แล้ว)') : 'โปรเจกต์ที่ไม่รู้ว่าตัดจากไฟล์ไหน';
        if (!g.exists) rn.classList.add('gone');
        rn.href = g.exists ? '/video-editor.html?raw=' + encodeURIComponent(g.raw) : '#';
        rn.onclick = e => {
          if (!g.exists) { e.preventDefault(); wrap.querySelector('.tw').click(); return; }
          if (window.ckOpenRaw) { e.preventDefault(); window.ckOpenRaw(g.raw); }
        };
        wrap.querySelector('.tw').onclick = () => {
          wrap.classList.toggle('open');
          open[g.raw] = wrap.classList.contains('open');
          try { localStorage.setItem('ck_sb_open', JSON.stringify(open)); } catch {}
        };
        const body = wrap.querySelector('.sb-raw-b');
        g.projects.forEach(d => {
          const a = document.createElement('a');
          a.className = 'sb-proj';
          a.href = '/video-editor.html?open=' + encodeURIComponent(d.path);
          a.title = d.name;
          a.innerHTML = `<span class="k ${d.kind}">${d.kind === 'capcut' ? 'CC' : 'HF'}</span><span class="n"></span>`;
          a.querySelector('.n').textContent = d.name;
          a.onclick = e => { if (window.ckOpenProject) { e.preventDefault(); window.ckOpenProject(d.path); } };
          body.appendChild(a);
        });
        box.appendChild(wrap);
      });
    } catch (e) { box.innerHTML = '<div class="sb-empty">❌ อ่านรายการงานไม่ได้</div>'; }
  };
  window.ckLoadProjects();
  // light refresh: coming back to the window re-reads the project list (work done in CapCut / Studio / chat)
  window.addEventListener('focus', () => window.ckLoadProjects());
  // a new version installed while this page was open: one small bar to reload, nothing reloads by itself
  let seenCommit = null;
  setInterval(() => fetch('/api/kit/version').then(r => r.json()).then(v => {
    if (seenCommit === null) { seenCommit = v.commit; return; }
    if (v.commit !== seenCommit && !document.getElementById('ckReload')) {
      const bar = document.createElement('div');
      bar.id = 'ckReload';
      bar.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:200;background:#1d2236;border:1px solid #7c5cff;' +
        'border-radius:10px;padding:8px 12px;color:#f1f4fb;font-size:13.5px;display:flex;gap:10px;align-items:center';
      bar.innerHTML = 'หน้านี้มีเวอร์ชันใหม่ <button style="background:#7c5cff;color:#fff;border:0;border-radius:7px;padding:4px 12px;cursor:pointer;font:inherit">รีเฟรช</button>';
      bar.querySelector('button').onclick = () => location.reload();
      document.body.appendChild(bar);
    }
  }).catch(() => {}), 30000);
  // bug report: keeps the last errors seen on the page so the report says what actually broke
  const errs = [];
  window.addEventListener('error', e => errs.push(`${e.message} @ ${(e.filename || '').split('/').pop()}:${e.lineno}`));
  window.addEventListener('unhandledrejection', e => errs.push('promise: ' + ((e.reason && e.reason.message) || e.reason)));
  const ce = console.error;
  console.error = (...a) => { errs.push(a.map(String).join(' ').slice(0, 300)); ce.apply(console, a); };
  new MutationObserver(() => {   // red ❌ messages the pages show to people
    document.querySelectorAll('#toast, .msg, .busy, #result').forEach(el => {
      const t = (el.textContent || '').trim();
      if (t.startsWith('❌') && errs[errs.length - 1] !== t) errs.push(t.slice(0, 300));
    });
  }).observe(document.body, {subtree: true, childList: true, characterData: true});
  document.getElementById('sbBug').onclick = e => {
    e.preventDefault();
    if (document.getElementById('ckBug')) return;
    const m = document.createElement('div');
    m.id = 'ckBug';
    m.style.cssText = 'position:fixed;inset:0;z-index:300;background:rgba(0,0,0,.55);display:grid;place-items:center';
    m.innerHTML = `<div style="background:#141824;border:1px solid #262d40;border-radius:14px;padding:20px;width:min(520px,92vw);color:#f1f4fb">
      <b style="font-size:17px">แจ้งปัญหา</b>
      <div style="color:#828ca4;font-size:13px;margin:4px 0 10px">เล่าว่ากดอะไร แล้วเกิดอะไรขึ้น · ระบบแนบหน้าที่เปิดอยู่ เวอร์ชัน และ error ล่าสุด ${errs.length} รายการให้เอง</div>
      <textarea rows="5" style="width:100%;background:#1b2030;color:#f1f4fb;border:1px solid #262d40;border-radius:10px;padding:10px;font:inherit" placeholder="เช่น กดส่งออก MP4 แล้วขึ้น error…"></textarea>
      <div class="r" style="color:#828ca4;font-size:13px;min-height:18px;margin-top:6px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px">
        <button class="x" style="background:#1b2030;color:#c5cde0;border:1px solid #262d40;border-radius:9px;padding:8px 14px;cursor:pointer;font:inherit">ปิด</button>
        <button class="s" style="background:#7c5cff;color:#fff;border:0;border-radius:9px;padding:8px 14px;cursor:pointer;font:inherit;font-weight:600">ส่ง</button></div></div>`;
    document.body.appendChild(m);
    const ta = m.querySelector('textarea'), res = m.querySelector('.r');
    ta.focus();
    m.querySelector('.x').onclick = () => m.remove();
    m.querySelector('.s').onclick = async () => {
      try {
        const r = await fetch('/api/kit/bug-report', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({text: ta.value, page: location.pathname + location.search, errors: errs})});
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || r.status);
        res.innerHTML = '✅ บันทึกในเครื่องแล้ว · <a target="_blank" style="color:#9fb0ff">ส่งเข้า GitHub ของทีม</a> (กดแล้วกด Submit)';
        res.querySelector('a').href = j.issue_url;
        window.open(j.issue_url, '_blank');
      } catch (err) { res.textContent = '❌ ' + err.message; }
    };
  };
  document.body.classList.add('shell');
  // repaint the active item when video-editor switches screens through the hash
  window.addEventListener('hashchange', () => {
    const now = location.pathname + location.hash;
    aside.querySelectorAll('.sb-item').forEach(a => a.classList.toggle('on',
      a.getAttribute('href') === now || (!location.hash && a.getAttribute('href') === location.pathname)));
  });
  // new version on GitHub: one banner at the top when the app opens, one click to update
  if (!sessionStorage.getItem('ck_upd_checked')) {
    sessionStorage.setItem('ck_upd_checked', '1');
    fetch('/api/kit/update-check').then(r => r.json()).then(u => {
      if (!u.available) return;
      const bar = document.createElement('div');
      bar.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:200;background:#1d2236;' +
        'border:1px solid #7c5cff;border-radius:12px;padding:10px 14px;display:flex;gap:12px;align-items:center;' +
        'color:#f1f4fb;font-size:14px;box-shadow:0 8px 30px rgba(0,0,0,.5);max-width:92vw';
      bar.innerHTML = '<span>มี ClipKit เวอร์ชันใหม่ (' + u.count + ' รายการ)</span>' +
        '<button style="background:#7c5cff;color:#fff;border:0;border-radius:8px;padding:6px 14px;font:inherit;font-weight:600;cursor:pointer">อัปเดต</button>' +
        '<button style="background:none;border:0;color:#828ca4;cursor:pointer;font:inherit">ภายหลัง</button>';
      bar.title = (u.changes || []).join('\n');
      const [go, later] = bar.querySelectorAll('button');
      later.onclick = () => bar.remove();
      go.onclick = async () => {
        go.disabled = true; go.textContent = 'กำลังอัปเดต…';
        try {
          const r = await fetch('/api/kit/update', {method: 'POST'});
          if (!r.ok) throw new Error((await r.json()).detail);
          let j;
          do { await new Promise(x => setTimeout(x, 1500)); j = await fetch('/api/kit/job/update').then(x => x.json()); }
          while (j.status === 'running');
          if (j.status !== 'done') throw new Error((j.log || '').trim().split('\n').pop());
          bar.firstChild.textContent = '✅ อัปเดตแล้ว · ปิดแล้วเปิด ClipKit ใหม่ให้ครบทุกส่วน';
          go.remove(); later.textContent = 'ปิด';
        } catch (e) { bar.firstChild.textContent = '❌ อัปเดตไม่สำเร็จ: ' + e.message; go.disabled = false; go.textContent = 'ลองอีกครั้ง'; }
      };
      document.body.appendChild(bar);
    }).catch(() => {});
  }
  fetch('/api/kit/version').then(r => r.ok ? r.json() : null).then(v => {
    if (v) document.getElementById('sbVer').innerHTML = `เวอร์ชัน <b>${v.version || ''}</b>${v.commit ? ' · ' + v.commit : ''}`;
  }).catch(() => {});
})();
