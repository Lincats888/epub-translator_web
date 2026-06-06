/**
 * Shared Settings Modal — works on any page.
 * Include via <script src="/settings-modal.js"></script>
 * Then use onclick="openSettings()" on any settings icon/button.
 *
 * Requires: fetch, localStorage
 * Optional: page-level `lang` variable for i18n (falls back to localStorage 'epub-lang')
 */

(function() {
  'use strict';

  // ── i18n ───────────────────────────────────────────────────────────
  const i18nDict = {
    zh: {
      settingsTitle: '设置',
      settingsNavGeneral: '通用',
      settingsNavTranslate: '翻译 API',
      settingsNavOcr: 'OCR 设置',
      generalMaxFileSize: '最大文件大小 (MB)',
      generalMaxFileSizeDesc: '适用于所有翻译页面的上传限制',
      generalMaxConcurrent: '最大并发翻译数',
      generalMaxConcurrentDesc: '仅适用于批量翻译页面 (1-5)',
      apiKeyLabel: 'API Key',
      apiBaseLabel: 'API 地址',
      modelLabel: '模型',
      modeLabel: '翻译模式',
      modeBilingual: '双语（原文 + 译文）',
      modeChineseOnly: '目标语言（只保留译文）',
      ocrEnabled: '启用 OCR',
      ocrApiKeyLabel: 'OCR API Key',
      ocrApiBaseLabel: 'OCR API 地址',
      ocrModelLabel: 'OCR 模型',
      encrypted: '已加密存储',
      apiKeyWarning: '请先输入 API Key 才能使用翻译功能',
      cancel: '取消',
      save: '保存',
      apiKeyHint: '未配置',
      apiKeyHintSet: '当前：',
      applyKey: '申请 API Key',
      saving: '保存中...',
      saved: '已保存！',
    },
    en: {
      settingsTitle: 'Settings',
      settingsNavGeneral: 'General',
      settingsNavTranslate: 'Translation API',
      settingsNavOcr: 'OCR Settings',
      generalMaxFileSize: 'Max File Size (MB)',
      generalMaxFileSizeDesc: 'Upload limit for all translation pages',
      generalMaxConcurrent: 'Max Concurrent Tasks',
      generalMaxConcurrentDesc: 'Batch translation only (1-5)',
      apiKeyLabel: 'API Key',
      apiBaseLabel: 'API Base URL',
      modelLabel: 'Model',
      modeLabel: 'Mode',
      modeBilingual: 'Bilingual',
      modeChineseOnly: 'Target Only',
      ocrEnabled: 'Enable OCR',
      ocrApiKeyLabel: 'OCR API Key',
      ocrApiBaseLabel: 'OCR API URL',
      ocrModelLabel: 'OCR Model',
      encrypted: 'Encrypted',
      apiKeyWarning: 'Please enter your API Key',
      cancel: 'Cancel',
      save: 'Save',
      apiKeyHint: 'Not configured',
      apiKeyHintSet: 'Current: ',
      applyKey: 'Apply for API Key',
      saving: 'Saving...',
      saved: 'Saved!',
    }
  };

  function getLang() {
    // Try page-level `lang` variable first, then localStorage
    if (typeof window.lang === 'string' && window.lang) return window.lang;
    try { var l = localStorage.getItem('epub-lang'); if (l) return l; } catch(e) {}
    return 'zh';
  }

  function t(key) {
    var l = getLang();
    var dict = i18nDict[l] || i18nDict.zh;
    return dict[key] || key;
  }

  // ── CSS Injection ──────────────────────────────────────────────────
  var cssInjected = false;
  function injectCSS() {
    if (cssInjected) return;
    cssInjected = true;
    var style = document.createElement('style');
    style.id = 'settings-modal-css';
    style.textContent = ''
      + '.settings-overlay{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.35);backdrop-filter:blur(4px);'
      + 'display:none;align-items:center;justify-content:center;}'
      + '.settings-overlay.active{display:flex;}'
      + '.settings-dialog{background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,0.15);'
      + 'width:100%;max-width:620px;margin:16px;overflow:hidden;display:flex;flex-direction:column;max-height:80vh;'
      + 'font-family:Inter,-apple-system,Segoe UI,sans-serif;color:#2D2D2D;}'
      + '.settings-header{padding:20px 24px 16px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #EDE8E2;}'
      + '.settings-header h3{font-family:"Noto Serif SC",Georgia,serif;font-size:18px;font-weight:600;flex:1;margin:0;}'
      + '.settings-close{width:32px;height:32px;border:none;background:none;cursor:pointer;border-radius:8px;'
      + 'display:flex;align-items:center;justify-content:center;color:#8A8A8A;}'
      + '.settings-close:hover{background:#F5EDE6;}'
      + '.settings-body{display:flex;flex:1;overflow:hidden;}'
      + '.settings-sidebar{width:140px;border-right:1px solid #EDE8E2;padding:8px;flex-shrink:0;background:#FCF9F5;}'
      + '.settings-nav-item{padding:10px 14px;border-radius:8px;cursor:pointer;font-size:13px;color:#8A8A8A;'
      + 'transition:all 0.15s;margin-bottom:2px;font-weight:500;}'
      + '.settings-nav-item:hover{background:#F5EDE6;color:#2D2D2D;}'
      + '.settings-nav-item.active{background:#F5EDE6;color:#C8956C;font-weight:600;}'
      + '.settings-content{flex:1;padding:20px 24px;overflow-y:auto;}'
      + '.settings-section{display:none;}'
      + '.settings-section.active{display:block;}'
      + '.settings-field{margin-bottom:14px;}'
      + '.settings-field label{display:block;font-size:12px;font-weight:500;margin-bottom:4px;color:#8A8A8A;}'
      + '.settings-field input,.settings-field select{width:100%;padding:8px 12px;border:1px solid #EDE8E2;border-radius:8px;'
      + 'font-family:Inter,-apple-system,Segoe UI,sans-serif;font-size:13px;background:#FCF9F5;color:#2D2D2D;'
      + 'outline:none;transition:border-color 0.2s;box-sizing:border-box;}'
      + '.settings-field input:focus,.settings-field select:focus{border-color:#C8956C;}'
      + '.settings-badge{display:none;margin-left:8px;font-size:10px;font-weight:600;color:#5DAA7E;'
      + 'background:#E8F5EE;padding:2px 8px;border-radius:6px;}'
      + '.settings-warn{padding:10px 14px;margin-bottom:14px;background:#FDECEC;border:1px solid #F5C6C6;'
      + 'border-radius:8px;font-size:12px;color:#B83232;display:none;align-items:center;gap:6px;}'
      + '.settings-warn.show{display:flex;}'
      + '.settings-footer{padding:14px 24px;border-top:1px solid #EDE8E2;display:flex;justify-content:flex-end;gap:8px;}'
      + '.settings-btn{font-size:13px;font-weight:500;padding:8px 18px;border-radius:10px;border:none;cursor:pointer;'
      + 'font-family:Inter,-apple-system,Segoe UI,sans-serif;transition:all 0.15s;}'
      + '.settings-btn-ghost{background:transparent;color:#8A8A8A;border:1px solid #EDE8E2;}'
      + '.settings-btn-ghost:hover{background:#FCF9F5;color:#2D2D2D;}'
      + '.settings-btn-primary{background:#C8956C;color:#fff;box-shadow:0 2px 8px rgba(200,149,108,0.3);}'
      + '.settings-btn-primary:hover{background:#B8845B;}'
      + '.settings-btn:disabled{opacity:0.5;cursor:not-allowed;}';
    document.head.appendChild(style);
  }

  // ── DOM Creation ───────────────────────────────────────────────────
  var overlay = null;

  function ensureDOM() {
    if (overlay) return;
    injectCSS();

    overlay = document.createElement('div');
    overlay.className = 'settings-overlay';
    overlay.id = 'settings-overlay';
    overlay.innerHTML = ''
      + '<div class="settings-dialog">'
      + '  <div class="settings-header">'
      + '    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#C8956C" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>'
      + '    <h3 class="settings-title-text">' + t('settingsTitle') + '</h3>'
      + '    <button class="settings-close" onclick="closeSettings()">'
      + '      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>'
      + '    </button>'
      + '  </div>'
      + '  <div class="settings-body">'
      + '    <div class="settings-sidebar">'
      + '      <div class="settings-nav-item active" data-section="general" onclick="switchSettingsSection(\'general\')">'
      + '        <span>' + t('settingsNavGeneral') + '</span>'
      + '      </div>'
      + '      <div class="settings-nav-item" data-section="translate" onclick="switchSettingsSection(\'translate\')">'
      + '        <span>' + t('settingsNavTranslate') + '</span>'
      + '      </div>'
      + '      <div class="settings-nav-item" data-section="ocr" onclick="switchSettingsSection(\'ocr\')">'
      + '        <span>' + t('settingsNavOcr') + '</span>'
      + '      </div>'
      + '    </div>'
      + '    <div class="settings-content">'
      + '      <div class="settings-section active" id="sec-general">'
      + '        <div class="settings-field">'
      + '          <label><span>' + t('generalMaxFileSize') + '</span></label>'
      + '          <input id="cfg-max-file-size" type="number" min="1" max="2000" placeholder="500">'
      + '          <div style="font-size:11px;color:#8A8A8A;margin-top:2px;">' + t('generalMaxFileSizeDesc') + '</div>'
      + '        </div>'
      + '        <div class="settings-field">'
      + '          <label><span>' + t('generalMaxConcurrent') + '</span></label>'
      + '          <input id="cfg-max-concurrent" type="number" min="1" max="5" placeholder="1">'
      + '          <div style="font-size:11px;color:#8A8A8A;margin-top:2px;">' + t('generalMaxConcurrentDesc') + '</div>'
      + '        </div>'
      + '      </div>'
      + '      <div class="settings-section" id="sec-translate">'
      + '        <div class="settings-warn" id="api-key-warning">'
      + '          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
      + '          <span class="warn-text">' + t('apiKeyWarning') + '</span>'
      + '        </div>'
      + '        <div class="settings-field">'
      + '          <label>'
      + '            <span>' + t('apiKeyLabel') + '</span>'
      + '            <span class="settings-badge" id="cfg-encrypted-badge">'
      + '              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:-1px;margin-right:2px;"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>'
      + '              <span>' + t('encrypted') + '</span>'
      + '            </span>'
      + '          </label>'
      + '          <input id="cfg-key" type="password" placeholder="sk-xxxxxxxxxxxx" oninput="checkApiKeyInput()">'
      + '          <div style="font-size:11px;color:#8A8A8A;margin-top:2px;" id="cfg-key-hint"></div>'
      + '        </div>'
      + '        <div class="settings-field">'
      + '          <label>' + t('apiBaseLabel') + '</label>'
      + '          <input id="cfg-base" type="text" placeholder="https://api.deepseek.com">'
      + '        </div>'
      + '        <div class="settings-field">'
      + '          <label>' + t('modelLabel') + '</label>'
      + '          <input id="cfg-model" type="text" placeholder="deepseek-chat">'
      + '        </div>'
      + '        <div class="settings-field">'
      + '          <label>' + t('modeLabel') + '</label>'
      + '          <select id="cfg-mode">'
      + '            <option value="bilingual">' + t('modeBilingual') + '</option>'
      + '            <option value="chinese_only">' + t('modeChineseOnly') + '</option>'
      + '          </select>'
      + '        </div>'
      + '      </div>'
      + '      <div class="settings-section" id="sec-ocr">'
      + '        <div class="settings-field">'
      + '          <label>' + t('ocrEnabled') + '</label>'
      + '          <select id="cfg-ocr-enabled">'
      + '            <option value="true">On</option>'
      + '            <option value="false">Off</option>'
      + '          </select>'
      + '        </div>'
      + '        <div class="settings-field">'
      + '          <label>'
      + '            <span>' + t('ocrApiKeyLabel') + '</span>'
      + '            <span class="settings-badge" id="cfg-ocr-encrypted-badge">'
      + '              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:-1px;margin-right:2px;"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>'
      + '              <span>' + t('encrypted') + '</span>'
      + '            </span>'
      + '          </label>'
      + '          <input id="cfg-ocr-key" type="password" placeholder="sk-xxxxxxxxxxxx">'
      + '        </div>'
      + '        <div class="settings-field">'
      + '          <label>' + t('ocrApiBaseLabel') + '</label>'
      + '          <input id="cfg-ocr-base" type="text" placeholder="https://api.siliconflow.com">'
      + '        </div>'
      + '        <div class="settings-field">'
      + '          <label>' + t('ocrModelLabel') + '</label>'
      + '          <input id="cfg-ocr-model" type="text" placeholder="Qwen/Qwen3-VL-32B-Instruct">'
      + '        </div>'
      + '      </div>'
      + '    </div>'
      + '  </div>'
      + '  <div class="settings-footer">'
      + '    <button class="settings-btn settings-btn-ghost" onclick="closeSettings()">' + t('cancel') + '</button>'
      + '    <button class="settings-btn settings-btn-primary" id="settings-save-btn" onclick="saveSettings()">' + t('save') + '</button>'
      + '  </div>'
      + '</div>';

    document.body.appendChild(overlay);

    // Close on overlay click (outside dialog)
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay) closeSettings();
    });
  }

  // ── Public API ─────────────────────────────────────────────────────

  window.switchSettingsSection = function(name) {
    var navItems = document.querySelectorAll('#settings-overlay .settings-nav-item');
    for (var i = 0; i < navItems.length; i++) {
      navItems[i].classList.toggle('active', navItems[i].dataset.section === name);
    }
    var sections = document.querySelectorAll('#settings-overlay .settings-section');
    for (var j = 0; j < sections.length; j++) {
      sections[j].classList.toggle('active', sections[j].id === 'sec-' + name);
    }
  };

  window.checkApiKeyInput = function() {
    var input = document.getElementById('cfg-key');
    var warn = document.getElementById('api-key-warning');
    if (input && warn) {
      warn.classList.toggle('show', !input.value.trim());
    }
  };

  window.openSettings = function() {
    ensureDOM();
    // Refresh i18n labels
    var titleEl = overlay.querySelector('.settings-title-text');
    if (titleEl) titleEl.textContent = t('settingsTitle');
    var warnEl = overlay.querySelector('.warn-text');
    if (warnEl) warnEl.textContent = t('apiKeyWarning');

    fetch('/api/config').then(function(r) { return r.json(); }).then(function(c) {
      document.getElementById('cfg-key').value = '';
      document.getElementById('cfg-base').value = c.api_base || '';
      document.getElementById('cfg-model').value = c.model || '';
      document.getElementById('cfg-mode').value = c.translation_mode || 'bilingual';
      document.getElementById('cfg-key-hint').innerHTML = c.api_key_set
        ? t('apiKeyHintSet') + c.api_key_masked
        : t('apiKeyHint') + ' — <a href="https://platform.deepseek.com/api_keys" target="_blank" style="color:#C8956C;text-decoration:underline;">' + t('applyKey') + '</a>';
      var badge = document.getElementById('cfg-encrypted-badge');
      badge.style.display = (c.api_key_set && c.api_key_encrypted) ? 'inline-block' : 'none';
      var warn = document.getElementById('api-key-warning');
      warn.classList.toggle('show', !c.api_key_set);

      // General
      document.getElementById('cfg-max-file-size').value = c.max_file_size_mb || 500;
      document.getElementById('cfg-max-concurrent').value = c.max_concurrent_tasks || 1;

      // OCR
      document.getElementById('cfg-ocr-enabled').value = c.ocr_enabled ? 'true' : 'false';
      document.getElementById('cfg-ocr-key').value = '';
      document.getElementById('cfg-ocr-base').value = c.ocr_api_base || '';
      document.getElementById('cfg-ocr-model').value = c.ocr_model || '';
      var ocrBadge = document.getElementById('cfg-ocr-encrypted-badge');
      ocrBadge.style.display = (c.ocr_api_key_set && c.ocr_api_key_encrypted) ? 'inline-block' : 'none';

      overlay.classList.add('active');
    }).catch(function() {});
  };

  window.closeSettings = function() {
    if (overlay) overlay.classList.remove('active');
  };

  window.saveSettings = function() {
    var body = {
      api_base: document.getElementById('cfg-base').value,
      model: document.getElementById('cfg-model').value,
      translation_mode: document.getElementById('cfg-mode').value,
      ocr_enabled: document.getElementById('cfg-ocr-enabled').value === 'true',
      ocr_api_base: document.getElementById('cfg-ocr-base').value,
      ocr_model: document.getElementById('cfg-ocr-model').value,
      max_file_size_mb: parseInt(document.getElementById('cfg-max-file-size').value) || 500,
      max_concurrent_tasks: parseInt(document.getElementById('cfg-max-concurrent').value) || 1,
    };
    var k = document.getElementById('cfg-key').value.trim();
    if (k) body.api_key = k;
    var ocrK = document.getElementById('cfg-ocr-key').value.trim();
    if (ocrK) body.ocr_api_key = ocrK;

    var btn = document.getElementById('settings-save-btn');
    btn.textContent = t('saving');
    btn.disabled = true;

    fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function(r) { return r.json(); }).then(function() {
      btn.textContent = t('saved');
      setTimeout(function() { closeSettings(); btn.textContent = t('save'); btn.disabled = false; }, 500);
    }).catch(function() {
      btn.textContent = t('save');
      btn.disabled = false;
    });
  };

  // ── Auto-open support (for index.html compat) ──────────────────────
  if (window.location.search.includes('settings=open')) {
    setTimeout(window.openSettings, 300);
  }
})();
