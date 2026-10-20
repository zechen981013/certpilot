/* ============================================================
   壳层：授权闸门 + 内容拉取 + 学习记录上报
   原有 App 逻辑整体包进 __runApp()，由 boot() 在数据就绪后调用。
   ============================================================ */
const API_BASE = '/api';

const T = {
  get(){ try{ return localStorage.getItem('sp_token'); }catch(e){ return null; } },
  set(v){ try{ localStorage.setItem('sp_token', v); }catch(e){} },
  clear(){ try{ localStorage.removeItem('sp_token'); }catch(e){} }
};

/* 设备标识：一次性随机串，不是指纹——不采集任何硬件/浏览器特征。
   作用只是让「同时 2 台」这条限制能落地。 */
function deviceId(){
  try{
    let d = localStorage.getItem('sp_dev');
    if(!d){
      d = (crypto.randomUUID ? crypto.randomUUID()
           : String(Date.now())+Math.random().toString(36).slice(2));
      localStorage.setItem('sp_dev', d);
    }
    return d;
  }catch(e){ return 'nostorage'; }
}

class ApiError extends Error {
  constructor(status, msg){ super(msg); this.status = status; }
}

async function call(path, {method='GET', body=null, etag=null} = {}){
  const h = {};
  const tok = T.get();
  if(tok) h['Authorization'] = 'Bearer ' + tok;
  if(body) h['Content-Type'] = 'application/json';
  if(etag) h['If-None-Match'] = etag;
  let r;
  try{
    r = await fetch(API_BASE + path, {
      method, headers: h, body: body ? JSON.stringify(body) : undefined
    });
  }catch(e){
    throw new ApiError(0, '连不上服务器，请检查网络');
  }
  if(r.status === 304) return {notModified: true};
  let data = null;
  const txt = await r.text();
  if(txt){ try{ data = JSON.parse(txt); }catch(e){ data = null; } }
  if(!r.ok){
    throw new ApiError(r.status, (data && data.error) || `请求失败（${r.status}）`);
  }
  if(data && typeof data === 'object') data.__etag = r.headers.get('ETag');
  return data;
}

/* ---------- 答题上报队列：先落本地，再慢慢送。地铁里没信号也不丢记录 ---------- */
const Q = {
  key: 'sp_outbox',
  read(){ try{ return JSON.parse(localStorage.getItem(this.key) || '[]'); }catch(e){ return []; } },
  write(a){ try{ localStorage.setItem(this.key, JSON.stringify(a.slice(-500))); }catch(e){} },
  push(item){ const a = this.read(); a.push(item); this.write(a); this.schedule(); },
  timer: null,
  schedule(){
    if(this.timer) return;
    this.timer = setTimeout(()=>{ this.timer = null; this.flush(); }, 2500);
  },
  flushing: false,
  async flush(){
    if(this.flushing) return;
    const batch = this.read();
    if(!batch.length) return;
    this.flushing = true;
    try{
      await call('/attempts', {method:'POST', body: batch.slice(0, 200)});
      this.write(this.read().slice(batch.slice(0,200).length));
      if(this.read().length) this.schedule();
    }catch(e){
      if(e.status === 401 || e.status === 402){ /* 过期/掉登录，留着下次 */ }
      this.schedule();
    }finally{ this.flushing = false; }
  }
};
window.addEventListener('online', ()=>Q.flush());
document.addEventListener('visibilitychange', ()=>{ if(!document.hidden) Q.flush(); });

/* ---------- 内容缓存：ETag + localStorage，第二次打开是瞬开 ---------- */
const CONTENT_KEY = 'sp_content', ETAG_KEY = 'sp_content_etag';

async function loadContent(){
  let cached = null, etag = null;
  try{
    const raw = localStorage.getItem(CONTENT_KEY);
    if(raw){ cached = JSON.parse(raw); etag = localStorage.getItem(ETAG_KEY); }
  }catch(e){ cached = null; }
  try{
    const r = await call('/content', {etag: cached ? etag : null});
    if(r.notModified && cached) return cached;
    const et = r.__etag; delete r.__etag;
    try{
      localStorage.setItem(CONTENT_KEY, JSON.stringify(r));
      if(et) localStorage.setItem(ETAG_KEY, et);
    }catch(e){ /* 配额满就只用内存里这份 */ }
    return r;
  }catch(e){
    if(cached) return cached;   // 服务器挂了也还能刷题
    throw e;
  }
}

/* ---------- 闸门 UI ---------- */
const gate = document.getElementById('gate');
const app  = document.getElementById('appRoot');

