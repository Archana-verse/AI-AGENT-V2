let sessionId    = localStorage.getItem('session_id') || crypto.randomUUID();
let currentMode  = 'email';
let history      = [];
let running      = false;
let isAuth       = false;
let userEmail    = '';
let lastEmailData = null;
let lastReplyText = '';

localStorage.setItem('session_id', sessionId);


function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function validateReceiverEmail() {
  const emailInput = document.getElementById('emailTo');
  const statusEl   = document.getElementById('emailStatus');

  if (!emailInput || !statusEl) return;

  const email = emailInput.value.trim();

  if (!email) {
    statusEl.textContent = '';
    return;
  }

  if (isValidEmail(email)) {
    statusEl.textContent = "✓ Valid email address";
    statusEl.style.color = "#22c55e";
  } else {
    statusEl.textContent = "✗ Invalid email address";
    statusEl.style.color = "#ef4444";
  }
}

const urlParams = new URLSearchParams(window.location.search);
if (urlParams.get('auth') === 'success') {
  const sid = urlParams.get('session_id');
  if (sid) { sessionId = sid; localStorage.setItem('session_id', sid); }
  window.history.replaceState({}, '', '/');
}


window.addEventListener('DOMContentLoaded', () => {
  checkAuth();

  const emailInput = document.getElementById('emailTo');
  if (emailInput) {
    emailInput.addEventListener('input', validateReceiverEmail);
  }
});

async function checkAuth() {
  try {
    const res  = await fetch(`/auth/status?session_id=${sessionId}`);
    const data = await res.json();
    isAuth     = data.authenticated;
    userEmail  = data.email || '';
    updateAuthUI();
    if (isAuth) loadScheduled();
  } catch (e) {
    console.error('Auth check failed', e);
  }
}

function updateAuthUI() {
  const btn = document.getElementById('authBtn');
  if (isAuth) {
    btn.textContent = `✓ ${userEmail}`;
    btn.className   = 'auth-btn connected';
    document.getElementById('scheduledBtn').style.display = 'inline-flex';
  } else {
    btn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z" fill="currentColor"/></svg> Connect Gmail`;
    btn.className = 'auth-btn';
    document.getElementById('scheduledBtn').style.display = 'none';
  }
}


async function handleAuth() {
  if (isAuth) {
    await fetch(`/auth/logout?session_id=${sessionId}`);
    isAuth = false; userEmail = '';
    updateAuthUI();
    return;
  }
  const res  = await fetch(`/auth/login?session_id=${sessionId}`);
  const data = await res.json();
  window.location.href = data.auth_url;
}


const MODES = {
  email:   { label: 'Write Email',   steps: ['Understand','Drafting','Structuring','Polishing','Deliver'] },
  summary: { label: 'Summarize',     steps: ['Understand','Extracting','Structuring','Refining','Deliver'] },
  plan:    { label: 'Action Plan',   steps: ['Understand','Research','Planning','Refining','Deliver'] },
  report:  { label: 'Draft Report',  steps: ['Understand','Outline','Drafting','Refining','Deliver'] },
  custom:  { label: 'Custom Task',   steps: ['Understand','Analyze','Generate','Refine','Deliver'] }
};

function setMode(m) {
  currentMode = m;
  document.querySelectorAll('.mode-btn').forEach(b =>
    b.classList.toggle('active', b.textContent.trim() === MODES[m].label)
  );
  resetPipeline();
}


function resetPipeline() {
  const steps = MODES[currentMode].steps;
  ['s1','s2','s3','s4','s5'].forEach((id, i) => {
    const el = document.getElementById(id);
    el.textContent = steps[i];
    el.className   = 'step-badge';
  });
}

function activateStep(i) {
  ['s1','s2','s3','s4','s5'].forEach((id, j) => {
    const el = document.getElementById(id);
    if      (j < i)  el.className = 'step-badge done';
    else if (j === i) el.className = 'step-badge active';
    else              el.className = 'step-badge';
  });
}

function allDone() {
  ['s1','s2','s3','s4','s5'].forEach(id =>
    document.getElementById(id).className = 'step-badge done'
  );
}


function useQuick(btn) {
  const inp = document.getElementById('inp');
  inp.value = btn.textContent.trim();
  autoResize(inp);
  inp.focus();
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 100) + 'px';
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}


