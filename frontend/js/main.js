/**
 * Main Application Controller
 * Tokio Marine Elevation Risk Analysis Platform
 * @module Main
 */

(function() {
  'use strict';

  // ---------------------------------------------------------------------------
  // Global configuration
  // ---------------------------------------------------------------------------
  const CONFIG = {
    DEBUG: false,
    AUTO_SAVE_RUN_ID: true,
    ANIMATION_ENABLED: true
  };

  // Application state
  let currentRunId = null;
  let isAnalyzing = false;
  let pollingHandle = null;

  // ---------------------------------------------------------------------------
  // Helper: parse area-level CSV to a small stats object
  // ---------------------------------------------------------------------------
  /**
   * Parse area CSV to statistics object
   * @param {string} csv - CSV content
   * @returns {Object} Statistics object
   */
  function parseAreaCSVToStats(csv) {
    const lines = csv.trim().split('\n');
    if (lines.length < 2) return {};

    const idxValue = 1;
    const pick = (keyword) => {
      const row = lines.find(l => l.toLowerCase().includes(keyword));
      if (!row) return null;
      const arr = row.split(',');
      return (arr[idxValue] || '').trim();
    };

    return {
      house: pick('house elevation'),
      min: pick('lowest'),
      max: pick('highest'),
      median: pick('median'),
      pct: pick('percentile')
    };
  }

  // ---------------------------------------------------------------------------
  // Helper: find file name by regex patterns
  // ---------------------------------------------------------------------------
  /**
   * Find file by regex patterns
   * @param {Array<string>} files - List of filenames (or relative paths)
   * @param {Array<RegExp>} patterns - Regex patterns to match
   * @returns {string|null} Matched filename or null
   */
  function findFileByRegex(files, patterns) {
    for (const pattern of patterns) {
      const file = files.find(x => pattern.test(x));
      if (file) return file;
    }
    return null;
  }

  // ---------------------------------------------------------------------------
  // Helper: Terrain Risk Score sentence injection into narrative
  // ---------------------------------------------------------------------------
  /**
   * Build a canonical TRS sentence from manifest fields.
   * Uses terrain_risk_score and terrain_risk_percentile from manifest.
   * Returns null if fields are missing or not finite (for backward compatibility).
   *
   * @param {Object} manifest
   * @returns {string|null}
   */
  function buildTRSSentence(manifest) {
    const score = Number(manifest.terrain_risk_score);
    const pct = Number(manifest.terrain_risk_percentile);

    // Backward compatibility: if TRS fields are missing, do not touch narrative
    if (!Number.isFinite(score) || !Number.isFinite(pct)) {
      return null;
    }

    const scoreTxt = score.toFixed(3);
    const pctTxt = pct.toFixed(1);
    const betterShare = (100 - pct).toFixed(1);

    return (
      `The terrain risk model estimates a terrain risk score of ${scoreTxt} for this location, ` +
      `which ranks at the ${pctTxt}th percentile across the 500 m terrain risk map, ` +
      `meaning it is higher and less flood-prone than about ${betterShare}% of the surrounding area.`
    );
  }

  /**
   * Patch narrative text by overwriting the TRS sentence using values from manifest.
   * - If a TRS sentence already exists (matching the regex), it will be replaced.
   * - If no such sentence exists and TRS is available, we prepend the new sentence.
   * - If TRS is not available, the original narrative is returned unchanged.
   *
   * @param {string} narrativeRaw - Original narrative text
   * @param {Object} manifest - Manifest with TRS fields
   * @returns {string} Final narrative text
   */
  function patchNarrativeWithTRS(narrativeRaw, manifest) {
    const trsSentence = buildTRSSentence(manifest);
    const original = narrativeRaw || '';

    // No TRS data in manifest → return original narrative
    if (!trsSentence) {
      return original;
    }

    // Regex to match the old TRS sentence (from "The terrain risk model..."
    // up to "DEM region."). This lets us overwrite just that sentence.
    const pattern =
      /The terrain risk model estimates a terrain risk score[\s\S]*?DEM region\./;

    if (pattern.test(original)) {
      // Existing TRS sentence found → replace with updated sentence
      return original.replace(pattern, trsSentence);
    }

    // If there is no TRS sentence in the narrative:
    // - If narrative is empty, just return the TRS sentence
    // - Otherwise, prepend TRS sentence at the top
    if (!original.trim()) {
      return trsSentence;
    }
    return `${trsSentence}\n\n${original}`;
  }

  // ---------------------------------------------------------------------------
  // Load and display analysis outputs
  // ---------------------------------------------------------------------------
  /**
   * Load and display analysis outputs
   * @param {string} runId - The run identifier
   */
  async function loadOutputs(runId) {
    try {
      // Get manifest data
      const manifest = await API.getManifest(runId);
      const files = manifest.files || {};
      const aoiR = Number(manifest.aoi_radius_m);
      const userR = Number.isFinite(aoiR) ? Math.round(aoiR) : 100;

      // ------------------------------
      // Location / address UI binding
      // ------------------------------
      const manifestAddr = manifest.address && String(manifest.address).trim();
      const addrInput = UI.$('#addr').value.trim();
      const address =
        manifestAddr ||
        addrInput ||
        ((Number.isFinite(manifest.lat) && Number.isFinite(manifest.lon))
          ? `${Number(manifest.lat).toFixed(6)}, ${Number(manifest.lon).toFixed(6)}`
          : '--');

      const displayAddr = UI.cleanAddress(address);
      UI.setText('#mLocation', displayAddr);
      UI.$('#addr').value = displayAddr;

      // ------------------------------
      // Ground elevation summary
      // ------------------------------
      if (Number.isFinite(Number(manifest.house_ground_m))) {
        UI.setText('#mGround', Number(manifest.house_ground_m).toFixed(2));
      }

      // ------------------------------
      // Map target / AOI radius
      // ------------------------------
      const latNum = Number(manifest.lat);
      const lonNum = Number(manifest.lon);
      if (Number.isFinite(latNum) && Number.isFinite(lonNum)) {
        MapModule.placeTarget(latNum, lonNum, Number.isFinite(aoiR) ? aoiR : null);
        UI.$('#lat').value = latNum.toFixed(6);
        UI.$('#lon').value = lonNum.toFixed(6);
      }
      UI.setText('#userAOIRadius', userR);

      // ------------------------------
      // Figures
      // ------------------------------
      const figs = manifest.figs || {};
      for (const [key, filename] of Object.entries(figs)) {
        const url = API.getDownloadUrl(runId, filename);

        switch (key) {
          case 'figure1_elevation':
            UI.$('#fig1').src = url;
            break;
          case 'figure2_slope':
            UI.$('#fig2').src = url;
            break;
          case 'figure3_aspect':
            UI.$('#fig3').src = url;
            break;
          case 'figure4_terrain_and_hist':
            UI.$('#fig4').src = url;
            break;
          case 'figure5_6_3d_combo': {
            const iframe = UI.$('#fig3d');
            iframe.src = url;
            setTimeout(() => {
              iframe.style.height = iframe.parentElement.clientHeight + 'px';
            }, 160);
            break;
          }
        }
      }

      // Risk map
      if (figs['figure_terrain_risk_map']) {
        const urlRisk = API.getDownloadUrl(runId, figs['figure_terrain_risk_map']);
        UI.$('#figRiskMap').src = urlRisk;
      }

      // ------------------------------
      // Summary multiscale table
      // ------------------------------
      const tableSummaryName =
        (manifest.tables || {})['summary_multiscale.csv'] ||
        findFileByRegex(files, [/summary[_-]?multiscale\.csv$/i]);

      let deltaUser = null;
      let delta500 = null;

      if (tableSummaryName) {
        const url = API.getDownloadUrl(runId, tableSummaryName);
        const csv = await API.fetchText(url);
        UI.csvToTable(csv, UI.$('#tblSummary'));

        // Extract ΔElev_median at given radius from CSV
        function getDeltaAtRadius(csvText, targetRadius) {
          const lines = csvText.trim().split('\n');
          if (lines.length < 2) return null;

          const headers = lines[0].split(',').map(h => h.trim());
          const idxRadius = headers.findIndex(h => /^radius\s*\(m\)$/i.test(h));
          const idxDelta = headers.findIndex(h => /Δ?elev.*median/i.test(h));

          if (idxRadius < 0 || idxDelta < 0) return null;

          for (let i = 1; i < lines.length; i++) {
            const cells = lines[i].split(',').map(s => s.trim());
            const r = parseFloat(cells[idxRadius]);
            if (Math.abs(r - targetRadius) < 1e-6) {
              return parseFloat(cells[idxDelta]);
            }
          }
          return null;
        }

        deltaUser = getDeltaAtRadius(csv, userR);
        delta500 = getDeltaAtRadius(csv, 500);
      }

      // ------------------------------
      // Area-level summaries
      // ------------------------------
      const area500Name =
        (manifest.tables || {})['summary_area_level.csv'] ||
        findFileByRegex(files, [/summary[_-]?area[_-]?level\.csv$/i]);

      const areaUserName =
        (manifest.tables || {})['summary_area_level_user.csv'] ||
        findFileByRegex(files, [/summary[_-]?area[_-]?level[_-]?user\.csv$/i]);

      let pctUser = null;
      let pct500 = null;

      // 500m area
      if (area500Name) {
        const url500 = API.getDownloadUrl(runId, area500Name);
        const csv500 = await API.fetchText(url500);
        UI.renderSnapshot('#snap500', parseAreaCSVToStats(csv500));

        const line500 = csv500
          .split('\n')
          .map(s => s.trim())
          .find(s => /^Elevation\s*Percentile\s*Rank/i.test(s));
        if (line500) {
          const parts = line500.split(',').map(s => s.trim());
          pct500 = parts[1] || null;
        } else {
          pct500 = parseAreaCSVToStats(csv500).pct || null;
        }
      }

      // User AOI area
      if (areaUserName) {
        const urlUser = API.getDownloadUrl(runId, areaUserName);
        const csvUser = await API.fetchText(urlUser);
        UI.renderSnapshot('#snapUser', parseAreaCSVToStats(csvUser));

        const lineUser = csvUser
          .split('\n')
          .map(s => s.trim())
          .find(s => /^Elevation\s*Percentile\s*Rank/i.test(s));
        if (lineUser) {
          const parts = lineUser.split(',').map(s => s.trim());
          pctUser = parts[1] || null;
        } else {
          pctUser = parseAreaCSVToStats(csvUser).pct || null;
        }
      }

      // ------------------------------
      // Combined elevation sentences
      // ------------------------------
      function formatAbsolute(value, digits = 3) {
        const n = Number(value);
        return Number.isFinite(n) ? Math.abs(n).toFixed(digits) : '--';
      }

      function getAboveBelowWord(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '--';
        return n < 0 ? 'below' : 'above';
      }

      function formatPercentage(pctStr) {
        if (pctStr == null) return null;
        const p = parseFloat(String(pctStr).replace('%', ''));
        if (!Number.isFinite(p)) return null;
        return `${p.toFixed(2)}%`;
      }

      const userDeltaWord = getAboveBelowWord(deltaUser);
      const userDeltaAbs = formatAbsolute(deltaUser, 3);
      const userPctText = formatPercentage(pctUser);
      const userLine =
        Number.isFinite(Number(deltaUser)) && userPctText
          ? `Within a ${userR} m radius, the home is ${userDeltaAbs} m ${userDeltaWord} the regional median (higher than ${userPctText} of the area)`
          : null;

      const fiveDeltaWord = getAboveBelowWord(delta500);
      const fiveDeltaAbs = formatAbsolute(delta500, 3);
      const fivePctText = formatPercentage(pct500);
      const fiveLine =
        Number.isFinite(Number(delta500)) && fivePctText
          ? `Within a 500 m radius, the home is ${fiveDeltaAbs} m ${fiveDeltaWord} the regional median (higher than ${fivePctText} of the area)`
          : null;

      const mergedHTML = [userLine, fiveLine].filter(Boolean).join('<br/>');
      UI.setHTML('#mPercentDelta', mergedHTML || '--');

      // ------------------------------
      // Narrative (patched with TRS sentence)
      // ------------------------------
      const narrativePath = findFileByRegex(files, [/narrative\.txt$/i]);
      if (narrativePath) {
        const rawText = await API.fetchText(
          API.getDownloadUrl(runId, narrativePath)
        );
        const patched = patchNarrativeWithTRS(rawText, manifest);
        UI.setText('#narrative', patched.trim() || '-- EMPTY NARRATIVE --');
      }

      // ------------------------------
      // File list
      // ------------------------------
      const flatFiles = Array.isArray(files) ? files : Object.values(files || {});
      const fileListHTML = (flatFiles || [])
        .map(filename => {
          const url = API.getDownloadUrl(runId, filename);
          return `<a href="${url}" target="_blank" class="file-link" title="${filename}">
            <i class="fa fa-file-text-o"></i> ${filename}
          </a>`;
        })
        .join('');
      UI.setHTML('#fileList', fileListHTML);
    } catch (error) {
      console.error('Failed to load outputs:', error);
      UI.showNotification('FAILED TO LOAD ANALYSIS RESULTS', 'error');
      UI.setHTML('#mPercentDelta', '--');
    }
  }

  // ---------------------------------------------------------------------------
  // Run analysis (submit job + poll status)
  // ---------------------------------------------------------------------------
  /**
   * Run analysis with given parameters
   */
  async function runAnalysis() {
    // Reset UI for new analysis
    UI.clearUI();
    UI.setText('#status', 'INITIALIZING');
    UI.setBadge('warn', 'PROCESSING', true);
    UI.setText('#hint', 'ESTABLISHING CONNECTION');
    UI.updateProgress(0);

    // Read input values
    const address = UI.$('#addr').value.trim();
    const latStr = UI.$('#lat').value.trim();
    const lonStr = UI.$('#lon').value.trim();
    const radius = parseFloat(UI.$('#radius').value);

    UI.setText('#userAOIRadius', Math.round(radius));

    // Build backend payload
    const payload = {
      aoi_radius_m: radius,
      output_format: 'csv',
      verbose: true,
      generate_narrative: true
    };

    // Choose location source: address or lat/lon
    if (address) {
      payload.address = address;
      UI.setText('#mLocation', address);
    } else if (!Number.isNaN(parseFloat(latStr)) && !Number.isNaN(parseFloat(lonStr))) {
      payload.lat = parseFloat(latStr);
      payload.lon = parseFloat(lonStr);
      UI.setText(
        '#mLocation',
        `${payload.lat.toFixed(6)}, ${payload.lon.toFixed(6)}`
      );
    } else {
      UI.setBadge('err', 'BAD INPUT');
      UI.showNotification('Enter address or lat/lon', 'error');
      UI.updateProgress(0);
      return;
    }

    // Place marker on map if coordinates are provided
    if (!address && !Number.isNaN(+latStr) && !Number.isNaN(+lonStr)) {
      MapModule.placeTarget(+latStr, +lonStr, radius);
    }

    try {
      isAnalyzing = true;

      // Submit run to backend
      const data = await API.submitRun(payload);
      currentRunId = data.run_id;
      UI.$('#rid').value = data.run_id;

      // If cached result is returned immediately
      if (data.status === 'done' && data.message === 'reused from cache') {
        UI.updateProgress(100);
        UI.setBadge('ok', 'COMPLETE');
        UI.setText('#status', 'DONE');
        UI.setText('#hint', 'LOADED FROM CACHE');
        UI.showNotification('Loaded from cache ✅', 'success');
        await loadOutputs(data.run_id);
        isAnalyzing = false;
        return;
      }

      // Otherwise start polling for job status
      UI.$('#rid').setAttribute('title', data.run_id);
      UI.setText('#status', 'QUEUED');
      UI.setBadge('warn', 'WAITING');
      UI.setText('#hint', 'AWAITING JOB SLOT');
      UI.updateProgress(10);

      pollingHandle = API.pollStatus(
        data.run_id,
        // Progress callback
        (progress) => {
          if (typeof progress === 'number') {
            UI.updateProgress(progress);
          }
          UI.setBadge('warn', 'ANALYZING', true);
          UI.setText('#hint', 'PROCESSING LIDAR DATA');
        },
        // Complete callback
        async () => {
          UI.updateProgress(100);
          UI.setBadge('ok', 'COMPLETE');
          UI.setText('#status', 'DONE');
          UI.setText('#hint', 'ANALYSIS COMPLETE');
          UI.showNotification('ANALYSIS COMPLETE', 'success');
          await loadOutputs(currentRunId);
          isAnalyzing = false;
        },
        // Error callback
        (errorData) => {
          UI.updateProgress(0);

          const err = API.extractStructuredError(errorData);
          const uiError = UI.formatUserFacingError(err);

          if (err.error_code === 'AOI_OUTSIDE_EPT_BOUNDS') {
            UI.handleOutOfGridError({ error: err });
          } else {
            UI.setBadge('err', 'FAILED');
            UI.setText('#status', 'ERROR');
            UI.setText('#hint', uiError.hint);
            UI.showNotification(uiError.toast, 'error');
            UI.setText('#narrative', `⚠ ${uiError.hint}`);

            if (CONFIG.DEBUG) {
              console.error('Backend error (raw):', errorData);
            }
          }
          isAnalyzing = false;
        }
      );
    } catch (error) {
      console.error('Analysis failed:', error);
      UI.setBadge('err', 'FAILED');
      UI.setText('#status', 'ERROR');
      UI.setText('#hint', 'REQUEST FAILED');
      UI.updateProgress(0);
      UI.showNotification('REQUEST FAILED', 'error');
      isAnalyzing = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Initialization & event wiring
  // ---------------------------------------------------------------------------
  /**
   * Initialize the application
   */
  async function initialize() {
    console.log('Initializing Tokio Marine Elevation Risk Analysis Platform...');

    // Initialize UI / layout
    UI.init();

    // Initialize map module
    MapModule.initMap();

    // System health check
    try {
      // Small artificial delay so loading screen does not flash too quickly
      await new Promise(resolve => setTimeout(resolve, 2000));

      const isHealthy = await API.checkHealth();
      if (isHealthy) {
        UI.updateSystemStatus('ok', 'SYSTEM READY');
        UI.$('#runBtn').disabled = false;
        console.log('System ready');
      } else {
        throw new Error('Health check failed');
      }
    } catch (error) {
      UI.updateSystemStatus('err', 'CONNECTION FAILED');
      UI.$('#runBtn').disabled = true;
      UI.showNotification('SYSTEM CONNECTION FAILED', 'error');
      console.error('System initialization failed:', error);
    } finally {
      // Fade out loading overlay
      const loadingScreen = UI.$('#loadingScreen');
      if (loadingScreen) {
        loadingScreen.style.opacity = '0';
        setTimeout(() => {
          loadingScreen.style.display = 'none';
        }, 500);
      }
    }

    // Wire up DOM events
    setupEventListeners();

    // Initialize scroll / card animations
    if (CONFIG.ANIMATION_ENABLED) {
      initializeAnimations();
    }

    console.log('Application initialized successfully');
  }

  /**
   * Setup DOM event listeners
   */
  function setupEventListeners() {
    // Run button
    const runBtn = UI.$('#runBtn');
    if (runBtn) {
      runBtn.addEventListener('click', runAnalysis);
    }

    // Copy RUN ID button
    const copyBtn = UI.$('#copyRid');
    if (copyBtn) {
      copyBtn.addEventListener('click', async () => {
        const value = UI.$('#rid')?.value ?? '';
        try {
          await navigator.clipboard.writeText(value);
          UI.showNotification('RUN ID copied.', 'success');
        } catch (error) {
          UI.showNotification('Copy failed.', 'error');
        }
      });
    }

    // Auto reverse-geocode on coordinate blur
    const latInput = UI.$('#lat');
    const lonInput = UI.$('#lon');

    const handleGeocode = async () => {
      const lat = parseFloat(latInput.value);
      const lon = parseFloat(lonInput.value);

      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        const address = await API.reverseGeocode(lat, lon);
        if (address) {
          UI.$('#addr').value = UI.cleanAddress(address);
          UI.showNotification('Auto-filled address from coordinates.', 'success');
        }
      }
    };

    if (latInput) latInput.addEventListener('blur', handleGeocode);
    if (lonInput) lonInput.addEventListener('blur', handleGeocode);

    // Resize handler for 3D iframe and map
    window.addEventListener('resize', () => {
      const iframe = UI.$('#fig3d');
      if (iframe && iframe.src) {
        iframe.style.height = iframe.parentElement.clientHeight + 'px';
      }

      // Invalidate Leaflet map size on window resize
      MapModule.invalidateSize();
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      // Ctrl/Cmd + Enter to run analysis
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        if (!isAnalyzing && !runBtn.disabled) {
          runAnalysis();
        }
      }
      // ESC to close modals etc. is handled inside UI module
    });
  }

  // ---------------------------------------------------------------------------
  // GSAP / ScrollTrigger animations
  // ---------------------------------------------------------------------------
  /**
   * Initialize GSAP-based animations (cards, figures, etc.)
   */
  function initializeAnimations() {
    if (typeof gsap === 'undefined' || typeof ScrollTrigger === 'undefined') {
      console.warn('GSAP not available, skipping animations');
      return;
    }

    // Register ScrollTrigger plugin
    gsap.registerPlugin(ScrollTrigger);

    // Animate glass cards on scroll
    gsap.utils.toArray('.glass-card').forEach((card, i) => {
      gsap.from(card, {
        y: 50,
        opacity: 0,
        duration: 0.8,
        ease: 'power2.out',
        scrollTrigger: {
          trigger: card,
          start: 'top 80%',
          toggleActions: 'play none none none'
        },
        delay: i * 0.1
      });
    });

    // Animate metric cards
    gsap.utils.toArray('.metric-card').forEach((card, i) => {
      gsap.from(card, {
        scale: 0.9,
        opacity: 0,
        duration: 0.6,
        ease: 'back.out(1.7)',
        scrollTrigger: {
          trigger: card,
          start: 'top 85%',
          toggleActions: 'play none none none'
        },
        delay: i * 0.15
      });
    });

    // Animate figure containers
    gsap.utils.toArray('.figure-container').forEach((container, i) => {
      gsap.from(container, {
        x: i % 2 === 0 ? -50 : 50,
        opacity: 0,
        duration: 0.7,
        ease: 'power3.out',
        scrollTrigger: {
          trigger: container,
          start: 'top 80%',
          toggleActions: 'play none none none'
        }
      });
    });

    console.log('Animations initialized');
  }

  // ---------------------------------------------------------------------------
  // Bootstrap: start application when DOM is ready
  // ---------------------------------------------------------------------------
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }

  // Export some handles for debugging from browser console
  window.TokioMarine = {
    CONFIG,
    API,
    UI,
    MapModule,
    runAnalysis,
    loadOutputs,
    getCurrentRunId: () => currentRunId,
    isAnalyzing: () => isAnalyzing
  };

})();