function showGate(mode, msg){
  app.style.display = 'none';
  gate.style.display = 'flex';
  gate.innerHTML = `
    <div class="gate-card">
      <div class="gate-seal"><b>14</b><span>DAYS</span></div>
      <h1>14天备考证券从业考试</h1>
      <p class="gate-sub">金融市场基础知识 · 证券市场基本法律法规<br>
        <b>422</b> 道题 · <b>14</b> 天日程 · 云端错题本</p>
      <div class="gate-tabs" data-mode="${mode}">
        <button class="gate-tab${mode==='login'?' on':''}" data-m="login">登录</button>
        <button class="gate-tab${mode==='activate'?' on':''}" data-m="activate">激活码注册</button>
        <span class="gate-underline"></span>
      </div>
      ${msg ? `<div class="gate-msg warn">${msg}</div>` : ''}
      <form id="gateForm" autocomplete="on">
        <label class="gate-field${mode==='activate'?'':' hide'}">
          <span>激活码</span>
          <input name="code" placeholder="SEC-XXXX-XXXX-XXXX" autocapitalize="characters"
                 spellcheck="false" ${mode==='activate'?'required':''}>
        </label>
        <label class="gate-field">
          <span>邮箱</span>
          <input name="email" type="email" autocomplete="username" required
                 placeholder="用来登录，换设备时凭它找回进度">
        </label>
        <label class="gate-field">
          <span>密码</span>
          <input name="password" type="password" minlength="8" required
                 autocomplete="${mode==='activate'?'new-password':'current-password'}"
                 placeholder="${mode==='activate'?'至少 8 位':''}">
        </label>
        <div class="gate-err" id="gateErr"></div>
        <button class="gate-go" type="submit">${mode==='activate'?'激活并开始':'登录'}</button>
      </form>
      <p class="gate-foot">进度与错题本存在账号上，换手机、换浏览器登录后照旧。<br>
        同一账号最多 2 台设备同时使用。</p>
    </div>`;

  gate.querySelectorAll('.gate-tab').forEach(b=>{
    b.addEventListener('click', ()=>showGate(b.dataset.m, msg));
  });

  const form = document.getElementById('gateForm');
  const err = document.getElementById('gateErr');
  form.addEventListener('submit', async (ev)=>{
    ev.preventDefault();
    const btn = form.querySelector('.gate-go');
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = mode==='activate' ? '正在激活…' : '正在登录…';
    err.textContent = '';
    const f = new FormData(form);
    const payload = {
      email: (f.get('email')||'').trim(),
      password: f.get('password')||'',
      fp: deviceId()
    };
    if(mode === 'activate') payload.code = (f.get('code')||'').trim();
    try{
      const r = await call(mode==='activate' ? '/activate' : '/login',
                           {method:'POST', body: payload});
      T.set(r.token);
      gate.innerHTML = '';
      await start();
    }catch(e){
      err.textContent = e.message;
      btn.disabled = false;
      btn.textContent = label;
    }
  });
  const first = gate.querySelector('input[name="'+(mode==='activate'?'code':'email')+'"]');
  if(first) first.focus();
}

/* ---------- 启动 ---------- */
async function start(){
  const me = await call('/me');
  const [content, study] = await Promise.all([loadContent(), call('/study')]);

  window.__DATA = content;
  window.__SRV = {
    wrong: study.wrong || [],
    stats: study.stats || {},
    progress: study.progress || {}
  };
  window.__ME = me;

  gate.style.display = 'none';
  app.style.display = '';
  __runApp();
  renderAccountBar(me);
  Q.flush();
}

/* 截图外流时要能定位到人，但不该把完整邮箱印在页面上 */
function maskEmail(e){
  const i = String(e||'').indexOf('@');
  if(i < 1) return '账号';
  const name = e.slice(0, i), dom = e.slice(i);
  return (name.length <= 3 ? name[0] : name.slice(0, 3)) + '***' + dom;
}

function renderAccountBar(me){
  const bar = document.getElementById('acctBar');
  if(!bar) return;
  const left = me.days_left;
  const warn = left !== null && left <= 7;
  bar.innerHTML = `
    <span class="acct-who" title="完整邮箱仅你自己可见于登录时">${maskEmail(me.email)}</span>
    ${me.role === 'admin' ? '<span class="acct-tag">管理员</span>' : ''}
    <span class="acct-exp${warn?' warn':''}">${
      me.role === 'admin' ? '不限期'
      : left === null ? '未激活'
      : left === 0 ? '今天到期'
      : `剩 ${left} 天`}</span>
    <span class="acct-dev">${me.devices}/${me.max_devices} 台设备</span>
    <button class="acct-out" id="logoutBtn">退出</button>`;
  document.getElementById('logoutBtn').addEventListener('click', async ()=>{
    await Q.flush();
    T.clear();
    try{
      localStorage.removeItem(CONTENT_KEY);
      localStorage.removeItem(ETAG_KEY);
    }catch(e){}
    location.reload();
  });
  const wm = document.getElementById('watermark');
  if(wm) wm.textContent =
    `授权编号 #${String(me.uid == null ? 0 : me.uid).padStart(4, '0')} · 请勿外传`;
}

async function boot(){
  if(!T.get()){ showGate('login'); return; }
  try{
    await start();
  }catch(e){
    if(e.status === 402){
      T.clear();
      showGate('activate', '授权已到期。输入新的激活码可续期——时长从原到期日往后接，不会浪费剩余天数。');
    }else if(e.status === 401 || e.status === 403){
      T.clear();
      showGate('login', e.status === 403 ? e.message : '登录已过期，请重新登录。');
    }else{
      showGate('login', e.message);
    }
  }
}

boot();