function addMsg(role, html, thinking = '') {
  const msgs = document.getElementById('messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + role;
  const av  = document.createElement('div');
  av.className  = 'avatar ' + role;
  av.textContent = role === 'ai' ? 'AI' : 'You';
  const bub = document.createElement('div');
  bub.className = 'bubble ' + role;
  bub.innerHTML = thinking ? `<div class="thinking">${thinking}</div>${html}` : html;
  wrap.appendChild(av);
  wrap.appendChild(bub);
  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
  return bub;
}

function addTyping() {
  const msgs = document.getElementById('messages');
  const div  = document.createElement('div');
  div.className = 'msg ai';
  div.id = 'typing-indicator';
  div.innerHTML = `<div class="avatar ai">AI</div><div class="bubble ai"><div class="typing"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div></div>`;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function removeTyping() {
  const t = document.getElementById('typing-indicator');
  if (t) t.remove();
}

function formatResponse(text) {
  return text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/Step (\d+) — ([^:]+):/g, (_, n, label) => {
      const tags = ['tag-analyze','tag-analyze','tag-generate','tag-refine','tag-done'];
      return `<br><span class="step-tag ${tags[Math.min(n-1,4)]}">Step ${n}</span> <strong>${label}:</strong>`;
    })
    .replace(/✓ (.+)/g, '<br><span class="step-tag tag-done">✓ $1</span>')
    .replace(/\n\n/g, '<br><br>')
    .replace(/\n/g, '<br>');
}

function addActionBar(bub, emailData, rawText) {
  const bar = document.createElement('div');
  bar.className = 'action-bar';

  const dlBtn = document.createElement('button');
  dlBtn.className = 'action-btn download-btn';
  dlBtn.innerHTML = '⬇ Download';
  dlBtn.onclick = () => downloadContent(rawText, currentMode);
  bar.appendChild(dlBtn);

  if (currentMode === 'email' || emailData) {
    const emailBtn = document.createElement('button');
    emailBtn.className = 'action-btn email-btn';
    emailBtn.innerHTML = '✉ Send Email';
    emailBtn.onclick = () => openEmailModal(emailData, rawText);
    bar.appendChild(emailBtn);
  }

  bub.appendChild(bar);
}


async function sendMessage() {
  const inp  = document.getElementById('inp');
  const text = inp.value.trim();
  if (!text || running) return;

  running = true;
  document.getElementById('sendBtn').disabled = true;
  resetPipeline();
  activateStep(0);

  addMsg('user', text);
  inp.value = '';
  inp.style.height = 'auto';
  history.push({ role: 'user', content: text });

  addTyping();

  let stepIdx = 0;
  const stepTimer = setInterval(() => {
    stepIdx = Math.min(stepIdx + 1, 4);
    activateStep(stepIdx);
  }, 1200);

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: currentMode, history, session_id: sessionId })
    });

    clearInterval(stepTimer);
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || 'Server error'); }

    const data       = await res.json();
    const reply      = data.reply || '';
    lastReplyText    = reply;
    lastEmailData    = data.email_data || null;

    removeTyping();
    allDone();

    const bub = addMsg('ai', formatResponse(reply), 'Agent completed workflow');
    addActionBar(bub, lastEmailData, reply);

    history.push({ role: 'assistant', content: reply });

  } catch (err) {
    clearInterval(stepTimer);
    removeTyping();
    activateStep(0);
    addMsg('ai', `<span class="step-tag tag-error">Error</span> ${err.message}`, 'Agent error');
  }

  running = false;
  document.getElementById('sendBtn').disabled = false;
}


