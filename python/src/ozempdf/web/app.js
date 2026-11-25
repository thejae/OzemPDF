/* OzemPDF web client. Plain DOM, no framework, no build step. */
(() => {
  'use strict';

  /* ------------------------------------------------------------------ */
  /* DOM helpers                                                        */
  /* ------------------------------------------------------------------ */

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function icon(name, className) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    if (className) svg.setAttribute('class', className);
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#i-${name}`);
    svg.appendChild(use);
    return svg;
  }

  const state = {
    version: '0.3.0',
    strings: {},
    languages: [],
    config: {},
    queue: [],
    run: { running: false, completed: 0, total: 0, summary: null, cancelled: false },
    settings: { dark_mode: true, advanced_open: false },
    step: 1,
    selected: null,
    pollTimer: null,
    queueSignature: '',
    secrets: { docPassword: '', certPassword: '' },
    signCertPath: '',
    picker: { mode: 'files', path: '', entries: [], selected: new Set(), roots: [] },
    configTimer: null,
  };

  /* ------------------------------------------------------------------ */
  /* i18n                                                               */
  /* ------------------------------------------------------------------ */

  function t(key, params) {
    let template = state.strings[key];
    if (template === undefined) return key;
    if (params) {
      template = template.replace(/\{(\w+)\}/g, (match, name) =>
        Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
      );
    }
    return template;
  }

  function applyI18n(root = document) {
    $$('[data-i18n]', root).forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    $$('[data-i18n-title]', root).forEach((node) => {
      node.title = t(node.dataset.i18nTitle);
    });
    $$('[data-i18n-aria]', root).forEach((node) => {
      node.setAttribute('aria-label', t(node.dataset.i18nAria));
    });
    $$('[data-i18n-placeholder]', root).forEach((node) => {
      node.placeholder = t(node.dataset.i18nPlaceholder);
    });
    document.title = t('app.window_title', { version: state.version });
    const versionLabel = $('#versionLabel');
    if (versionLabel) versionLabel.textContent = `v${state.version}`;
    const helpVersion = $('#helpVersion');
    if (helpVersion) helpVersion.textContent = `v${state.version}`;
  }

  /* ------------------------------------------------------------------ */
  /* API                                                                */
  /* ------------------------------------------------------------------ */

  async function api(path, body = null, method = 'POST') {
    const options = { method, headers: {} };
    if (body !== null) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let payload = {};
    try {
      payload = await response.json();
    } catch (error) {
      payload = {};
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  /* ------------------------------------------------------------------ */
  /* Formatting                                                         */
  /* ------------------------------------------------------------------ */

  function formatBytes(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = Math.abs(Number(value));
    let index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size /= 1024;
      index += 1;
    }
    const text = index === 0 ? `${Math.round(size)} ${units[index]}` : `${size.toFixed(1)} ${units[index]}`;
    return `${value < 0 ? '-' : ''}${text}`;
  }

  function formatPercent(value, digits = 1) {
    if (value === null || value === undefined) return '—';
    return `${Number(value).toFixed(digits)}%`;
  }

  function pluralFiles(count) {
    return count === 1 ? '1 file' : `${count} files`;
  }

  /* ------------------------------------------------------------------ */
  /* Boot                                                               */
  /* ------------------------------------------------------------------ */

  async function boot() {
    try {
      const data = await api('/api/bootstrap', null, 'GET');
      state.version = data.version;
      state.strings = data.strings;
      state.languages = data.languages;
      state.language = data.language;
      state.config = data.config;
      state.signCertPath = data.config.sign_pkcs12_path || '';
      state.settings = data.settings;
      state.queue = data.queue;
      state.run = data.run;
      applyI18n();
      applyTheme(state.settings.dark_mode);
      syncControls();
      renderLanguageMenu();
      const hasDone = state.queue.some((item) => item.status === 'done');
      const initialStep = hasDone ? 3 : state.queue.length ? 2 : 1;
      setStep(initialStep);
      renderAll();
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  /* ------------------------------------------------------------------ */
  /* Theme & language                                                   */
  /* ------------------------------------------------------------------ */

  function applyTheme(dark) {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    state.settings.dark_mode = dark;
    const use = $('#themeIcon');
    if (use) use.setAttribute('href', dark ? '#i-sun' : '#i-moon');
    const button = $('#themeBtn');
    if (button) button.title = dark ? t('header.theme_tip') : t('header.theme_tip_light');
  }

  async function toggleTheme() {
    const dark = !state.settings.dark_mode;
    applyTheme(dark);
    try {
      await api('/api/ui', { dark_mode: dark });
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  function renderLanguageMenu() {
    const menu = $('#langMenu');
    menu.replaceChildren();
    state.languages.forEach((language) => {
      const button = el('button', 'menu-item');
      button.type = 'button';
      button.setAttribute('role', 'menuitemradio');
      button.setAttribute('aria-checked', String(language.code === state.language));
      const name = el('span', 'menu-name', language.native_name);
      const code = el('span', 'menu-code mono', language.code.toUpperCase());
      if (language.code === state.language) {
        button.classList.add('active');
        button.appendChild(icon('check', 'menu-check'));
      }
      button.append(name, code);
      button.addEventListener('click', () => chooseLanguage(language.code));
      menu.appendChild(button);
    });
    const code = (state.language || 'en').split('_')[0].toUpperCase();
    $('#langCode').textContent = code;
  }

  async function chooseLanguage(code) {
    $('#langMenu').hidden = true;
    $('#langBtn').setAttribute('aria-expanded', 'false');
    try {
      const data = await api('/api/ui', { language: code });
      state.strings = data.strings;
      state.languages = data.languages;
      state.language = data.language;
      state.settings = { ...state.settings, ...data.settings };
      document.documentElement.lang = data.language;
      applyI18n();
      applyTheme(state.settings.dark_mode);
      renderLanguageMenu();
      syncControls();
      renderAll();
      if ($('#inspectorDialog').open) renderInspectorChrome();
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  /* ------------------------------------------------------------------ */
  /* Steps                                                              */
  /* ------------------------------------------------------------------ */

  const stepPanels = { 1: '#stepOne', 2: '#stepTwo', 3: '#stepThree' };

  function setStep(step) {
    state.step = step;
    Object.entries(stepPanels).forEach(([key, selector]) => {
      $(selector).hidden = Number(key) !== step;
    });
    $$('#stepper .step').forEach((button) => {
      const value = Number(button.dataset.step);
      button.classList.toggle('active', value === step);
      button.classList.toggle('done', value < step);
      if (value === step) button.setAttribute('aria-current', 'step');
      else button.removeAttribute('aria-current');
    });
    mountQueue(step);
    renderDock();
    renderStepperState();
  }

  function mountQueue(step) {
    const panel = $('#queuePanel');
    const slot = $('#resultsQueueSlot');
    if (step === 3) slot.appendChild(panel);
    else {
      const modeCard = $('#modeCard');
      $('#stepTwo').insertBefore(panel, modeCard);
    }
  }

  function renderStepperState() {
    const hasQueue = state.queue.length > 0;
    const hasDone = state.queue.some((item) => item.status === 'done');
    const running = state.run.running;
    $$('#stepper .step').forEach((button) => {
      const value = Number(button.dataset.step);
      button.disabled =
        running || (value === 2 && !hasQueue) || (value === 3 && !hasDone && !state.run.summary);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Queue rendering                                                    */
  /* ------------------------------------------------------------------ */

  function setQueue(queue) {
    state.queue = queue;
    renderAll();
  }

  function renderAll() {
    renderQueue();
    renderStepperState();
    renderDock();
    renderMode();
    if (state.step === 3) renderResults();
  }

  function queueCounts() {
    const pending = state.queue.filter((item) =>
      ['queued', 'new_queued'].includes(item.status)
    );
    const done = state.queue.filter((item) => item.status === 'done');
    const failed = state.queue.filter((item) => item.status === 'error');
    const totalBytes = pending.reduce((sum, item) => sum + (item.size || 0), 0);
    const totalPages = state.queue.reduce((sum, item) => sum + (item.pages || 0), 0);
    return { pending, done, failed, totalBytes, totalPages };
  }

  function statusCell(item) {
    const cell = el('td', 'c-status');
    const wrap = el('div', 'status');
    const dot = el('span', 'status-dot');
    const text = el('span', 'status-text');
    switch (item.status) {
      case 'processing':
        dot.classList.add('accent', 'pulse');
        text.textContent = t('step2.status_processing');
        const bar = el('span', 'mini-bar');
        bar.appendChild(el('span'));
        wrap.append(dot, text, bar);
        break;
      case 'done':
        dot.classList.add('success');
        text.textContent = item.result && item.result.already_optimal
          ? t('step2.status_already_optimal')
          : t('step2.status_done', { size: formatBytes(item.result ? item.result.compressed_bytes : 0) });
        wrap.append(dot, text);
        if (item.result && item.result.protection_removed) {
          const unlocked = icon('lock-open', 'status-extra');
          unlocked.setAttribute('aria-hidden', 'true');
          unlocked.title = t('step2.permissions_removed_tip');
          wrap.appendChild(unlocked);
        }
        if (item.result && item.result.signed) {
          const signed = icon('check', 'status-extra');
          signed.setAttribute('aria-hidden', 'true');
          wrap.appendChild(signed);
        }
        break;
      case 'error':
        dot.classList.add('danger');
        text.textContent = t('step2.status_error', { error: item.error || '—' });
        text.title = item.error || '';
        wrap.append(dot, text);
        break;
      case 'new_queued':
        dot.classList.add('accent');
        text.textContent = t('step2.status_new');
        wrap.append(dot, text);
        break;
      default:
        dot.classList.add('muted');
        if (item.locked) {
          text.textContent = item.protection === 'unsupported'
            ? t('step2.status_unsupported')
            : t('step2.status_locked');
        } else {
          text.textContent = t('step2.status_queued');
        }
        if (item.locked) wrap.appendChild(icon('lock', 'status-extra'));
        wrap.append(dot, text);
    }
    cell.appendChild(wrap);
    return cell;
  }

  function savingsCell(item) {
    const cell = el('td', 'c-savings');
    const chip = el('span', 'chip');
    if (item.status === 'done' && item.result) {
      const percent = item.result.reduction_percent || 0;
      chip.classList.add(percent > 0 ? 'success' : 'neutral');
      chip.textContent = `−${formatPercent(percent)}`;
      if (item.result.already_optimal) {
        chip.textContent = '0.0%';
        chip.title = t('step3.already_optimal');
      }
    } else if (item.status === 'error') {
      chip.classList.add('danger');
      chip.textContent = t('step2.savings_failed');
    } else {
      chip.classList.add('muted');
      chip.textContent = item.status === 'processing' ? '—' : t('step2.savings_pending');
    }
    cell.appendChild(chip);
    return cell;
  }

  function actionCell(item) {
    const cell = el('td', 'c-action');
    const wrap = el('div', 'row-actions');
    const add = (label, action, variant = 'ghost', tip = '') => {
      const button = el('button', `btn ${variant} xs`, label);
      button.type = 'button';
      button.dataset.action = action;
      button.dataset.id = item.id;
      if (tip) {
        button.title = tip;
        button.setAttribute('aria-label', tip);
      }
      wrap.appendChild(button);
    };
    if (item.status === 'done') {
      add(t('step2.action_inspect'), 'inspect', 'ghost');
      add(t('step2.action_reset'), 'reset', 'ghost', t('step2.action_reset_tip'));
    } else if (item.status === 'error') {
      add(t('step2.action_copy'), 'copy_error', 'ghost', t('step2.action_copy_tip'));
      add(t('step2.action_retry'), 'retry');
      add(t('step2.action_remove'), 'remove');
    } else if (item.status !== 'processing') {
      add(t('step2.action_remove'), 'remove');
    }
    cell.appendChild(wrap);
    return cell;
  }

  function thumbCell(item) {
    const cell = el('td', 'c-doc');
    const thumb = el('button', 'thumb');
    thumb.type = 'button';
    thumb.dataset.action = 'inspect';
    thumb.dataset.id = item.id;
    thumb.title = t('step2.thumb_tip', { name: item.name });
    thumb.setAttribute('aria-label', thumb.title);
    if (item.has_thumb) {
      const img = el('img');
      img.src = `/api/thumb?id=${encodeURIComponent(item.id)}&v=${item.thumb_version}`;
      img.alt = '';
      img.loading = 'lazy';
      img.draggable = false;
      thumb.appendChild(img);
    } else {
      thumb.appendChild(icon('file', 'thumb-placeholder'));
    }
    const meta = el('div', 'doc-meta');
    const titleRow = el('div', 'doc-title');
    const name = el('span', 'doc-name', item.name);
    name.title = item.path;
    titleRow.appendChild(name);
    if (item.locked) titleRow.appendChild(icon('lock', 'doc-lock'));
    if (item.protection === 'permissions') {
      const unlocked = icon('lock-open', 'doc-unlocked');
      unlocked.setAttribute(
        'aria-label',
        item.status === 'done'
          ? t('step2.permissions_removed_tip')
          : t('step2.permissions_tip')
      );
      unlocked.title = unlocked.getAttribute('aria-label');
      titleRow.appendChild(unlocked);
    }
    const path = el('span', 'doc-path mono', item.rel_path || folderName(item.path));
    path.title = item.path;
    meta.append(titleRow, path);
    const doc = el('div', 'doc');
    doc.append(thumb, meta);
    cell.appendChild(doc);
    return cell;
  }

  function folderName(path) {
    const parts = String(path || '').split(/[\\/]/);
    return parts.length > 1 ? parts[parts.length - 2] : '';
  }

  function renderQueue() {
    const signature = JSON.stringify([
      state.queue.map((item) => [
        item.id, item.status, item.pages, item.size, item.error, item.thumb_version,
        item.result ? item.result.reduction_percent : null,
        item.result ? item.result.already_optimal : null,
      ]),
      state.step,
      state.selected,
    ]);
    if (signature === state.queueSignature) {
      updateQueueHeadings();
      return;
    }
    state.queueSignature = signature;

    const body = $('#queueBody');
    body.replaceChildren();
    state.queue.forEach((item) => {
      const row = document.createElement('tr');
      row.className = `queue-row status-${item.status}`;
      row.dataset.id = item.id;
      row.tabIndex = 0;
      if (item.status === 'done') row.title = t('step2.row_tip');
      if (state.selected === item.id) row.classList.add('selected');
      row.appendChild(thumbCell(item));
      const pages = el('td', 'c-pages mono', item.pages ? `${item.pages}p` : '…');
      row.appendChild(pages);
      row.appendChild(el('td', 'c-size mono', formatBytes(item.size)));
      row.appendChild(statusCell(item));
      row.appendChild(savingsCell(item));
      row.appendChild(actionCell(item));
      body.appendChild(row);
    });

    const empty = state.queue.length === 0;
    $('#queueEmpty').hidden = !empty;
    $('.queue-table').hidden = empty;
    updateQueueHeadings();
    renderDock();
  }

  function updateQueueHeadings() {
    const { pending, done, totalPages } = queueCounts();
    const size = state.queue.reduce((sum, item) => sum + (item.size || 0), 0);
    // Reset all exists to re-slim finished files, so it is only useful (and
    // only enabled) once at least one result exists.
    const resetAll = $('#btnResetAll');
    if (resetAll) resetAll.disabled = done.length === 0 || state.run.running;
    const heading = $('#queueTitle');
    const summary = $('#queueSummary');
    if (pending.length && done.length) {
      heading.textContent = t('step2.queue_heading_mixed', { done: done.length, pending: pending.length });
    } else {
      heading.textContent = t('step2.queue_heading_count', { count: state.queue.length });
    }
    summary.textContent = t('step2.queue_summary', {
      pages: totalPages === 1
        ? t('step2.pages_one', { count: 1 })
        : t('step2.pages_many', { count: totalPages }),
      size: formatBytes(size),
    });

    const resultsTitle = $('#resultsQueueTitle');
    const resultsSummary = $('#resultsQueueSummary');
    if (resultsTitle) {
      if (pending.length && done.length) {
        resultsTitle.textContent = t('step2.queue_heading_mixed', { done: done.length, pending: pending.length });
      } else {
        resultsTitle.textContent = t('step3.results_heading_count', { count: state.queue.length });
      }
      const before = state.queue.reduce((sum, item) => sum + (item.size || 0), 0);
      const after = state.queue.reduce(
        (sum, item) => sum + (item.status === 'done' && item.result ? item.result.compressed_bytes : item.size || 0),
        0
      );
      resultsSummary.textContent = t('step3.results_summary', {
        pages: totalPages === 1
          ? t('step2.pages_one', { count: 1 })
          : t('step2.pages_many', { count: totalPages }),
        size: formatBytes(before),
        after: formatBytes(after),
      });
    }
  }

  function renderDock() {
    const { pending, done } = queueCounts();
    const running = state.run.running;
    const run = $('#btnRun');
    const stop = $('#btnStop');
    const newBatch = $('#btnNewBatch');

    let label;
    if (running) {
      label = t('footer.slimming', {
        done: state.run.completed,
        total: state.run.total || pending.length,
      });
    } else if (!pending.length) {
      label = t('footer.nothing');
    } else {
      const size = formatBytes(pending.reduce((sum, item) => sum + (item.size || 0), 0));
      // "new" is for files added after a run; a reset result is queued again but
      // is not new, so it must read "Slim 1 PDF", not "Slim 1 new PDF".
      const isNew = pending.every((item) => item.status === 'new_queued');
      if (pending.length === 1) {
        label = isNew ? t('footer.slim_new_one', { size }) : t('footer.slim_one', { size });
      } else {
        label = isNew
          ? t('footer.slim_new_many', { count: pending.length, size })
          : t('footer.slim_many', { count: pending.length, size });
      }
    }
    run.textContent = label;
    run.hidden = state.step === 3 && pending.length === 0 && !running;
    run.disabled = running || pending.length === 0;
    stop.hidden = !running;
    newBatch.hidden = state.step !== 3 || running;
    $('#btnOpenOutput').disabled = done.length === 0;

    const outputLabel = $('#outputLabel');
    const config = state.config;
    // In step 3 the destination only matters while new files are pending.
    const showOutput = state.step !== 3 || pending.length > 0;
    if (config.output_mode === 'overwrite') {
      outputLabel.textContent = t('output.overwrite_mode_label');
    } else if (config.output_mode === 'folder') {
      outputLabel.textContent = config.output_dir
        ? t('output.folder_mode_label', { path: shortenPath(config.output_dir) })
        : t('output.default_folder');
    } else {
      outputLabel.textContent = t('output.suffix_mode_label', { suffix: config.output_suffix || '_compressed' });
    }
    $('#suffixWrap').hidden = config.output_mode !== 'suffix';
    $('#btnOutput').hidden = !showOutput;
    $('#dockOutput').hidden = !showOutput;
    renderStepperState();
  }

  function shortenPath(path) {
    const text = String(path || '');
    if (text.length <= 46) return text;
    return `…${text.slice(-44)}`;
  }

  /* ------------------------------------------------------------------ */
  /* Mode card                                                          */
  /* ------------------------------------------------------------------ */

  function renderMode() {
    const config = state.config;
    const preset = config.preset || 'custom';
    $$('#presetGroup [data-preset]').forEach((button) => {
      const active = button.dataset.preset === preset;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
      button.title = t(`mode.tip_${button.dataset.preset}`);
    });
    const description = $('#modeDesc');
    if (preset === 'lossless') description.textContent = t('mode.lossless_desc');
    else if (preset === 'balanced') description.textContent = t('mode.balanced_desc');
    else if (preset === 'max') description.textContent = t('mode.max_desc');
    else description.textContent = t('mode.custom_desc');
    $('#btnAdvanced').setAttribute('aria-expanded', String(!!state.settings.advanced_open));
    $('#btnAdvanced .chev').style.transform = state.settings.advanced_open ? 'rotate(180deg)' : '';
    const advancedLabel = $('#btnAdvanced span');
    if (advancedLabel) {
      advancedLabel.textContent = t(state.settings.advanced_open ? 'mode.advanced_hide' : 'mode.advanced');
    }
    const drawer = $('#advancedDrawer');
    drawer.hidden = !state.settings.advanced_open;
    drawer.classList.toggle('open', !!state.settings.advanced_open);
    syncControls();
  }

  function syncControls() {
    const config = state.config;
    const setRange = (id, value, formatter, out) => {
      const input = $(id);
      if (!input) return;
      input.value = String(value);
      if (out) out.textContent = formatter(value);
    };
    setRange('#dpiRange', config.target_dpi || 150, (v) => `${v} DPI`, $('#dpiValue'));
    if (config.strict_lossless) $('#dpiValue').textContent = t('advanced.dpi_value_lossless');
    setRange('#jpegRange', config.jpeg_quality || 85, (v) => `Q ${v}`, $('#jpegValue'));
    setRange('#zopfliRange', config.zopfli_iterations || 15, (v) => t('advanced.zopfli_value', { passes: v }), $('#zopfliValue'));
    const dpiHint = $('#dpiHint');
    if (dpiHint) {
      dpiHint.textContent = config.strict_lossless
        ? t('advanced.dpi_value_lossless')
        : t('advanced.dpi_hint', { trigger: config.downsample_threshold_dpi || 218 });
    }
    const checks = {
      '#chkPalette': 'quantize_ui_screenshots',
      '#chkSmask': 'strip_opaque_smask',
      '#chkDedup': 'deduplicate_resources',
      '#chkObjStm': 'use_object_streams',
      '#chkFonts': 'subset_fonts',
      '#chkBloat': 'strip_bloat',
      '#chkMeta': 'strip_metadata',
      '#chkFlatten': 'unlock_and_flatten_signatures',
      '#chkSign': 'sign_after_slimming',
    };
    Object.entries(checks).forEach(([selector, key]) => {
      const input = $(selector);
      if (input) input.checked = !!config[key];
    });
    $('#signFields').hidden = !config.sign_after_slimming;
    $('#signingNote').hidden = !config.sign_after_slimming;
    $('#signWarn').hidden = !config.sign_after_slimming && !config.unlock_and_flatten_signatures;
    $('#certPath').value = state.signCertPath || '';
    $('#signReason').value = config.sign_reason || '';
    $('#docPassword').value = state.secrets.docPassword || '';
    $('#suffixInput').value = config.output_suffix || '_compressed';
    renderDock();
  }

  async function updateConfig(partial, { markCustom = true } = {}) {
    const merged = { ...state.config, ...partial };
    if (markCustom && state.config.preset && state.config.preset !== 'custom' && !('preset' in partial)) {
      merged.preset = 'custom';
    }
    state.config = merged;
    syncControls();
    renderDock();
    clearTimeout(state.configTimer);
    state.configTimer = setTimeout(async () => {
      try {
        const body = { ...state.config, sign_pkcs12_path: state.signCertPath || '' };
        const data = await api('/api/config', body);
        state.config = { ...data.config, sign_pkcs12_path: state.signCertPath };
        syncControls();
        renderMode();
      } catch (error) {
        showToast(error.message, 'danger');
      }
    }, 220);
  }

  async function applyPreset(preset) {
    try {
      const data = await api('/api/preset', { preset });
      state.config = { ...data.config, sign_pkcs12_path: state.signCertPath };
      renderMode();
      renderDock();
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  /* ------------------------------------------------------------------ */
  /* Results                                                            */
  /* ------------------------------------------------------------------ */

  function renderResults() {
    const summary = state.run.summary;
    if (!summary) return;
    const failed = summary.failed_count || 0;
    const band = $('#resultsBand');
    band.classList.toggle('warn', failed > 0);
    band.classList.toggle('success', failed === 0);

    $('#bandLabel').textContent = failed > 0 ? t('step3.band_label_warn') : t('step3.band_label');
    $('#bandPercent').textContent = formatPercent(summary.total_reduction_percent || 0);
    $('#bandSaved').textContent = t('step3.band_saved', { saved: formatBytes(summary.total_bytes_saved) });
    $('#bandSub').textContent = t('step3.band_sub', {
      before: formatBytes(summary.total_original_bytes),
      after: formatBytes(summary.total_compressed_bytes),
      files: summary.succeeded_count === 1
        ? t('step3.files_one')
        : t('step3.files_many', { count: summary.succeeded_count }),
    });
    const notes = [];
    if (summary.signed_count) notes.push(t('step3.band_notes_signed'));
    else notes.push(t('step3.band_notes'));
    if (summary.protection_removed_count) {
      notes.push(
        summary.protection_removed_count === 1
          ? t('step3.band_notes_unlocked_one')
          : t('step3.band_notes_unlocked_many', { count: summary.protection_removed_count })
      );
    }
    if (summary.already_optimal_count) {
      notes.push(t('step3.band_notes_optimal', { count: summary.already_optimal_count }));
    }
    if (failed) notes.push(t('step3.band_notes_failed', { count: failed }));
    $('#bandNotes').textContent = notes.join(' · ');

    renderBreakdown();
  }

  function categoryRows(source) {
    const before = source.before || {};
    const after = source.after || {};
    return [
      { key: 'images', before: before.images_bytes || 0, after: after.images_bytes || 0, label: t('step3.cat_images') },
      { key: 'fonts', before: before.fonts_bytes || 0, after: after.fonts_bytes || 0, label: t('step3.cat_fonts') },
      { key: 'vectors', before: before.vectors_bytes || 0, after: after.vectors_bytes || 0, label: t('step3.cat_vectors') },
      { key: 'metadata', before: before.metadata_bytes || 0, after: after.metadata_bytes || 0, label: t('step3.cat_metadata') },
    ];
  }

  function breakdownSource() {
    const summary = state.run.summary;
    if (!summary) return null;
    if (state.selected) {
      const item = state.queue.find((row) => row.id === state.selected && row.status === 'done');
      if (item && item.result) {
        return {
          label: t('step3.breakdown_one', { name: item.name }),
          range: t('step3.breakdown_range', {
            before: formatBytes(item.result.original_bytes),
            after: formatBytes(item.result.compressed_bytes),
          }),
          before: item.result.before_breakdown,
          after: item.result.after_breakdown,
        };
      }
    }
    return {
      label: t('step3.breakdown_agg', { count: summary.succeeded_count }),
      range: t('step3.breakdown_range', {
        before: formatBytes(summary.total_original_bytes),
        after: formatBytes(summary.total_compressed_bytes),
      }),
      before: summary.aggregate_before_breakdown,
      after: summary.aggregate_after_breakdown,
    };
  }

  function renderBreakdown() {
    const source = breakdownSource();
    if (!source) return;
    $('#breakdownSub').textContent = `${source.label} · ${source.range}`;
    $('#btnShowTotal').hidden = !state.selected;
    const rows = $('#breakdownRows');
    rows.replaceChildren();
    categoryRows(source).forEach((category) => {
      const removed = Math.max(0, category.before - category.after);
      const percent = category.before > 0 ? (removed / category.before) * 100 : 0;
      const row = el('div', 'breakdown-row');
      const label = el('div', 'bd-label');
      label.append(el('span', 'bd-name', category.label), el('span', 'bd-pct mono', removed > 0 ? t('step3.removed', { percent: formatPercent(percent) }) : t('step3.nothing_saved')));
      const track = el('div', 'bd-track');
      const fill = el('div', 'bd-fill');
      fill.style.width = `${removed > 0 ? Math.max(4, percent) : 0}%`;
      track.appendChild(fill);
      row.append(label, track, el('span', 'bd-range mono', `${formatBytes(category.before)} → ${formatBytes(category.after)}`));
      rows.appendChild(row);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Queue actions                                                      */
  /* ------------------------------------------------------------------ */

  async function addPaths(paths, password = '') {
    if (!paths || !paths.length) return;
    try {
      const data = await api('/api/queue/add', { paths, password });
      setQueue(data.queue);
      const count = data.added || 0;
      if (count) showToast(t('notices.added', { count }), 'success');
      if (data.skipped && data.skipped.length) {
        const reasons = data.skipped.map((item) => item.reason).filter(Boolean);
        const unique = [...new Set(reasons)];
        showToast(t('notices.skipped', { count: data.skipped.length, reason: unique.join(', ') }), 'warning');
      } else if (!count) {
        showToast(t('notices.added_none'), 'neutral');
      }
      if (state.step === 1) setStep(2);
      setTimeout(refreshQueue, 600);
      setTimeout(refreshQueue, 1800);
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  async function refreshQueue() {
    if (state.run.running) return;
    try {
      const data = await api('/api/run', null, 'GET');
      state.queue = data.queue;
      renderAll();
    } catch (error) {
      /* transient */
    }
  }

  async function removeItem(id) {
    try {
      const data = await api('/api/queue/remove', { id });
      state.selected = state.selected === id ? null : state.selected;
      setQueue(data.queue);
      if (!data.queue.length) setStep(1);
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  async function retryItem(id) {
    try {
      const data = await api('/api/queue/retry', { id });
      if (!data.ok) {
        showToast(t('errors.run_in_progress'), 'warning');
        return false;
      }
      setQueue(data.queue);
      return true;
    } catch (error) {
      showToast(error.message, 'danger');
      return false;
    }
  }

  async function resetItem(id) {
    // A slimmed row goes back to the queue so the same file can be slimmed
    // again (step 2 and the results table share this action cell).
    if (!(await retryItem(id))) return;
    if (insp.id === id && $('#inspectorDialog').open) closeDialog('inspectorDialog');
  }

  async function resetAllItems() {
    try {
      const data = await api('/api/queue/reset', {});
      if (!data.ok) {
        showToast(t('errors.run_in_progress'), 'warning');
        return;
      }
      setQueue(data.queue);
      if (data.reset) showToast(t('notices.reset_all', { count: data.reset }), 'success');
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  /**
   * Put the complete failure text on the clipboard.
   *
   * The status column is width-capped, so a long message is ellipsized and a
   * drag-selection can only reach the visible part. Copying from the queue row
   * uses the stored message, so the whole diagnostic arrives even when the
   * column shows the beginning only.
   */
  async function copyError(id) {
    const item = state.queue.find((entry) => entry.id === id);
    const message = item && item.error ? item.error : '';
    if (!message) return;
    try {
      await navigator.clipboard.writeText(message);
      showToast(t('notices.copied'), 'success');
      return;
    } catch (error) {
      /* Clipboard API can be unavailable; fall through to the legacy path. */
    }
    const area = el('textarea');
    area.value = message;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    let copied = false;
    try {
      copied = document.execCommand('copy');
    } catch (error) {
      copied = false;
    }
    area.remove();
    if (copied) {
      showToast(t('notices.copied'), 'success');
    } else {
      showToast(t('notices.copy_failed'), 'danger');
    }
  }

  async function clearQueue() {
    try {
      const data = await api('/api/queue/clear', {});
      state.selected = null;
      state.run.summary = null;
      setQueue([]);
      setStep(1);
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  /* ------------------------------------------------------------------ */
  /* Run                                                                */
  /* ------------------------------------------------------------------ */

  function serializableConfig() {
    const config = { ...state.config };
    delete config.sign_pkcs12_path;
    return {
      ...config,
      sign_pkcs12_path: state.signCertPath || '',
      sign_pkcs12_password: state.secrets.certPassword || '',
    };
  }

  async function startRun(ids = null) {
    const { pending } = queueCounts();
    const targets = ids || pending.map((item) => item.id);
    if (!targets.length) return;
    if (state.config.output_mode === 'overwrite') {
      openOverwriteConfirm(targets);
      return;
    }
    await doRun(targets);
  }

  async function doRun(targets) {
    try {
      const data = await api('/api/run/start', {
        ids: targets,
        config: serializableConfig(),
        password: state.secrets.docPassword || '',
      });
      state.run = { ...state.run, running: true, completed: 0, total: targets.length };
      renderAll();
      startPolling();
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = setInterval(pollRun, 350);
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function pollRun() {
    try {
      const data = await api('/api/run', null, 'GET');
      const wasRunning = state.run.running;
      state.run = data;
      state.queue = data.queue;
      renderAll();
      const finished = wasRunning && !data.running;
      if (!data.running) {
        stopPolling();
        if (finished) onRunFinished();
      }
    } catch (error) {
      stopPolling();
    }
  }

  function onRunFinished() {
    const summary = state.run.summary;
    if (state.run.cancelled) {
      showToast(t('notices.run_stopped'), 'warning');
      return;
    }
    if (!summary) return;
    if (summary.failed_count > 0) {
      showToast(t('notices.run_failed'), 'danger');
      const failed = state.queue.filter((item) => item.status === 'error');
      const needsPassword = failed.filter(
        (item) => item.error === t('errors.password_required')
      );
      if (needsPassword.length) openPasswordDialog(needsPassword);
      return;
    }
    showToast(t('notices.run_finished'), 'success');
    if (summary.already_optimal_count && summary.already_optimal_count === summary.succeeded_count) {
      showToast(t('notices.optimal'), 'neutral');
    }
    state.selected = null;
    setStep(3);
    renderResults();
  }

  async function stopRun() {
    const button = $('#btnStop');
    button.disabled = true;
    button.querySelector('span').textContent = t('footer.stopping');
    try {
      await api('/api/run/stop', {});
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  /* ------------------------------------------------------------------ */
  /* Output destination                                                 */
  /* ------------------------------------------------------------------ */

  function openOutputDialog() {
    renderOutputDialog();
    openDialog('outputDialog');
  }

  function renderOutputDialog() {
    const mode = state.config.output_mode;
    $$('#outputDialog .output-option').forEach((button) => {
      const active = button.dataset.outputMode === mode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    $('#folderOptionBody').textContent = state.config.output_dir
      ? shortenPath(state.config.output_dir)
      : t('output.folder_body');
  }

  async function chooseOutputMode(mode) {
    if (mode === 'suffix') {
      closeDialog('outputDialog');
      await updateConfig({ output_mode: 'suffix' }, { markCustom: false });
      renderOutputDialog();
      return;
    }
    if (mode === 'overwrite') {
      closeDialog('outputDialog');
      state.config = { ...state.config, output_mode: 'overwrite' };
      renderDock();
      renderOutputDialog();
      await updateConfig({ output_mode: 'overwrite' }, { markCustom: false });
      return;
    }
    if (mode === 'folder') {
      closeDialog('outputDialog');
      openPicker('output');
    }
  }

  async function applyOutputFolder(path) {
    if (!path) return;
    await updateConfig({ output_mode: 'folder', output_dir: path }, { markCustom: false });
    renderOutputDialog();
  }

  /* ------------------------------------------------------------------ */
  /* Overwrite confirmation                                             */
  /* ------------------------------------------------------------------ */

  function openOverwriteConfirm(targets) {
    const body = $('#overwriteBody');
    body.textContent = t('confirm.overwrite_body', { count: targets.length });
    openDialog('overwriteDialog');
    const cancel = $('#overwriteCancel');
    const confirm = $('#overwriteConfirm');
    const cleanup = () => {
      cancel.onclick = null;
      confirm.onclick = null;
    };
    cancel.onclick = () => {
      cleanup();
      closeDialog('overwriteDialog');
    };
    confirm.onclick = async () => {
      cleanup();
      closeDialog('overwriteDialog');
      await doRun(targets);
    };
    cancel.focus();
  }

  /* ------------------------------------------------------------------ */
  /* Password prompt                                                    */
  /* ------------------------------------------------------------------ */

  let passwordTargets = [];

  function openPasswordDialog(items) {
    passwordTargets = items.map((item) => item.id);
    $('#passwordBody').textContent = t('password.body', { name: items[0].name });
    $('#passwordInput').value = '';
    openDialog('passwordDialog');
    $('#passwordInput').focus();
  }

  async function submitPassword() {
    const value = $('#passwordInput').value;
    if (!value) return;
    state.secrets.docPassword = value;
    closeDialog('passwordDialog');
    try {
      for (const id of passwordTargets) {
        await api('/api/queue/retry', { id });
      }
      const data = await api('/api/run', null, 'GET');
      state.queue = data.queue;
      await doRun(passwordTargets);
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  /* ------------------------------------------------------------------ */
  /* File picker                                                        */
  /* ------------------------------------------------------------------ */

  function pickerLabels(mode) {
    if (mode === 'folder') return { title: 'picker.title_folder', hint: 'picker.hint_folder', confirm: 'picker.select_folder' };
    if (mode === 'output') return { title: 'picker.title_output', hint: 'picker.hint_output', confirm: 'picker.select_folder' };
    if (mode === 'cert') return { title: 'picker.title_cert', hint: 'picker.hint_cert', confirm: 'picker.select_cert' };
    return { title: 'picker.title_files', hint: 'picker.hint_files', confirm: 'picker.select_files' };
  }

  async function openPicker(mode) {
    const labels = pickerLabels(mode);
    state.picker.mode = mode;
    state.picker.selected = new Set();
    $('#pickerTitle').textContent = t(labels.title);
    $('#pickerHint').textContent = t(labels.hint);
    $('#pickerConfirm').textContent = t(labels.confirm);
    let start = state.picker.path;
    if (mode === 'output' && state.config.output_dir) start = state.config.output_dir;
    if (mode === 'cert' && state.signCertPath) start = state.signCertPath.replace(/[\\/][^\\/]+$/, '');
    openDialog('pickerDialog');
    await loadPicker(start || '');
  }

  async function loadPicker(path) {
    const kinds = state.picker.mode === 'cert' ? ['cert'] : state.picker.mode === 'output' ? [] : ['pdf'];
    try {
      const data = await api('/api/fs/list', { path: path || null, kinds });
      state.picker.path = data.path;
      state.picker.entries = data.entries;
      state.picker.roots = data.roots;
      state.picker.parent = data.parent;
      renderPicker();
    } catch (error) {
      showToast(error.message, 'danger');
    }
  }

  function renderPicker() {
    $('#pickerPath').textContent = state.picker.path;
    $('#pickerPath').title = state.picker.path;
    $('#pickerUp').disabled = !state.picker.parent;
    const roots = $('#pickerRoots');
    roots.replaceChildren();
    state.picker.roots.forEach((root) => {
      const button = el('button', 'root-btn mono', root.name);
      button.type = 'button';
      button.title = root.path;
      button.addEventListener('click', () => loadPicker(root.path));
      roots.appendChild(button);
    });

    const list = $('#pickerList');
    list.replaceChildren();
    if (!state.picker.entries.length) {
      list.appendChild(el('p', 'picker-empty', t('picker.empty')));
    }
    state.picker.entries.forEach((entry) => {
      const row = el('button', 'picker-row');
      row.type = 'button';
      row.dataset.path = entry.path;
      const selected = state.picker.selected.has(entry.path);
      if (entry.is_dir) {
        row.classList.add('is-dir');
        row.append(icon('folder', 'picker-icon'), el('span', 'picker-name', entry.name), icon('chevron-right', 'picker-go'));
        row.addEventListener('click', () => {
          state.picker.selected.clear();
          loadPicker(entry.path);
        });
      } else {
        row.classList.add('is-file');
        row.classList.toggle('selected', selected);
        row.append(
          icon(state.picker.mode === 'cert' ? 'lock' : 'file', 'picker-icon'),
          el('span', 'picker-name', entry.name),
          el('span', 'picker-size mono', formatBytes(entry.size))
        );
        row.addEventListener('click', (event) => togglePickerSelection(entry.path, event));
      }
      list.appendChild(row);
    });

    const mode = state.picker.mode;
    const confirm = $('#pickerConfirm');
    if (mode === 'folder' || mode === 'output') {
      confirm.disabled = false;
      $('#pickerCount').textContent = state.picker.path;
    } else {
      confirm.disabled = state.picker.selected.size === 0;
      $('#pickerCount').textContent = t('picker.count', { count: state.picker.selected.size });
    }
  }

  function togglePickerSelection(path, event) {
    const selected = state.picker.selected;
    if (event.ctrlKey || event.metaKey) {
      if (selected.has(path)) selected.delete(path);
      else selected.add(path);
    } else if (state.picker.mode === 'cert') {
      selected.clear();
      selected.add(path);
    } else if (selected.size === 1 && selected.has(path)) {
      selected.delete(path);
    } else {
      selected.clear();
      selected.add(path);
    }
    renderPicker();
  }

  async function confirmPicker() {
    const mode = state.picker.mode;
    const selected = [...state.picker.selected];
    closeDialog('pickerDialog');
    if (mode === 'files') {
      await addPaths(selected);
    } else if (mode === 'folder') {
      await addPaths([state.picker.path]);
    } else if (mode === 'output') {
      await applyOutputFolder(state.picker.path);
    } else if (mode === 'cert') {
      state.signCertPath = selected[0] || '';
      $('#certPath').value = state.signCertPath;
      await updateConfig({ sign_pkcs12_path: state.signCertPath, sign_after_slimming: true }, { markCustom: false });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Native pickers (Electron)                                          */
  /* ------------------------------------------------------------------ */

  async function pickNatively(kind) {
    if (!window.ozempdf || !window.ozempdf.pickPaths) return false;
    try {
      const paths = await window.ozempdf.pickPaths(kind);
      if (!paths || !paths.length) return true;
      if (kind === 'cert') {
        state.signCertPath = paths[0];
        $('#certPath').value = state.signCertPath;
        await updateConfig({ sign_pkcs12_path: paths[0], sign_after_slimming: true }, { markCustom: false });
      } else if (kind === 'output') {
        await applyOutputFolder(paths[0]);
      } else {
        await addPaths(paths);
      }
      return true;
    } catch (error) {
      showToast(error.message, 'danger');
      return true;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Inspector                                                          */
  /* ------------------------------------------------------------------ */

  const insp = {
    id: null,
    item: null,
    page: 1,
    total: 1,
    zoom: 200,
    wipe: 50,
    panX: 0,
    panY: 0,
    imgW: 0,
    imgH: 0,
    hasSlim: false,
    drag: null,
  };

  // Pending zoom rasterisation; a wheel gesture coalesces into one render.
  let zoomTimer = null;

  function openInspector(id) {
    const item = state.queue.find((row) => row.id === id);
    if (!item) return;
    if (zoomTimer) {
      clearTimeout(zoomTimer);
      zoomTimer = null;
    }
    insp.id = id;
    insp.item = item;
    insp.page = 1;
    insp.total = Math.max(1, item.pages || 1);
    insp.zoom = 200;
    insp.wipe = 50;
    insp.hasSlim = !!(item.result && item.result.success);
    renderInspectorChrome();
    openDialog('inspectorDialog');
    loadInspectorPage();
  }

  function renderInspectorChrome() {
    const item = insp.item;
    if (!item) return;
    $('#inspectorName').textContent = item.name;
    $('#inspectorBadge').textContent = insp.hasSlim ? t('inspector.badge_compare') : t('inspector.badge_preview');
    if (insp.hasSlim && item.result && item.result.already_optimal) {
      $('#inspectorSub').textContent = t('inspector.identical');
    } else if (insp.hasSlim) {
      $('#inspectorSub').textContent = `${t('inspector.subtitle_compare')} · ${t('inspector.vectors_crisp')}`;
    } else {
      $('#inspectorSub').textContent = t('inspector.subtitle_preview');
    }
    $('#pageInput').value = String(insp.page);
    $('#pageInput').max = String(insp.total);
    $('#pageInput').setAttribute(
      'aria-label',
      t('inspector.page_of', { page: insp.page, total: insp.total })
    );
    $('#pageTotal').textContent = `/ ${insp.total}`;
    // Floating pager: only useful (and only shown) when there is somewhere to go.
    const fabGroup = $('.page-fab');
    if (fabGroup) fabGroup.hidden = insp.total <= 1;
    const fabPrev = $('#fabPrev');
    const fabNext = $('#fabNext');
    if (fabPrev) fabPrev.disabled = insp.page <= 1;
    if (fabNext) fabNext.disabled = insp.page >= insp.total;
    $$('#zoomPills [data-zoom]').forEach((button) => {
      const active = Number(button.dataset.zoom) === insp.zoom;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    $('.wipe-divider').hidden = !insp.hasSlim;
    $('#wipeRange').disabled = !insp.hasSlim;
    $('#inspectorDialog').classList.toggle('preview-only', !insp.hasSlim);
    $('#btnInspectorOpen').querySelector('span').textContent = t('inspector.open_file');
    renderInspectorBadges();
    applyWipe();
  }

  function renderInspectorBadges() {
    const item = insp.item;
    if (!item) return;
    $('#badgeOriginal').textContent = t('inspector.badge_original', { size: formatBytes(item.size) });
    if (insp.hasSlim) {
      const result = item.result;
      $('#badgeSlim').textContent = t('inspector.badge_slim', {
        size: formatBytes(result.compressed_bytes),
        percent: formatPercent(result.reduction_percent || 0),
      });
    } else {
      $('#badgeSlim').textContent = t('inspector.badge_preview', { size: formatBytes(item.size) });
    }
  }

  function inspectorLayer(id, stackId) {
    let layer = document.getElementById(id);
    if (!layer) {
      layer = el('img', 'page-layer');
      layer.id = id;
      layer.alt = '';
      layer.draggable = false;
      document.getElementById(stackId).appendChild(layer);
    }
    return layer;
  }

  function showInspectorNotice(message) {
    const notice = $('#inspectorNotice');
    if (!notice) return;
    notice.textContent = message;
    notice.hidden = false;
  }

  function clearInspectorNotice() {
    const notice = $('#inspectorNotice');
    if (!notice) return;
    notice.hidden = true;
    notice.textContent = '';
  }

  function loadInspectorPage() {
    if (!insp.item) return;
    const base = `/api/page?id=${encodeURIComponent(insp.id)}&page=${insp.page}&zoom=${insp.zoom}`;
    const orig = inspectorLayer('origImg', 'stackOrig');
    const slim = inspectorLayer('slimImg', 'stackSlim');
    clearInspectorNotice();
    orig.onload = () => {
      insp.imgW = orig.naturalWidth;
      insp.imgH = orig.naturalHeight;
      centerInspector();
    };
    // A page the engine cannot rasterise must say so instead of leaving the
    // dark canvas looking broken.
    orig.onerror = () => showInspectorNotice(t('inspector.render_failed'));
    orig.src = `${base}&side=orig&t=${Date.now()}`;
    if (insp.hasSlim) {
      slim.style.visibility = 'visible';
      slim.onerror = () => showInspectorNotice(t('inspector.render_failed'));
      slim.src = `${base}&side=slim&t=${Date.now()}`;
    } else {
      slim.onerror = null;
      slim.removeAttribute('src');
      slim.style.visibility = 'hidden';
    }
    renderInspectorChrome();
  }

  function canvasSize() {
    const canvas = $('#wipeCanvas');
    return { width: canvas.clientWidth, height: canvas.clientHeight };
  }

  function centerInspector() {
    const { width, height } = canvasSize();
    insp.panX = Math.round((width - insp.imgW) / 2);
    insp.panY = Math.round((height - insp.imgH) / 2);
    applyTransform();
  }

  function applyTransform() {
    const transform = `translate(${insp.panX}px, ${insp.panY}px)`;
    $('#stackOrig').style.transform = transform;
    $('#stackSlim').style.transform = transform;
  }

  function applyWipe() {
    const canvas = $('#wipeCanvas');
    canvas.style.setProperty('--canvas-w', `${canvas.clientWidth}px`);
    canvas.style.setProperty('--canvas-h', `${canvas.clientHeight}px`);
    canvas.style.setProperty('--wipe', `${insp.wipe}%`);
    const divider = $('#wipeDivider');
    divider.style.left = `${insp.wipe}%`;
    divider.setAttribute('aria-valuenow', String(Math.round(insp.wipe)));
    divider.setAttribute(
      'aria-valuetext',
      t('inspector.wiped', { percent: Math.round(insp.wipe) })
    );
    $('#wipeRange').value = String(Math.round(insp.wipe));
    $('#wipeReadout').textContent = `${Math.round(insp.wipe)}%`;
    const width = canvas.clientWidth;
    const offset = Math.round((width * insp.wipe) / 100);
    $('#stageSlim').style.left = `${-offset}px`;
  }

  function setInspectorPage(page) {
    insp.page = Math.max(1, Math.min(insp.total, page));
    loadInspectorPage();
  }

  function setInspectorZoom(zoom) {
    insp.zoom = zoom;
    // A wheel gesture produces several steps; only the level the user stops at
    // is rasterised (each step re-renders two pages at the new scale).
    if (zoomTimer) clearTimeout(zoomTimer);
    renderInspectorChrome();
    zoomTimer = setTimeout(() => {
      zoomTimer = null;
      loadInspectorPage();
    }, 140);
  }

  function resetInspector() {
    if (zoomTimer) {
      clearTimeout(zoomTimer);
      zoomTimer = null;
    }
    insp.zoom = 200;
    insp.wipe = 50;
    $('#pageInput').value = String(insp.page);
    loadInspectorPage();
    applyWipe();
  }

  function bindInspector() {
    const canvas = $('#wipeCanvas');
    const dialog = $('#inspectorDialog');
    // A drag that starts inside the modal (page image, corner badge, page number)
    // must never become a drop on the queue behind it: a dropped image url used
    // to be read as a list of paths.
    dialog.addEventListener('dragstart', (event) => event.preventDefault());
    dialog.addEventListener('drop', (event) => {
      event.preventDefault();
      event.stopPropagation();
    });
    // A zoom queued just before closing must not fire into a closed dialog.
    dialog.addEventListener('close', () => {
      if (zoomTimer) {
        clearTimeout(zoomTimer);
        zoomTimer = null;
      }
    });
    canvas.addEventListener('pointerdown', (event) => {
      // The floating pager lives on the canvas; its buttons must keep their
      // clicks (pointer capture would retarget them to the canvas).
      if (event.target.closest('button, a, input, [role="button"]')) return;
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const wipeX = (rect.width * insp.wipe) / 100;
      insp.drag = {
        mode: insp.hasSlim && Math.abs(x - wipeX) < 20 ? 'wipe' : 'pan',
        startX: event.clientX,
        startY: event.clientY,
        panX: insp.panX,
        panY: insp.panY,
      };
      canvas.setPointerCapture(event.pointerId);
      canvas.classList.add(insp.drag.mode === 'wipe' ? 'dragging-wipe' : 'dragging-pan');
    });
    canvas.addEventListener('pointermove', (event) => {
      if (!insp.drag) return;
      if (insp.drag.mode === 'wipe') {
        const rect = canvas.getBoundingClientRect();
        insp.wipe = Math.max(2, Math.min(98, ((event.clientX - rect.left) / rect.width) * 100));
        applyWipe();
      } else {
        insp.panX = insp.drag.panX + (event.clientX - insp.drag.startX);
        insp.panY = insp.drag.panY + (event.clientY - insp.drag.startY);
        applyTransform();
      }
    });
    const endDrag = () => {
      insp.drag = null;
      canvas.classList.remove('dragging-wipe', 'dragging-pan');
    };
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);
    // A pointer released outside the window must not leave the page stuck to
    // the cursor.
    canvas.addEventListener('lostpointercapture', endDrag);
    // Delegated: the wheel zooms anywhere in the dialog, not only when the
    // pointer happens to be over the canvas.
    $('#inspectorDialog').addEventListener('wheel', (event) => {
      if (event.target.closest('input, textarea, select')) return;
      event.preventDefault();
      const steps = [100, 200, 400, 800];
      const index = steps.indexOf(insp.zoom);
      const next = event.deltaY < 0 ? Math.min(steps.length - 1, index + 1) : Math.max(0, index - 1);
      setInspectorZoom(steps[next]);
    }, { passive: false });

    $('#wipeRange').addEventListener('input', (event) => {
      insp.wipe = Number(event.target.value);
      applyWipe();
    });
    $('#wipeDivider').addEventListener('keydown', (event) => {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        insp.wipe = Math.max(2, Math.min(98, insp.wipe + (event.key === 'ArrowLeft' ? -2 : 2)));
        applyWipe();
      }
    });
    $('#pagePrev').addEventListener('click', () => setInspectorPage(insp.page - 1));
    $('#pageNext').addEventListener('click', () => setInspectorPage(insp.page + 1));
    $('#pageInput').addEventListener('change', (event) => setInspectorPage(Number(event.target.value) || 1));
    $('#fabPrev').addEventListener('click', () => setInspectorPage(insp.page - 1));
    $('#fabNext').addEventListener('click', () => setInspectorPage(insp.page + 1));
    // Delegated so the pills keep working even if the header is rebuilt.
    $('#zoomPills').addEventListener('click', (event) => {
      const pill = event.target.closest('[data-zoom]');
      if (pill) setInspectorZoom(Number(pill.dataset.zoom));
    });
    $('#btnInspectorReset').addEventListener('click', resetInspector);
    $('#btnInspectorOpen').addEventListener('click', () => {
      api('/api/open', { kind: 'file', id: insp.id }).catch((error) => showToast(error.message, 'danger'));
    });
    $('#inspectorDialog').addEventListener('keydown', (event) => {
      if (event.target.matches('input')) return;
      if (event.key === 'ArrowLeft') setInspectorPage(insp.page - 1);
      else if (event.key === 'ArrowRight') setInspectorPage(insp.page + 1);
      else if (event.key === '+' || event.key === '=') {
        const steps = [100, 200, 400, 800];
        setInspectorZoom(steps[Math.min(steps.length - 1, steps.indexOf(insp.zoom) + 1)]);
      } else if (event.key === '-') {
        const steps = [100, 200, 400, 800];
        setInspectorZoom(steps[Math.max(0, steps.indexOf(insp.zoom) - 1)]);
      } else if (event.key.toLowerCase() === 'r') resetInspector();
    });
    new ResizeObserver(() => {
      if ($('#inspectorDialog').open) {
        centerInspector();
        applyWipe();
      }
    }).observe(canvas);
  }

  /* ------------------------------------------------------------------ */
  /* Dialogs & toasts                                                   */
  /* ------------------------------------------------------------------ */

  function openDialog(id) {
    const dialog = document.getElementById(id);
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    // Focus the dialog itself so opening does not paint a focus ring on the
    // close button; Tab still reaches every control in DOM order.
    dialog.tabIndex = -1;
    dialog.focus({ preventScroll: true });
  }

  function closeDialog(id) {
    const dialog = document.getElementById(id);
    if (dialog && dialog.open) dialog.close();
  }

  let consentAction = null;

  function openConsent(title, body, onConfirm) {
    $('#consentTitle').textContent = title;
    $('#consentBody').textContent = body;
    consentAction = onConfirm;
    openDialog('consentDialog');
  }

  function showToast(message, kind = 'neutral') {
    const container = $('#toasts');
    const toast = el('div', `toast ${kind}`, message);
    toast.setAttribute('role', 'status');
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('leaving');
      setTimeout(() => toast.remove(), 250);
    }, 6000);
  }

  /* ------------------------------------------------------------------ */
  /* Drag & drop                                                        */
  /* ------------------------------------------------------------------ */

  function bindDragAndDrop() {
    const zones = [$('#dropMat'), $('#queuePanel')];
    let depth = 0;
    const light = (on) => zones.forEach((zone) => zone && zone.classList.toggle('drag-active', on));
    // The inspector is modal: a drag that happens while it is open is a page
    // element or selected text, never a file for the queue.
    const inspectorOpen = () => {
      const dialog = $('#inspectorDialog');
      return Boolean(dialog && dialog.open);
    };
    window.addEventListener('dragenter', (event) => {
      event.preventDefault();
      if (inspectorOpen()) return;
      depth += 1;
      light(true);
    });
    window.addEventListener('dragover', (event) => {
      event.preventDefault();
      if (inspectorOpen()) return;
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    });
    window.addEventListener('dragleave', () => {
      depth = Math.max(0, depth - 1);
      if (!depth) light(false);
    });
    window.addEventListener('drop', async (event) => {
      event.preventDefault();
      depth = 0;
      light(false);
      if (inspectorOpen()) return;
      const types = Array.from((event.dataTransfer && event.dataTransfer.types) || []);
      // Element and link drags (an image carries its own url) must never be read
      // as dropped paths.
      if (!types.includes('Files') && (types.includes('text/uri-list') || types.includes('text/html'))) {
        return;
      }
      const files = Array.from(event.dataTransfer.files || []);
      if (!files.length) {
        const text = event.dataTransfer.getData('text/plain');
        if (text) {
          const paths = text
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(Boolean)
            .filter((line) => !/^[a-z][a-z0-9+.-]*:\/\//i.test(line));
          if (paths.length) await addPaths(paths);
        }
        return;
      }
      if (window.ozempdf && window.ozempdf.getPathForFile) {
        const paths = files
          .map((file) => window.ozempdf.getPathForFile(file))
          .filter(Boolean);
        if (paths.length) await addPaths(paths);
        return;
      }
      await uploadDropped(files);
    });
  }

  async function uploadDropped(files) {
    let count = 0;
    for (const file of files) {
      if (!file.name.toLowerCase().endsWith('.pdf')) continue;
      try {
        const response = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/octet-stream' },
          body: file,
        });
        const payload = await response.json();
        if (!response.ok || payload.ok === false) throw new Error(payload.error || 'upload failed');
        count += 1;
      } catch (error) {
        showToast(t('notices.upload_failed'), 'danger');
      }
    }
    if (count) {
      showToast(t('notices.upload_staged', { name: `${count} file(s)` }), 'warning');
      await refreshQueue();
      if (state.step === 1) setStep(2);
      setTimeout(refreshQueue, 800);
    } else {
      showToast(t('notices.drop_unsupported'), 'warning');
    }
  }

  /* ------------------------------------------------------------------ */
  /* Event wiring                                                       */
  /* ------------------------------------------------------------------ */

  function bindEvents() {
    // Steps
    $$('#stepper .step').forEach((button) => {
      button.addEventListener('click', () => setStep(Number(button.dataset.step)));
    });

    // Theme / language / help
    $('#themeBtn').addEventListener('click', toggleTheme);
    $('#langBtn').addEventListener('click', (event) => {
      event.stopPropagation();
      const menu = $('#langMenu');
      menu.hidden = !menu.hidden;
      $('#langBtn').setAttribute('aria-expanded', String(!menu.hidden));
    });
    document.addEventListener('click', (event) => {
      if (!event.target.closest('.header-controls')) {
        $('#langMenu').hidden = true;
        $('#langBtn').setAttribute('aria-expanded', 'false');
      }
    });
    $('#helpBtn').addEventListener('click', () => {
      $('#helpVersion').textContent = `v${state.version}`;
      openDialog('helpDialog');
    });

    // Add files / folders
    const openFiles = () => pickNatively('files').then((handled) => {
      if (!handled) openPicker('files');
    });
    const openFolder = () => pickNatively('folder').then((handled) => {
      if (!handled) openPicker('folder');
    });
    ['#btnPickFiles', '#btnAddFiles', '#btnResultsAddFiles'].forEach((selector) =>
      $(selector).addEventListener('click', openFiles)
    );
    ['#btnPickFolder', '#btnAddFolder', '#btnResultsAddFolder'].forEach((selector) =>
      $(selector).addEventListener('click', openFolder)
    );
    $('#btnClear').addEventListener('click', clearQueue);
    $('#btnResetAll').addEventListener('click', resetAllItems);

    // Queue delegation
    $('#queueBody').addEventListener('click', (event) => {
      const actionNode = event.target.closest('[data-action]');
      const row = event.target.closest('tr[data-id]');
      if (!row) return;
      const id = row.dataset.id;
      if (actionNode) {
        event.stopPropagation();
        const action = actionNode.dataset.action;
        if (action === 'remove') removeItem(id);
        else if (action === 'retry') retryItem(id);
        else if (action === 'reset') resetItem(id);
        else if (action === 'copy_error') copyError(id);
        else if (action === 'inspect') openInspector(id);
        return;
      }
      const item = state.queue.find((entry) => entry.id === id);
      if (state.step === 3 && item && item.status === 'done') {
        state.selected = state.selected === id ? null : id;
        renderQueue();
        renderBreakdown();
      }
    });
    $('#queueBody').addEventListener('keydown', (event) => {
      const row = event.target.closest('tr[data-id]');
      if (!row) return;
      if (event.key === 'Enter' || event.key === ' ') {
        if (event.target.matches('button')) return;
        const item = state.queue.find((entry) => entry.id === row.dataset.id);
        if (state.step === 3 && item && item.status === 'done') {
          event.preventDefault();
          state.selected = state.selected === row.dataset.id ? null : row.dataset.id;
          renderQueue();
          renderBreakdown();
        }
      }
    });

    // Presets & advanced
    $$('#presetGroup [data-preset]').forEach((button) => {
      button.addEventListener('click', () => applyPreset(button.dataset.preset));
    });
    $('#btnReset').addEventListener('click', () => {
      const preset = state.config.preset === 'custom' ? 'balanced' : state.config.preset || 'balanced';
      applyPreset(preset);
    });
    $('#btnAdvanced').addEventListener('click', async () => {
      state.settings.advanced_open = !state.settings.advanced_open;
      renderMode();
      try {
        await api('/api/ui', { advanced_open: state.settings.advanced_open });
      } catch (error) {
        /* non-fatal */
      }
    });

    // Knobs
    let sliderTimer = null;
    const knob = (selector, key, transform = Number) => {
      const input = $(selector);
      input.addEventListener('input', () => {
        clearTimeout(sliderTimer);
        sliderTimer = setTimeout(() => updateConfig({ [key]: transform(input.value) }), 160);
      });
    };
    knob('#dpiRange', 'target_dpi');
    knob('#jpegRange', 'jpeg_quality');
    knob('#zopfliRange', 'zopfli_iterations');

    const check = (selector, key) => {
      const input = $(selector);
      input.addEventListener('change', () => {
        const partial = { [key]: input.checked };
        if (selector === '#chkSign' && input.checked) {
          partial.sign_after_slimming = true;
        }
        updateConfig(partial);
      });
    };
    check('#chkPalette', 'quantize_ui_screenshots');
    check('#chkSmask', 'strip_opaque_smask');
    check('#chkDedup', 'deduplicate_resources');
    check('#chkObjStm', 'use_object_streams');
    check('#chkFonts', 'subset_fonts');
    check('#chkBloat', 'strip_bloat');
    check('#chkMeta', 'strip_metadata');

    // Removing a signature is irreversible, so it needs explicit consent.
    $('#chkFlatten').addEventListener('change', (event) => {
      if (!event.target.checked) {
        updateConfig({ unlock_and_flatten_signatures: false });
        return;
      }
      openConsent(t('confirm.flatten_title'), t('confirm.flatten_body'), () =>
        updateConfig({ unlock_and_flatten_signatures: true })
      );
    });

    $('#chkSign').addEventListener('change', (event) => {
      if (!event.target.checked) {
        updateConfig({ sign_after_slimming: false });
        return;
      }
      openConsent(t('confirm.sign_title'), t('confirm.sign_body'), async () => {
        if (state.signCertPath) {
          updateConfig({ sign_after_slimming: true });
        } else {
          const handled = await pickNatively('cert');
          if (!handled) openPicker('cert');
        }
      });
    });

    $('#btnPickCert').addEventListener('click', async () => {
      const handled = await pickNatively('cert');
      if (!handled) openPicker('cert');
    });
    $('#certPassword').addEventListener('input', (event) => {
      state.secrets.certPassword = event.target.value;
    });
    $('#signReason').addEventListener('change', (event) => updateConfig({ sign_reason: event.target.value }));
    $('#docPassword').addEventListener('input', (event) => {
      state.secrets.docPassword = event.target.value;
    });

    // Dock
    $('#btnRun').addEventListener('click', () => startRun());
    $('#btnStop').addEventListener('click', stopRun);
    $('#btnOpenOutput').addEventListener('click', () => {
      api('/api/open', { kind: 'folder' }).catch((error) => showToast(error.message, 'danger'));
    });
    $('#btnNewBatch').addEventListener('click', clearQueue);
    $('#btnOutput').addEventListener('click', openOutputDialog);
    $('#suffixInput').addEventListener('change', (event) => {
      const suffix = event.target.value.trim() || '_compressed';
      updateConfig({ output_suffix: suffix }, { markCustom: false });
      event.target.value = suffix;
    });

    // Output dialog
    $$('#outputDialog .output-option').forEach((button) => {
      button.addEventListener('click', () => chooseOutputMode(button.dataset.outputMode));
    });
    $('#btnOutputDone').addEventListener('click', () => closeDialog('outputDialog'));

    // Picker
    $('#pickerUp').addEventListener('click', () => loadPicker(state.picker.parent || ''));
    $('#pickerConfirm').addEventListener('click', confirmPicker);

    // Overwrite & password
    $('#passwordConfirm').addEventListener('click', submitPassword);
    $('#passwordInput').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') submitPassword();
    });
    $('#consentCancel').addEventListener('click', () => {
      consentAction = null;
      closeDialog('consentDialog');
      syncControls();
    });
    $('#consentConfirm').addEventListener('click', async () => {
      const action = consentAction;
      consentAction = null;
      closeDialog('consentDialog');
      if (action) await action();
    });

    // Close-dialog buttons
    $$('[data-close-dialog]').forEach((button) => {
      button.addEventListener('click', () => closeDialog(button.dataset.closeDialog));
    });
    ['pickerDialog', 'consentDialog', 'outputDialog'].forEach((id) => {
      const dialog = document.getElementById(id);
      // A cancelled consent or picker must restore the controls to stored state.
      if (dialog) dialog.addEventListener('close', () => syncControls());
    });

    // Results
    $('#btnShowTotal').addEventListener('click', () => {
      state.selected = null;
      renderQueue();
      renderBreakdown();
    });

    bindDragAndDrop();
    bindInspector();

    // Global shortcuts
    document.addEventListener('keydown', (event) => {
      const modifier = event.ctrlKey || event.metaKey;
      const isField = event.target.matches('input, textarea, select, [contenteditable="true"]');
      if (modifier && event.key.toLowerCase() === 'o') {
        event.preventDefault();
        if (event.shiftKey) openFolder();
        else openFiles();
        return;
      }
      if (modifier && event.key.toLowerCase() === 'i') {
        event.preventDefault();
        const firstDone = state.queue.find((item) => item.status === 'done');
        if (firstDone) openInspector(firstDone.id);
        return;
      }
      if (event.key === 'Enter' && !isField && !event.defaultPrevented) {
        if (event.target.closest('button, a, [role="button"]')) return;
        if (!state.run.running && state.step !== 1) startRun();
      }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Close guard (US-053)                                               */
  /* ------------------------------------------------------------------ */

  window.__ozemCloseGuard = () => {
    const { pending } = queueCounts();
    return {
      running: state.run.running,
      title: t('confirm.quit_title'),
      body: t('confirm.quit_body'),
      detail: t('confirm.quit_detail', { count: pending.length }),
      cancel: t('dialogs.cancel'),
      quit: t('confirm.quit_button'),
    };
  };

  window.__ozemStopAndWait = async () => {
    if (!state.run.running) return true;
    try {
      await api('/api/run/stop', {});
    } catch (error) {
      /* engine may already be gone */
    }
    const deadline = Date.now() + 90000;
    while (state.run.running && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      try {
        const data = await api('/api/run', null, 'GET');
        state.run = data;
        state.queue = data.queue;
        renderAll();
      } catch (error) {
        return false;
      }
    }
    return !state.run.running;
  };

  window.addEventListener('beforeunload', (event) => {
    if (state.run.running) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  /* ------------------------------------------------------------------ */
  /* Start                                                              */
  /* ------------------------------------------------------------------ */

  bindEvents();
  boot();
})();
