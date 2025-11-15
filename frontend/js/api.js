/**
 * API Module
 * Handles all backend communication for the Tokio Marine Elevation Risk Analysis Platform
 * @module API
 */

const API = (() => {
    'use strict';
  
    // Configuration
    const config = {
      BASE_URL: "http://127.0.0.1:8000",
      POLLING_INTERVAL: 1400,
      TIMEOUT: 30000
    };
  
    /**
     * Fetch wrapper with error handling
     * @private
     */
    async function fetchWithTimeout(url, options = {}) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), config.TIMEOUT);
  
      try {
        const response = await fetch(url, {
          ...options,
          signal: controller.signal
        });
        clearTimeout(timeout);
        return response;
      } catch (error) {
        clearTimeout(timeout);
        if (error.name === 'AbortError') {
          throw new Error('Request timeout');
        }
        throw error;
      }
    }
  
    /**
     * Check system health status
     */
    async function checkHealth() {
      try {
        const response = await fetchWithTimeout(`${config.BASE_URL}/health`);
        return response.ok;
      } catch (error) {
        console.error('Health check failed:', error);
        return false;
      }
    }
  
    /**
     * Submit a new analysis run
     * @param {Object} payload - Analysis parameters
     * @returns {Promise<Object>} Run response data
     */
    async function submitRun(payload) {
      const response = await fetchWithTimeout(`${config.BASE_URL}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
  
      if (!response.ok) {
        throw new Error(`Request failed: HTTP ${response.status}`);
      }
  
      return response.json();
    }
  
    /**
     * Get run status
     * @param {string} runId - The run identifier
     * @returns {Promise<Object>} Status data
     */
    async function getRunStatus(runId) {
      const response = await fetchWithTimeout(`${config.BASE_URL}/runs/${runId}`);
      return response.json();
    }
  
    /**
     * Get run manifest
     * @param {string} runId - The run identifier
     * @returns {Promise<Object>} Manifest data
     */
    async function getManifest(runId) {
      const response = await fetchWithTimeout(`${config.BASE_URL}/runs/${runId}/manifest`);
      if (!response.ok) {
        throw new Error(`Failed to load manifest: HTTP ${response.status}`);
      }
      return response.json();
    }
  
    /**
     * Download a file from a run
     * @param {string} runId - The run identifier
     * @param {string} filename - The file to download
     * @returns {string} Download URL
     */
    function getDownloadUrl(runId, filename) {
      return `${config.BASE_URL}/runs/${runId}/download?filename=${encodeURIComponent(filename)}`;
    }
  
    /**
     * Fetch text content from a URL
     * @param {string} url - The URL to fetch from
     * @returns {Promise<string>} Text content
     */
    async function fetchText(url) {
      const response = await fetchWithTimeout(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.text();
    }
  
    /**
     * Reverse geocode coordinates to address
     * @param {number} lat - Latitude
     * @param {number} lon - Longitude
     * @returns {Promise<string|null>} Address or null
     */
    async function reverseGeocode(lat, lon) {
      try {
        const response = await fetchWithTimeout(
          `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&accept-language=en&namedetails=0`
        );
        const data = await response.json();
        if (data && data.display_name) {
          return data.display_name;
        }
      } catch (error) {
        console.warn('OSM geocoding failed:', error);
      }
      return null;
    }
  
    /**
     * Poll run status until completion
     * @param {string} runId - The run identifier
     * @param {Function} onProgress - Progress callback
     * @param {Function} onComplete - Completion callback
     * @param {Function} onError - Error callback
     * @returns {Object} Interval ID and stop function
     */
    function pollStatus(runId, onProgress, onComplete, onError) {
      let progressValue = 10;
      
      const progressInterval = setInterval(() => {
        if (progressValue < 90) {
          progressValue += Math.random() * 2;
          onProgress?.(progressValue);
        }
      }, 500);
  
      const statusInterval = setInterval(async () => {
        try {
          const status = await getRunStatus(runId);
          
          if (status.status === 'done') {
            clearInterval(statusInterval);
            clearInterval(progressInterval);
            onComplete?.(status);
          } else if (status.status === 'error') {
            clearInterval(statusInterval);
            clearInterval(progressInterval);
            onError?.(status);
          } else if (status.status === 'running') {
            onProgress?.(progressValue);
          }
        } catch (error) {
          clearInterval(statusInterval);
          clearInterval(progressInterval);
          onError?.({ error: error.message });
        }
      }, config.POLLING_INTERVAL);
  
      return {
        statusInterval,
        progressInterval,
        stop: () => {
          clearInterval(statusInterval);
          clearInterval(progressInterval);
        }
      };
    }
  
    /**
     * Extract structured error information
     * @param {*} errorData - Error data from API
     * @returns {Object} Structured error
     */
    function extractStructuredError(errorData) {
      const out = { error_code: undefined, message: undefined };
  
      if (errorData && typeof errorData.error === 'object' && errorData.error !== null) {
        return { ...errorData.error };
      }
  
      if (errorData && typeof errorData.error === 'string') {
        try {
          return JSON.parse(errorData.error);
        } catch (e) {
          // Not JSON, continue
        }
      }
  
      const msg = (errorData && typeof errorData.message === 'string') ? errorData.message : '';
      if (msg) {
        const match = msg.match(/\{[\s\S]*?"error_code"[\s\S]*?\}/);
        if (match) {
          try {
            return JSON.parse(match[0]);
          } catch (e) {
            // Not valid JSON
          }
        }
        out.message = msg;
      }
  
      return out;
    }
  
    // Public API
    return {
      config,
      checkHealth,
      submitRun,
      getRunStatus,
      getManifest,
      getDownloadUrl,
      fetchText,
      reverseGeocode,
      pollStatus,
      extractStructuredError
    };
  })();
  
  // Export for use in other modules
  window.API = API;