function downloadContent(text, mode) {
  const clean = text
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/\*(.*?)\*/g, '$1')
    .replace(/<[^>]+>/g, '');
  const blob = new Blob([clean], { type: 'text/plain' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `${mode}-output-${Date.now()}.txt`;
  a.click();
  URL.revokeObjectURL(url);
}


function openEmailModal(emailData, rawText) {
  if (!isAuth) {
    addStatusMsg('Please connect your Gmail account first.', 'error');
    return;
  }

  const toInput = document.getElementById('emailTo');

  toInput.value = emailData?.to || '';
  document.getElementById('emailSubject').value = emailData?.subject || '';
  document.getElementById('emailBody').value = emailData?.body || cleanBody(rawText);

  validateReceiverEmail();

  const now = new Date(Date.now() + 60 * 60 * 1000);
  document.getElementById('scheduleTime').value = now.toISOString().slice(0, 16);

  document.getElementById('scheduleToggle').checked = false;
  document.getElementById('scheduleSection').style.display = 'none';
  document.getElementById('emailModal').style.display = 'flex';
}

function closeEmailModal() {
  document.getElementById('emailModal').style.display = 'none';
}

function toggleSchedule() {
  const checked = document.getElementById('scheduleToggle').checked;
  document.getElementById('scheduleSection').style.display = checked ? 'block' : 'none';
  const btn = document.getElementById('sendEmailBtn');
  btn.innerHTML = checked
    ? `<svg viewBox="0 0 24 24" width="14" height="14"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67V7z" fill="currentColor"/></svg> Schedule`
    : `<svg viewBox="0 0 24 24" width="14" height="14"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" fill="currentColor"/></svg> Send Now`;
}

function cleanBody(rawText) {
  const lines  = rawText.split('\n');
  const start  = lines.findIndex(l => l.trim().startsWith('SUBJECT:'));
  if (start === -1) return rawText.replace(/Step \d+ —[^\n]*/g, '').trim();
  return lines.slice(start + 1).join('\n').trim();
}

async function sendEmail() {
  const to      = document.getElementById('emailTo').value.trim();
  const subject = document.getElementById('emailSubject').value.trim();
  const body    = document.getElementById('emailBody').value.trim();
  const isScheduled = document.getElementById('scheduleToggle').checked;

  if (!to || !subject || !body) {
    addStatusMsg('Please fill in To, Subject, and Body.', 'error');
    return;
  }

  if (!isValidEmail(to)) {
    addStatusMsg('Receiver email address is invalid.', 'error');
    return;
  }

  const btn = document.getElementById('sendEmailBtn');
  btn.disabled = true;
  const origHTML = btn.innerHTML;
  btn.textContent = isScheduled ? 'Scheduling...' : 'Sending...';

  try {
    if (isScheduled) {
      const sendAt = document.getElementById('scheduleTime').value;
      if (!sendAt) { addStatusMsg('Please pick a date and time.', 'error'); return; }

      const res  = await fetch('/api/schedule-email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, to, subject, body, send_at: sendAt })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail);

      closeEmailModal();
      addStatusMsg(`Email scheduled for ${new Date(sendAt).toLocaleString()}`, 'success');
      loadScheduled();

    } else {
      const res  = await fetch('/api/send-email', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, to, subject, body })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail);

      closeEmailModal();
      addStatusMsg(`Email sent successfully to ${to}`, 'success');
    }

  } catch (err) {
    addStatusMsg(`Failed: ${err.message}`, 'error');
  } finally {
    btn.disabled  = false;
    btn.innerHTML = origHTML;
  }
}


function addStatusMsg(text, type) {
  const msgs = document.getElementById('messages');
  const div  = document.createElement('div');
  div.className = `status-msg status-${type}`;
  div.textContent = type === 'success' ? `✓ ${text}` : `✗ ${text}`;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  setTimeout(() => div.remove(), 6000);
}

async function loadScheduled() {
  try {
    const res  = await fetch(`/api/scheduled-emails?session_id=${sessionId}`);
    const data = await res.json();
    renderScheduled(data.jobs || []);
  } catch (e) {}
}

function renderScheduled(jobs) {
  const list = document.getElementById('scheduledList');
  if (!jobs.length) {
    list.innerHTML = `<div style="padding:12px 14px;font-size:12px;color:var(--text-tertiary)">No scheduled emails.</div>`;
    return;
  }
  list.innerHTML = jobs.map(j => `
    <div class="scheduled-item">
      <div class="s-to">${j.to}</div>
      <div class="s-time">${new Date(j.send_at).toLocaleString()}</div>
      <div class="s-status ${j.status}">${j.status}</div>
      ${j.status === 'scheduled' ? `<button class="cancel-btn" onclick="cancelJob('${j.id}')">Cancel</button>` : ''}
    </div>
  `).join('');
}

async function cancelJob(jobId) {
  await fetch(`/api/scheduled-emails/${jobId}`, { method: 'DELETE' });
  loadScheduled();
}

function toggleScheduledPanel() {
  const panel = document.getElementById('scheduledPanel');
  const shown = panel.style.display !== 'none';
  panel.style.display = shown ? 'none' : 'block';
  if (!shown) loadScheduled();
}
