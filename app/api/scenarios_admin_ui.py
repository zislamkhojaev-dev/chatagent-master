"""HTML UI for /admin/scenarios."""


def get_admin_scenarios_html() -> str:
    return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Сценарии агента — Paynet</title>
  <style>
    :root {
      --border: #d0d7de;
      --bg: #f6f8fa;
      --card: #fff;
      --primary: #1976d2;
      --danger: #c62828;
      --muted: #57606a;
      --enabled: #2e7d32;
      --disabled: #9e9e9e;
    }
    * { box-sizing: border-box; }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      margin: 0; padding: 1.25rem;
      background: var(--bg); color: #1f2328;
    }
    .wrap { max-width: 1080px; margin: 0 auto; }
    .nav { margin-bottom: 1rem; font-size: 0.95rem; }
    .nav a { color: var(--primary); margin-right: 1rem; }
    h1 { font-size: 1.45rem; margin: 0 0 0.75rem 0; }
    .status {
      background: var(--card); border: 1px solid var(--border);
      padding: 0.75rem 1rem; border-radius: 8px; margin-bottom: 1rem;
      font-size: 0.9rem; color: var(--muted);
    }
    .toolbar {
      display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;
      margin-bottom: 1rem; position: sticky; top: 0; z-index: 5;
      background: var(--bg); padding: 0.5rem 0;
    }
    .tabs { display: flex; gap: 0.25rem; margin-right: auto; }
    .tab {
      padding: 0.45rem 0.9rem; border: 1px solid var(--border);
      background: var(--card); border-radius: 6px; cursor: pointer; font-size: 0.9rem;
    }
    .tab.active { background: var(--primary); color: #fff; border-color: var(--primary); }
    button {
      padding: 0.45rem 0.9rem; border-radius: 6px; border: 1px solid var(--border);
      background: var(--card); cursor: pointer; font-size: 0.9rem;
    }
    button.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
    button.danger { color: var(--danger); border-color: #ef9a9a; }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .panel { display: none; }
    .panel.active { display: block; }
    .global-card, .scenario-card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 10px; margin-bottom: 0.85rem; overflow: hidden;
    }
    .global-card { padding: 1rem 1.1rem; }
    .global-card h2, .scenario-head h3 { margin: 0; font-size: 1rem; }
    .global-card h2 { margin-bottom: 0.75rem; font-size: 1.05rem; }
    .scenario-card { border-left: 4px solid var(--enabled); }
    .scenario-card.off { border-left-color: var(--disabled); opacity: 0.92; }
    .scenario-head {
      display: flex; align-items: center; gap: 0.65rem;
      padding: 0.85rem 1rem; cursor: pointer; user-select: none;
      background: #fafbfc; border-bottom: 1px solid transparent;
    }
    .scenario-card.open .scenario-head { border-bottom-color: var(--border); }
    .scenario-head h3 { flex: 1; font-family: ui-monospace, monospace; font-size: 0.95rem; }
    .badge {
      font-size: 0.75rem; padding: 0.15rem 0.45rem; border-radius: 4px;
      background: #e3f2fd; color: #1565c0;
    }
    .badge.off { background: #eee; color: #666; }
    .scenario-body { display: none; padding: 1rem 1.1rem 1.1rem; }
    .scenario-card.open .scenario-body { display: block; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem 1rem; }
    @media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
    label { display: block; font-size: 0.8rem; font-weight: 600; color: var(--muted); margin-bottom: 0.25rem; }
    input[type="text"], input[type="number"], select, textarea {
      width: 100%; padding: 0.45rem 0.55rem; border: 1px solid var(--border);
      border-radius: 6px; font-size: 0.9rem; font-family: inherit;
    }
    textarea.mono { font-family: ui-monospace, monospace; font-size: 0.82rem; }
    .field { margin-bottom: 0.65rem; }
    .field-full { grid-column: 1 / -1; }
    .slots { margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px dashed var(--border); }
    .slot-card {
      background: #f6f8fa; border: 1px solid var(--border);
      border-radius: 8px; padding: 0.75rem; margin-bottom: 0.6rem;
    }
    .slot-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }
    .slot-head strong { font-size: 0.85rem; }
    .card-actions { display: flex; gap: 0.35rem; flex-shrink: 0; }
    .card-actions button { padding: 0.3rem 0.55rem; font-size: 0.8rem; }
    .toggle { display: flex; align-items: center; gap: 0.35rem; font-size: 0.85rem; cursor: pointer; }
    .toggle input { width: auto; }
    .chevron { color: var(--muted); font-size: 0.85rem; transition: transform 0.15s; }
    .scenario-card.open .chevron { transform: rotate(90deg); }
    #jsonEditor {
      width: 100%; min-height: 520px; font-family: ui-monospace, monospace;
      font-size: 13px; padding: 0.75rem; border: 1px solid var(--border); border-radius: 8px;
    }
    .result {
      display: none; margin-top: 1rem; padding: 0.75rem 1rem;
      border-radius: 8px; font-size: 0.9rem;
    }
    .result.show { display: block; }
    .result.ok { background: #e8f5e9; border: 1px solid #a5d6a7; color: #1b5e20; }
    .result.err { background: #ffebee; border: 1px solid #ef9a9a; color: #b71c1c; }
    .hint { font-size: 0.82rem; color: var(--muted); margin-top: 0.2rem; }
    .empty { text-align: center; color: var(--muted); padding: 2rem; border: 2px dashed var(--border); border-radius: 10px; }
  </style>
</head>
<body>
  <div class="wrap">
    <p class="nav"><a href="/admin/kb">База знаний</a> · <strong>Сценарии агента</strong></p>
    <h1>Управление сценариями</h1>
    <div class="status" id="status">Загрузка...</div>

    <div class="toolbar">
      <div class="tabs">
        <button type="button" class="tab active" data-tab="cards">Карточки</button>
        <button type="button" class="tab" data-tab="json">JSON целиком</button>
      </div>
      <button type="button" class="primary" id="btnSave">Сохранить</button>
      <button type="button" id="btnReload">Перезагрузить из файла</button>
      <button type="button" id="btnAddScenario">+ Сценарий</button>
    </div>

    <div id="panelCards" class="panel active">
      <div class="global-card">
        <h2>Глобальные настройки</h2>
        <div class="grid">
          <div class="field">
            <label for="globalVersion">version</label>
            <input type="number" id="globalVersion" min="1" value="1">
          </div>
          <div class="field">
            <label for="globalMaxClar">default_max_clarifications</label>
            <input type="number" id="globalMaxClar" min="0" value="2">
          </div>
          <div class="field field-full">
            <label for="globalAutoEsc">auto_escalate_categories (по одной на строку)</label>
            <textarea id="globalAutoEsc" rows="3" class="mono"></textarea>
          </div>
        </div>
      </div>
      <div id="scenarioList"></div>
    </div>

    <div id="panelJson" class="panel">
      <p class="hint">Полный конфиг scenarios.json. При переключении на «Карточки» изменения из JSON применятся к форме.</p>
      <textarea id="jsonEditor" spellcheck="false"></textarea>
    </div>

    <div id="result" class="result"></div>
  </div>
  <datalist id="topic-hints">
    <option value="qr">
    <option value="payment">
    <option value="refund">
    <option value="sms">
    <option value="identification">
    <option value="general">
  </datalist>

  <script>
    const api = (path, opts = {}) => fetch(path, { credentials: 'include', ...opts });

    let config = null;
    let activeTab = 'cards';
    let expandedIds = new Set();

    const POLICIES = ['never', 'if_ambiguous', 'always'];
    const AUDIENCES = ['client', 'agent', 'both'];
    const CHANNELS = ['', 'mobile_app', 'agent', 'infokiosk', 'general'];

    function showResult(ok, msg) {
      const el = document.getElementById('result');
      el.className = 'result show ' + (ok ? 'ok' : 'err');
      el.textContent = msg;
    }

    function linesToList(text) {
      return text.split(/\\n/).map(s => s.trim()).filter(Boolean);
    }

    function listToLines(arr) {
      return (arr || []).join('\\n');
    }

    function newScenarioTemplate() {
      const n = (config.scenarios.length + 1);
      return {
        id: 'new_scenario_' + n,
        enabled: true,
        triggers: ['ключевое слово'],
        clarify_policy: 'if_ambiguous',
        default_audience: 'client',
        default_channel: null,
        topic: null,
        required_slots: [],
        search_hint: '',
        description_ru: '',
        description_uz: '',
        max_clarifications: null,
      };
    }

    function newSlotTemplate() {
      return { id: 'slot_id', question_ru: 'Вопрос на русском?', question_uz: 'Savol o\\'zbekcha?' };
    }

    function selectOptions(values, selected, labels) {
      return values.map((v, i) => {
        const label = labels ? labels[i] : (v || '— не задан —');
        const sel = (selected === v || (selected == null && v === '')) ? ' selected' : '';
        return '<option value="' + v + '"' + sel + '>' + label + '</option>';
      }).join('');
    }

    function renderSlot(scenarioIdx, slotIdx, slot) {
      return '<div class="slot-card" data-slot="' + slotIdx + '">' +
        '<div class="slot-head"><strong>Слот #' + (slotIdx + 1) + '</strong>' +
        '<button type="button" class="danger btn-remove-slot" data-scenario="' + scenarioIdx + '" data-slot="' + slotIdx + '">Удалить слот</button></div>' +
        '<div class="grid">' +
        '<div class="field"><label>id</label><input class="slot-id" value="' + esc(slot.id) + '"></div>' +
        '<div class="field field-full"><label>question_ru</label><input class="slot-ru" value="' + esc(slot.question_ru) + '"></div>' +
        '<div class="field field-full"><label>question_uz</label><input class="slot-uz" value="' + esc(slot.question_uz) + '"></div>' +
        '</div></div>';
    }

    function esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
    }

    function renderScenarioCard(s, idx) {
      const open = expandedIds.has(s.id);
      const off = !s.enabled;
      const slotsHtml = (s.required_slots || []).map((sl, si) => renderSlot(idx, si, sl)).join('');
      return '<div class="scenario-card' + (open ? ' open' : '') + (off ? ' off' : '') + '" data-index="' + idx + '" data-id="' + esc(s.id) + '">' +
        '<div class="scenario-head" data-toggle="' + idx + '">' +
        '<span class="chevron">▶</span>' +
        '<h3>' + esc(s.id) + '</h3>' +
        '<span class="badge' + (off ? ' off' : '') + '">' + (s.enabled ? 'вкл' : 'выкл') + '</span>' +
        '<span class="badge">' + esc(s.clarify_policy || 'if_ambiguous') + '</span>' +
        '<div class="card-actions" onclick="event.stopPropagation()">' +
        '<button type="button" class="btn-dup" data-index="' + idx + '">Копия</button>' +
        '<button type="button" class="danger btn-del" data-index="' + idx + '">Удалить</button>' +
        '</div></div>' +
        '<div class="scenario-body">' +
        '<div class="grid">' +
        '<div class="field"><label>id</label><input class="f-id" value="' + esc(s.id) + '"></div>' +
        '<div class="field"><label class="toggle"><input type="checkbox" class="f-enabled"' + (s.enabled ? ' checked' : '') + '> enabled</label></div>' +
        '<div class="field"><label>clarify_policy</label><select class="f-policy">' +
        selectOptions(POLICIES, s.clarify_policy || 'if_ambiguous') + '</select></div>' +
        '<div class="field"><label>default_audience</label><select class="f-audience">' +
        selectOptions(AUDIENCES, s.default_audience || 'client') + '</select></div>' +
        '<div class="field"><label>default_channel</label><select class="f-channel">' +
        selectOptions(CHANNELS, s.default_channel || '', ['— не задан —', 'mobile_app', 'agent', 'infokiosk', 'general']) + '</select></div>' +
        '<div class="field"><label>topic</label><input class="f-topic" list="topic-hints" value="' + esc(s.topic || '') + '" placeholder="qr, payment, cashout…"></div>' +
        '<div class="field"><label>max_clarifications</label><input type="number" class="f-maxclar" min="0" placeholder="глобальный" value="' + (s.max_clarifications != null ? s.max_clarifications : '') + '"></div>' +
        '<div class="field field-full"><label>search_hint</label><input class="f-hint" value="' + esc(s.search_hint) + '" placeholder="QR {user_type}"></div>' +
        '<div class="field field-full"><label>description_ru (для embedding-router)</label>' +
        '<textarea class="f-desc-ru" rows="2">' + esc(s.description_ru || '') + '</textarea></div>' +
        '<div class="field field-full"><label>description_uz (для embedding-router)</label>' +
        '<textarea class="f-desc-uz" rows="2">' + esc(s.description_uz || '') + '</textarea></div>' +
        '<div class="field field-full"><label>triggers (по одному на строку)</label>' +
        '<textarea class="f-triggers mono" rows="4">' + esc(listToLines(s.triggers)) + '</textarea></div>' +
        '</div>' +
        '<div class="slots"><strong>Уточняющие слоты (required_slots)</strong>' +
        '<div class="slots-list">' + slotsHtml + '</div>' +
        '<button type="button" class="btn-add-slot" data-index="' + idx + '">+ Добавить слот</button></div>' +
        '</div></div>';
    }

    function renderCards() {
      const list = document.getElementById('scenarioList');
      if (!config.scenarios.length) {
        list.innerHTML = '<div class="empty">Нет сценариев. Нажмите «+ Сценарий».</div>';
        return;
      }
      list.innerHTML = config.scenarios.map((s, i) => renderScenarioCard(s, i)).join('');
      bindCardEvents();
    }

    function bindCardEvents() {
      document.querySelectorAll('.scenario-head[data-toggle]').forEach(el => {
        el.onclick = () => {
          const card = el.closest('.scenario-card');
          const id = card.dataset.id;
          if (card.classList.contains('open')) {
            card.classList.remove('open');
            expandedIds.delete(id);
          } else {
            card.classList.add('open');
            expandedIds.add(id);
          }
        };
      });
      document.querySelectorAll('.btn-del').forEach(btn => {
        btn.onclick = () => {
          collectFromCards();
          const idx = +btn.dataset.index;
          const id = config.scenarios[idx].id;
          if (!confirm('Удалить сценарий «' + id + '»?')) return;
          config.scenarios.splice(idx, 1);
          expandedIds.delete(id);
          renderCards();
        };
      });
      document.querySelectorAll('.btn-dup').forEach(btn => {
        btn.onclick = () => {
          collectFromCards();
          const idx = +btn.dataset.index;
          const copy = JSON.parse(JSON.stringify(config.scenarios[idx]));
          copy.id = copy.id + '_copy';
          let n = 2;
          while (config.scenarios.some(s => s.id === copy.id)) {
            copy.id = config.scenarios[idx].id + '_copy' + n++;
          }
          config.scenarios.splice(idx + 1, 0, copy);
          expandedIds.add(copy.id);
          renderCards();
        };
      });
      document.querySelectorAll('.btn-add-slot').forEach(btn => {
        btn.onclick = () => {
          collectFromCards();
          const idx = +btn.dataset.index;
          config.scenarios[idx].required_slots = config.scenarios[idx].required_slots || [];
          config.scenarios[idx].required_slots.push(newSlotTemplate());
          renderCards();
        };
      });
      document.querySelectorAll('.btn-remove-slot').forEach(btn => {
        btn.onclick = () => {
          collectFromCards();
          const si = +btn.dataset.scenario;
          const sl = +btn.dataset.slot;
          config.scenarios[si].required_slots.splice(sl, 1);
          renderCards();
        };
      });
    }

    function collectFromCards() {
      if (!config) return;
      config.version = parseInt(document.getElementById('globalVersion').value, 10) || 1;
      config.default_max_clarifications = parseInt(document.getElementById('globalMaxClar').value, 10) || 0;
      config.auto_escalate_categories = linesToList(document.getElementById('globalAutoEsc').value);

      document.querySelectorAll('.scenario-card').forEach(card => {
        const idx = +card.dataset.index;
        const s = config.scenarios[idx];
        if (!s) return;
        const oldId = s.id;
        s.id = card.querySelector('.f-id').value.trim() || oldId;
        if (oldId !== s.id) {
          if (expandedIds.has(oldId)) { expandedIds.delete(oldId); expandedIds.add(s.id); }
          card.dataset.id = s.id;
        }
        s.enabled = card.querySelector('.f-enabled').checked;
        s.clarify_policy = card.querySelector('.f-policy').value;
        s.default_audience = card.querySelector('.f-audience').value;
        const ch = card.querySelector('.f-channel').value;
        s.default_channel = ch || null;
        const tp = card.querySelector('.f-topic').value.trim();
        s.topic = tp || null;
        const mc = card.querySelector('.f-maxclar').value;
        s.max_clarifications = mc === '' ? null : parseInt(mc, 10);
        s.search_hint = card.querySelector('.f-hint').value.trim();
        s.description_ru = card.querySelector('.f-desc-ru').value.trim();
        s.description_uz = card.querySelector('.f-desc-uz').value.trim();
        s.triggers = linesToList(card.querySelector('.f-triggers').value);
        if (!s.triggers.length) s.triggers = ['trigger'];

        s.required_slots = [];
        card.querySelectorAll('.slot-card').forEach(slotEl => {
          s.required_slots.push({
            id: slotEl.querySelector('.slot-id').value.trim() || 'slot',
            question_ru: slotEl.querySelector('.slot-ru').value.trim(),
            question_uz: slotEl.querySelector('.slot-uz').value.trim(),
          });
        });
      });
    }

    function fillGlobalFields() {
      document.getElementById('globalVersion').value = config.version || 1;
      document.getElementById('globalMaxClar').value = config.default_max_clarifications ?? 2;
      document.getElementById('globalAutoEsc').value = listToLines(config.auto_escalate_categories);
    }

    function syncToJson() {
      collectFromCards();
      document.getElementById('jsonEditor').value = JSON.stringify(config, null, 2);
    }

    function syncFromJson() {
      try {
        config = JSON.parse(document.getElementById('jsonEditor').value);
        if (!config.scenarios) config.scenarios = [];
        fillGlobalFields();
        renderCards();
        return true;
      } catch (e) {
        showResult(false, 'Невалидный JSON: ' + e.message);
        return false;
      }
    }

    function getConfigForSave() {
      if (activeTab === 'json') {
        if (!syncFromJson()) return null;
      } else {
        collectFromCards();
        syncToJson();
      }
      return config;
    }

    function switchTab(tab) {
      if (activeTab === 'cards' && tab === 'json') syncToJson();
      if (activeTab === 'json' && tab === 'cards') syncFromJson();
      activeTab = tab;
      document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
      document.getElementById('panelCards').classList.toggle('active', tab === 'cards');
      document.getElementById('panelJson').classList.toggle('active', tab === 'json');
    }

    async function loadStatus() {
      const r = await api('/scenarios/status');
      const d = await r.json();
      document.getElementById('status').textContent =
        'Файл: ' + (d.file_exists ? 'да' : 'нет') +
        ' | Сценариев: ' + d.count + ' (активных: ' + d.enabled_count + ')' +
        ' | Обновлено: ' + (d.updated_at || '—') +
        (d.backup_exists ? ' | Бэкап: есть' : '');
    }

    async function loadScenarios() {
      const r = await api('/scenarios');
      config = await r.json();
      if (!config.scenarios) config.scenarios = [];
      fillGlobalFields();
      renderCards();
      document.getElementById('jsonEditor').value = JSON.stringify(config, null, 2);
      await loadStatus();
    }

    document.querySelectorAll('.tab').forEach(t => {
      t.onclick = () => switchTab(t.dataset.tab);
    });

    document.getElementById('btnAddScenario').onclick = () => {
      if (activeTab === 'json') syncFromJson();
      else collectFromCards();
      const s = newScenarioTemplate();
      config.scenarios.push(s);
      expandedIds.add(s.id);
      if (activeTab === 'cards') renderCards();
      else syncToJson();
    };

    document.getElementById('btnSave').onclick = async () => {
      const data = getConfigForSave();
      if (!data) return;
      const r = await api('/scenarios', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const j = await r.json().catch(() => ({}));
      if (r.ok) {
        showResult(true, 'Сохранено. Сценариев: ' + j.count + ', дата: ' + j.updated_at);
        await loadScenarios();
      } else {
        showResult(false, 'Ошибка: ' + (j.detail ? JSON.stringify(j.detail) : r.status));
      }
    };

    document.getElementById('btnReload').onclick = async () => {
      if (!confirm('Отменить несохранённые изменения и перезагрузить из файла?')) return;
      const r = await api('/scenarios/reload', { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (r.ok) {
        showResult(true, 'Перезагружено. Сценариев: ' + j.count);
        expandedIds.clear();
        await loadScenarios();
      } else {
        showResult(false, 'Ошибка перезагрузки');
      }
    };

    loadScenarios();
  </script>
</body>
</html>"""
