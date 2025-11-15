/**
 * Map Module
 * Handles all map-related functionality using Leaflet
 * @module MapModule
 */

const MapModule = (() => {
    'use strict';
  
    // Module variables
    let satMap = null;
    let overlayGroup = null;
    let aoiCircle = null;
    let circle500 = null;
    let satMarker = null;
  
    // Constants
    const STAR_SVG = `
      <svg viewBox="0 0 24 24" width="28" height="28" fill="#FFD60A" stroke="#222" stroke-width="1.1">
        <path d="M12 2l2.9 6.6 7.1.6-5.3 4.6 1.6 6.7L12 16.9 5.7 20.5l1.6-6.7L2 9.2l7.1-.6z"/>
      </svg>`;
  
    /**
     * Create a star icon for the map marker
     * @param {number} size - Icon size in pixels
     * @returns {L.DivIcon} Leaflet div icon
     */
    function createStarIcon(size = 28) {
      return L.divIcon({
        className: "",
        html: `<div class="star-marker" title="TARGET">
                 <div class="halo"></div>
                 ${STAR_SVG}
               </div>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
      });
    }
  
    /**
     * Initialize the map
     * @param {string} elementId - The ID of the map container element
     */
    function initMap(elementId = 'satMap') {
      const element = document.getElementById(elementId);
      if (!element) {
        console.error(`Map element ${elementId} not found`);
        return;
      }
  
      // Create map instance
      satMap = L.map(element, { zoomControl: true }).setView([0, 0], 2);
  
      // Define tile layers
      const osmLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '© OpenStreetMap'
      });
  
      const esriWorldImagery = L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        {
          maxZoom: 20,
          attribution: "Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community"
        }
      );
  
      const esriPlaces = L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        {
          maxZoom: 20,
          opacity: 0.8,
          attribution: "Esri Boundaries & Places"
        }
      );
  
      // Add default layers
      esriWorldImagery.addTo(satMap);
      esriPlaces.addTo(satMap);
  
      // Add layer control
      L.control.layers(
        {
          "🛰 Satellite (Esri)": esriWorldImagery,
          "🗺 Street (OSM)": osmLayer
        },
        {
          "🏷 Labels (Esri)": esriPlaces
        },
        { collapsed: false }
      ).addTo(satMap);
  
      // Initialize overlay group for circles and markers
      overlayGroup = L.layerGroup().addTo(satMap);
  
      console.log('Map initialized successfully');
    }
  
    /**
     * Place a target marker on the map with AOI circles
     * @param {number} lat - Latitude
     * @param {number} lon - Longitude
     * @param {number} radiusM - Analysis radius in meters (optional)
     */
    function placeTarget(lat, lon, radiusM = null) {
      // Initialize map if not already done
      if (!satMap) {
        initMap();
      }
  
      // Validate coordinates
      const latNum = Number(lat);
      const lonNum = Number(lon);
      if (!Number.isFinite(latNum) || !Number.isFinite(lonNum)) {
        console.error('Invalid coordinates:', lat, lon);
        return;
      }
  
      // Clear existing overlays
      if (overlayGroup) {
        overlayGroup.clearLayers();
      }
      aoiCircle = null;
      circle500 = null;
      satMarker = null;
  
      // Create and add star marker
      const icon = createStarIcon(28);
      satMarker = L.marker([latNum, lonNum], { 
        icon, 
        zIndexOffset: 1000 
      }).addTo(overlayGroup);
  
      // Add tooltip to marker
      satMarker.bindTooltip("Target", { 
        permanent: false, 
        direction: "top", 
        offset: [0, -12] 
      });
  
      // Bring marker to front if possible
      if (satMarker.bringToFront) {
        satMarker.bringToFront();
      }
  
      // Add user-selected radius circle (red)
      if (Number.isFinite(radiusM) && radiusM > 0) {
        aoiCircle = L.circle([latNum, lonNum], {
          radius: radiusM,
          color: "#b3001b",        // Red color for user radius
          weight: 3,
          fillColor: "#b3001b",
          fillOpacity: 0.08
        }).addTo(overlayGroup);
      }
  
      // Add 500m reference circle (yellow)
      circle500 = L.circle([latNum, lonNum], {
        radius: 500,
        color: "#FFD60A",         // Yellow color for 500m
        weight: 3,
        fillColor: "#FFD60A",
        fillOpacity: 0.05
      }).addTo(overlayGroup);
  
      // Fit map bounds to show all circles
      const bounds = L.latLngBounds([]);
      if (aoiCircle) {
        bounds.extend(aoiCircle.getBounds());
      }
      if (circle500) {
        bounds.extend(circle500.getBounds());
      }
  
      if (bounds.isValid()) {
        satMap.fitBounds(bounds, { 
          maxZoom: 19, 
          padding: [20, 20] 
        });
      } else {
        satMap.setView([latNum, lonNum], 18);
      }
  
      // Force map size recalculation
      setTimeout(() => {
        if (satMap) {
          satMap.invalidateSize();
        }
      }, 100);
  
      console.log(`Target placed at ${latNum}, ${lonNum} with radius ${radiusM}m`);
    }
  
    /**
     * Update the map view
     * @param {number} lat - Latitude
     * @param {number} lon - Longitude
     * @param {number} zoom - Zoom level (optional)
     */
    function updateView(lat, lon, zoom = 18) {
      if (!satMap) {
        console.warn('Map not initialized');
        return;
      }
  
      const latNum = Number(lat);
      const lonNum = Number(lon);
      if (!Number.isFinite(latNum) || !Number.isFinite(lonNum)) {
        console.error('Invalid coordinates:', lat, lon);
        return;
      }
  
      satMap.setView([latNum, lonNum], zoom);
    }
  
    /**
     * Clear all overlays from the map
     */
    function clearOverlays() {
      if (overlayGroup) {
        overlayGroup.clearLayers();
      }
      aoiCircle = null;
      circle500 = null;
      satMarker = null;
    }
  
    /**
     * Refresh the map size (useful after container resize)
     */
    function invalidateSize() {
      if (satMap) {
        satMap.invalidateSize();
      }
    }
  
    /**
     * Get the current map instance
     * @returns {L.Map|null} The Leaflet map instance
     */
    function getMap() {
      return satMap;
    }
  
    /**
     * Destroy the map and clean up resources
     */
    function destroy() {
      if (satMap) {
        satMap.remove();
        satMap = null;
      }
      overlayGroup = null;
      aoiCircle = null;
      circle500 = null;
      satMarker = null;
    }
  
    // Public API
    return {
      initMap,
      placeTarget,
      updateView,
      clearOverlays,
      invalidateSize,
      getMap,
      destroy
    };
  })();
  
  // Export for use in other modules
  window.MapModule = MapModule;