/**
 * UI Module
 * Handles all UI interactions and updates for the platform
 * @module UI
 */

const UI = (() => {
    'use strict';
  
    // DOM element cache
    const elements = {};
    let modalOpen = false;
  
    /**
     * Initialize DOM element cache
     * @private
     */
    function cacheElements() {
      const ids = [
        'loadingScreen', 'systemStatus', 'runBtn', 'copyRid', 'addr', 'lat', 'lon',
        'radius', 'rid', 'status', 'statusBadge', 'hint', 'progressBar',
        'mLocation', 'mGround', 'mPercentDelta', 'userAOIRadius',
        'fig1', 'fig2', 'fig3', 'fig4', 'fig3d', 'figRiskMap',
        'satMap', 'snapUser', 'snap500', 'tblSummary', 'narrative',
        'fileList', 'modal', 'modalImg', 'notification', 'notificationIcon', 'notificationText'
      ];
  
      ids.forEach(id => {
        elements[id] = document.getElementById(id);
      });
    }
  
    /**
     * Get cached element or query selector
     * @param {string} selector - Element ID or CSS selector
     * @returns {HTMLElement|null}
     */
    function $(selector) {
      if (selector.startsWith('#')) {
        const id = selector.slice(1);
        return elements[id] || document.getElementById(id);
      }
      return document.querySelector(selector);
    }
  
    /**
     * Query selector all
     * @param {string} selector - CSS selector
     * @returns {NodeList}
     */
    function $$(selector) {
      return document.querySelectorAll(selector);
    }
  
    /**
     * Show notification message
     * @param {string} message - Notification message
     * @param {string} type - Type: 'info', 'success', 'error', 'warning'
     */
    function showNotification(message, type = 'info') {
      const notification = elements.notification || $('#notification');
      const notificationIcon = elements.notificationIcon || $('#notificationIcon');
      const notificationText = elements.notificationText || $('#notificationText');
      
      // Set the message
      notificationText.textContent = message;
      
      // Set the type and icon
      notification.className = 'notification';
      notification.classList.add(type);
      
      notificationIcon.className = 'notification-icon';
      notificationIcon.classList.add(type);
      
      // Set the appropriate icon
      const icons = {
        success: '<i class="fa fa-check-circle"></i>',
        error: '<i class="fa fa-exclamation-circle"></i>',
        warning: '<i class="fa fa-exclamation-triangle"></i>',
        info: '<i class="fa fa-info-circle"></i>'
      };
      notificationIcon.innerHTML = icons[type] || icons.info;
      
      // Show the notification
      notification.classList.add('show');
      
      // Hide after 3 seconds
      setTimeout(() => {
        notification.classList.remove('show');
      }, 3000);
    }
  
    /**
     * Update progress bar
     * @param {number} percentage - Progress percentage (0-100)
     */
    function updateProgress(percentage) {
      const progressBar = elements.progressBar || $('#progressBar');
      progressBar.style.width = percentage + '%';
      
      // Animate glow effect
      if (percentage > 0 && percentage < 100) {
        progressBar.style.boxShadow = '0 0 5px #00e5ff, 0 0 10px #00e5ff';
      } else {
        progressBar.style.boxShadow = 'none';
      }
    }
  
    /**
     * Set status badge
     * @param {string} type - Badge type: 'ok', 'err', 'warn'
     * @param {string} text - Badge text
     * @param {boolean} showLoader - Show loading spinner
     */
    function setBadge(type, text, showLoader = false) {
      const badge = elements.statusBadge || $('#statusBadge');
      badge.className = 'badge ' + type;
      badge.innerHTML = (showLoader ? '<div class="loader"></div>' : '') + text;
    }
  
    /**
     * Update system status indicator
     * @param {string} type - Status type: 'ok', 'warn', 'err'
     * @param {string} text - Status text
     */
    function updateSystemStatus(type, text) {
      const systemStatus = elements.systemStatus || $('#systemStatus');
      const statusIcon = systemStatus.querySelector('.status-icon');
      const statusText = systemStatus.querySelector('.status-text');
      
      systemStatus.className = 'system-status ' + type;
      statusIcon.className = 'status-icon ' + type;
      statusText.className = 'status-text ' + type;
      statusText.textContent = text;
    }
  
    /**
     * Clear UI to initial state
     */
    function clearUI() {
      // Reset metrics
      setText('#mLocation', '--');
      setText('#mGround', '--.--');
      setText('#mPercentDelta', '--');
      setText('#userAOIRadius', '--');
      setText('#narrative', '-- PROCESSING --');
      
      // Clear images
      $$('.gallery img').forEach(img => img.removeAttribute('src'));
      const fig3d = $('#fig3d');
      if (fig3d) fig3d.removeAttribute('src');
      
      // Clear file list
      setHTML('#fileList', '');
      
      // Clear table
      const tbody = $('#tblSummary tbody');
      const thead = $('#tblSummary thead tr');
      if (tbody) tbody.innerHTML = '';
      if (thead) thead.innerHTML = '<th>Summary Multiscale</th>';
      
      // Clear snapshots
      setHTML('#snap500', '');
      setHTML('#snapUser', '');
      
      // Update status
      setText('#status', 'INITIALIZING');
      setText('#hint', 'ESTABLISHING CONNECTION');
      setBadge('warn', 'PROCESSING', true);
    }
  
    /**
     * Set text content of an element
     * @param {string} selector - Element selector
     * @param {string} text - Text content
     */
    function setText(selector, text) {
      const element = $(selector);
      if (element) element.textContent = text;
    }
  
    /**
     * Set HTML content of an element
     * @param {string} selector - Element selector
     * @param {string} html - HTML content
     */
    function setHTML(selector, html) {
      const element = $(selector);
      if (element) element.innerHTML = html;
    }
  
    /**
     * Open image in modal
     * @param {HTMLImageElement} img - Image element to display
     */
    function openImage(img) {
      if (!img || !img.src) return;
      
      const modal = elements.modal || $('#modal');
      const modalImg = elements.modalImg || $('#modalImg');
      
      modalImg.src = img.src;
      modal.classList.add('active');
      modal.setAttribute('aria-hidden', 'false');
      modalOpen = true;
      
      // Add to browser history
      try {
        history.pushState({ modal: true }, '', '#preview');
      } catch (e) {
        console.error('History push failed:', e);
      }
      
      // Add escape key listener
      window.addEventListener('keydown', escapeKeyListener);
    }
  
    /**
     * Close modal
     */
    function closeModal() {
      if (!modalOpen) return;
      
      const modal = elements.modal || $('#modal');
      const modalImg = elements.modalImg || $('#modalImg');
      
      modal.classList.remove('active');
      modal.setAttribute('aria-hidden', 'true');
      modalImg.src = '';
      modalOpen = false;
      
      window.removeEventListener('keydown', escapeKeyListener);
      
      // Handle browser history
      try {
        if (history.state && history.state.modal) {
          history.back();
        }
      } catch (e) {
        console.error('History back failed:', e);
      }
    }
  
    /**
     * Escape key listener for modal
     * @private
     */
    function escapeKeyListener(e) {
      if (e.key === 'Escape') {
        closeModal();
      }
    }
  
    /**
     * Open 3D visualization in fullscreen
     */
    function open3DFullscreen() {
      const iframe = elements.fig3d || $('#fig3d');
      if (!iframe || !iframe.src) return;
      
      const modal = elements.modal || $('#modal');
      const content = modal.querySelector('.modal-content');
      const img = content.querySelector('img');
      
      // Hide image
      img.style.display = 'none';
      
      // Create fullscreen iframe
      const fullscreenIframe = document.createElement('iframe');
      fullscreenIframe.src = iframe.src;
      fullscreenIframe.style.width = '90vw';
      fullscreenIframe.style.height = '85vh';
      fullscreenIframe.style.border = 'none';
      content.appendChild(fullscreenIframe);
      
      // Show modal
      modal.classList.add('active');
      modal.setAttribute('aria-hidden', 'false');
      modalOpen = true;
      
      // Add to browser history
      try {
        history.pushState({ modal: true }, '', '#preview3d');
      } catch (e) {
        console.error('History push failed:', e);
      }
      
      window.addEventListener('keydown', escapeKeyListener);
      
      // Setup close handler
      const closeOnce = () => {
        content.removeChild(fullscreenIframe);
        img.style.display = '';
        closeModal();
        modal.querySelector('.modal-close').removeEventListener('click', closeOnce);
      };
      
      modal.querySelector('.modal-close').addEventListener('click', closeOnce);
    }
  
    /**
     * Render elevation snapshot data
     * @param {string} elementId - Container element ID
     * @param {Object} stats - Statistics object
     */
    function renderSnapshot(elementId, stats) {
      const element = $(elementId);
      if (!element) return;
      
      const html = `
        <div class="snapshot-chip">
          <div class="t">House</div>
          <div class="v">${stats.house ?? '--'}</div>
        </div>
        <div class="snapshot-chip">
          <div class="t">Min</div>
          <div class="v">${stats.min ?? '--'}</div>
        </div>
        <div class="snapshot-chip">
          <div class="t">Median</div>
          <div class="v">${stats.median ?? '--'}</div>
        </div>
        <div class="snapshot-chip">
          <div class="t">Max</div>
          <div class="v">${stats.max ?? '--'}</div>
        </div>
        <div class="snapshot-chip">
          <div class="t">Percentile</div>
          <div class="v">${stats.pct ?? '--'}</div>
        </div>
      `;
      
      element.innerHTML = html;
    }
  
    /**
     * Populate table from CSV data
     * @param {string} csv - CSV data string
     * @param {HTMLTableElement} table - Table element
     */
    function csvToTable(csv, table) {
      const lines = csv.trim().split('\n');
      if (!lines.length) return;
      
      const headers = lines[0].split(',').map(h => h.trim());
      const thead = table.querySelector('thead tr');
      thead.innerHTML = headers.map(h => `<th>${h}</th>`).join('');
      
      const tbody = table.querySelector('tbody');
      tbody.innerHTML = '';
      
      for (let i = 1; i < lines.length; i++) {
        const cells = lines[i].split(',').map(c => c.trim());
        const tr = document.createElement('tr');
        tr.innerHTML = cells.map(c => `<td>${c}</td>`).join('');
        tbody.appendChild(tr);
      }
    }
  
    /**
     * Clean address string for display
     * @param {string} raw - Raw address string
     * @returns {string} Cleaned address
     */
    function cleanAddress(raw) {
      if (!raw) return raw;
      let s = String(raw);
  
      // Take first part before slash
      s = s.split(' / ')[0].split('/')[0];
  
      // Remove non-Latin characters
      s = s.replace(/[\u3400-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]+/g, '');
  
      // Clean up formatting
      s = s.replace(/[\/]+/g, ' ');
      s = s.replace(/\s*,\s*/g, ', ');
      s = s.replace(/,{2,}/g, ', ');
      s = s.replace(/\s{2,}/g, ' ').trim();
  
      return s || String(raw).trim();
    }
  
    /**
     * Extract first sentence from text
     * @param {string} text - Full text
     * @returns {string} First sentence
     */
    function extractFirstSentence(text) {
      if (!text) return '';
      const s = text.trim();
      const stops = ['. ', '! ', '? '];
      let cut = -1;
      
      for (const stop of stops) {
        const i = s.indexOf(stop);
        if (i !== -1) {
          cut = (cut === -1) ? i + 2 : Math.min(cut, i + 2);
        }
      }
      
      return (cut === -1) ? s : s.slice(0, cut);
    }
  
    /**
     * Format user-facing error message
     * @param {Object} err - Error object
     * @returns {Object} Formatted error details
     */
    function formatUserFacingError(err) {
      const code = err.error_code || '';
      const raw = (err.message || '').toString();
  
      if (code === 'AOI_OUTSIDE_EPT_BOUNDS') {
        const maxR = Number(err.max_radius_m);
        const options = [50, 100, 200, 300, 500];
        const safe = options.filter(v => Number.isFinite(maxR) ? v <= maxR : true);
        const suggested = safe.length ? safe[safe.length - 1] : 50;
        const maxText = Number.isFinite(maxR) ? `${Math.floor(maxR)} m` : 'dataset bounds';
        
        return {
          title: 'AOI OUT OF GRID',
          hint: `AOI exceeds dataset extent. Max ≈ ${maxText}. Try ${suggested} m or smaller.`,
          toast: `AOI out of grid · Max ≈ ${maxText} · Suggest ${suggested} m`
        };
      }
  
      const clean = raw
        .replace(/pdal|writers\.gdal|Traceback[\s\S]*/gi, '')
        .replace(/\s+/g, ' ')
        .trim();
  
      return {
        title: 'ANALYSIS FAILED',
        hint: clean || 'An unexpected error occurred. Please try again.',
        toast: clean || 'Analysis failed.'
      };
    }
  
    /**
     * Handle out-of-grid error
     * @param {Object} runStatus - Run status object with error
     */
    function handleOutOfGridError(runStatus) {
      const err = runStatus && runStatus.error ? runStatus.error : {};
      const maxR = Number(err.max_radius_m);
      const curR = Number($('#radius').value);
  
      const options = [50, 100, 200, 300, 500];
      const safe = options.filter(v => Number.isFinite(maxR) ? v <= maxR : true);
      const suggested = safe.length ? safe[safe.length - 1] : 50;
  
      setBadge('err', 'OUT OF GRID');
      updateSystemStatus('warn', 'ADJUST RADIUS');
      setText('#status', 'ERROR');
      setText('#hint', 'AOI exceeds LiDAR dataset extent. Please reduce radius.');
  
      // Highlight radius selector
      const radiusSel = $('#radius');
      radiusSel.classList.add('ring-2', 'ring-offset-2');
      radiusSel.style.boxShadow = '0 0 12px rgba(255,214,10,.6)';
      
      setTimeout(() => {
        radiusSel.classList.remove('ring-2', 'ring-offset-2');
        radiusSel.style.boxShadow = '';
      }, 1800);
  
      const maxText = Number.isFinite(maxR) ? `${Math.floor(maxR)} m` : 'dataset bounds';
      const curText = Number.isFinite(curR) ? `${curR} m` : '--';
      const suggestText = Number.isFinite(maxR) ? `${suggested} m` : 'smaller value';
  
      showNotification(
        `AOI OUT OF GRID\nCurrent: ${curText}\nMax allowed: ${maxText}\nTry: ${suggestText}`,
        'warning'
      );
  
      if (Number.isFinite(maxR)) {
        $('#radius').value = String(suggested);
      }
  
      setText('#narrative', 
        `⚠ The requested analysis radius (${curText}) exceeds the LiDAR dataset extent (max ≈ ${maxText}). ` +
        `Please reduce the radius (e.g., ${suggestText}) and run again.`
      );
    }
  
    /**
     * Initialize UI module
     */
    function init() {
      cacheElements();
      
      // Setup modal click-outside-to-close
      const modal = elements.modal || $('#modal');
      if (modal) {
        modal.addEventListener('click', (e) => {
          if (e.target === modal) {
            closeModal();
          }
        });
      }
      
      // Setup popstate listener for modal
      window.addEventListener('popstate', () => {
        if (modalOpen) {
          const modal = elements.modal || $('#modal');
          const modalImg = elements.modalImg || $('#modalImg');
          
          modal.classList.remove('active');
          modal.setAttribute('aria-hidden', 'true');
          modalImg.src = '';
          modalOpen = false;
          
          window.removeEventListener('keydown', escapeKeyListener);
        }
      });
      
      console.log('UI module initialized');
    }
  
    // Public API
    return {
      $,
      $$,
      init,
      showNotification,
      updateProgress,
      setBadge,
      updateSystemStatus,
      clearUI,
      setText,
      setHTML,
      openImage,
      closeModal,
      open3DFullscreen,
      renderSnapshot,
      csvToTable,
      cleanAddress,
      extractFirstSentence,
      formatUserFacingError,
      handleOutOfGridError
    };
  })();
  
  // Export for use in other modules
  window.UI = UI;