/* Aftershock Save Planner - operational map.
 *
 * The basemap is imagery only. Every route, ETA and blocked road on screen is
 * computed by the Python planner against our own OpenStreetMap graph; no routing
 * API is involved, which is why blocking a road can take effect instantly.
 */
'use strict';

// Keyless satellite imagery, so the demo runs without anyone signing up for
// anything. To use Google instead, load the Maps JavaScript API with your key and
// swap this Leaflet map for a google.maps.Map -- every overlay below is plain
// lat/lng and ports across unchanged.
const BASEMAP_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const BASEMAP_ATTRIBUTION = 'Imagery &copy; Esri, Maxar, Earthstar Geographics';

const UNIT_COLOURS = ['#4da3ff', '#9d7bff', '#35d0a5', '#ffb020'];
const ROAD_STYLE = { color: '#8fa3bb', weight: 1.6, opacity: 0.45 };
const ROAD_HOVER = { color: '#ffffff', weight: 3.5, opacity: 0.95 };
const BLOCKED_STYLE = { color: '#ff4d5e', weight: 4.5, opacity: 0.95 };

const map = L.map('map', { zoomControl: true, preferCanvas: true, attributionControl: false });
L.tileLayer(BASEMAP_URL, { maxZoom: 19 }).addTo(map);

const roadLayer = L.layerGroup().addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const siteLayer = L.layerGroup().addTo(map);
const unitLayer = L.layerGroup().addTo(map);

const roadLines = new Map();     // edge_id -> polyline
const unitMarkers = new Map();   // resource_id -> { marker, from, to, startedAt }
const siteMarkers = new Map();
let routeSignature = '';
let blockedNow = new Set();
let latest = null;
let selectedWorksite = null;
let unitColour = new Map();
let fitted = false;

const $ = (id) => document.getElementById(id);

/* ---------------------------------------------------------------- roads --- */

async function loadRoads() {
  const response = await fetch('/api/roads');
  const data = await response.json();
  $('attribution').innerHTML = `${data.attribution} &middot; ${BASEMAP_ATTRIBUTION}`;
  const bounds = [];
  for (const edge of data.edges) {
    const line = L.polyline(edge.path, { ...ROAD_STYLE, interactive: true, bubblingMouseEvents: false });
    line.edgeId = edge.edge_id;
    line.roadName = edge.name;
    line.bindTooltip(`${edge.name}<br><span style="opacity:.7">click to block / reopen</span>`,
                     { sticky: true, direction: 'top' });
    line.on('mouseover', () => { if (!blockedNow.has(edge.edge_id)) line.setStyle(ROAD_HOVER); });
    line.on('mouseout', () => { if (!blockedNow.has(edge.edge_id)) line.setStyle(ROAD_STYLE); });
    line.on('click', (event) => { L.DomEvent.stop(event); toggleRoad(edge.edge_id, edge.name); });
    line.addTo(roadLayer);
    roadLines.set(edge.edge_id, line);
    bounds.push(...edge.path);
  }
  if (bounds.length && !fitted) {
    map.fitBounds(L.latLngBounds(bounds), { padding: [24, 24] });
    fitted = true;
  }
}

async function toggleRoad(edgeId, name) {
  const blocked = !blockedNow.has(edgeId);
  await fetch('/api/road', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ edge_id: edgeId, blocked }),
  });
  toast(`${name} ${blocked ? 'blocked' : 'reopened'} — replanning`, blocked);
}

function paintBlocked(blockedIds) {
  const next = new Set(blockedIds);
  for (const edgeId of blockedNow) {
    if (!next.has(edgeId)) roadLines.get(edgeId)?.setStyle(ROAD_STYLE);
  }
  for (const edgeId of next) {
    if (!blockedNow.has(edgeId)) roadLines.get(edgeId)?.setStyle(BLOCKED_STYLE);
  }
  blockedNow = next;
}

/* --------------------------------------------------------------- routes --- */

function paintRoutes(routes) {
  const signature = routes.map((r) => r.route_id).join('|');
  if (signature === routeSignature) return;
  routeSignature = signature;
  routeLayer.clearLayers();
  for (const route of routes) {
    const colour = unitColour.get(route.resource_id) || '#35d0a5';
    const points = route.path.map((p) => [p.latitude, p.longitude]);
    L.polyline(points, { color: '#04101c', weight: 7, opacity: 0.5, interactive: false }).addTo(routeLayer);
    L.polyline(points, { color: colour, weight: 3.5, opacity: 0.95, dashArray: '10 6', interactive: false })
      .addTo(routeLayer);
  }
}

/* ------------------------------------------------------------ worksites --- */

function rankClass(priority) {
  if (priority === null || priority === undefined) return '';
  if (priority >= 0.75) return 'rank-critical';
  if (priority >= 0.55) return 'rank-high';
  return 'rank-moderate';
}

function rankLabel(priority) {
  if (priority === null || priority === undefined) return 'NOT ELIGIBLE';
  if (priority >= 0.75) return 'CRITICAL';
  if (priority >= 0.55) return 'HIGH';
  return 'MODERATE';
}

function paintWorksites(worksites) {
  const container = $('worksites');
  const eligible = worksites.filter((w) => w.eligible && w.operational_status !== 'resolved');
  $('worksite-count').textContent = `(${eligible.length} eligible / ${worksites.length})`;
  const ordered = [...worksites].sort((a, b) => (b.save_priority ?? -1) - (a.save_priority ?? -1));
  container.innerHTML = '';
  for (const worksite of ordered) {
    const card = document.createElement('div');
    card.className = `ws ${rankClass(worksite.save_priority)}`;
    if (!worksite.eligible) card.classList.add('ineligible');
    if (worksite.worksite_id === selectedWorksite) card.classList.add('selected');
    const blockedRoute = worksite.eligible && worksite.candidate_assignments.length === 0;
    card.innerHTML = `
      <div class="ws-top">
        <span class="sector">${worksite.sector}</span>
        <span class="ws-name">${worksite.display_name.replace(worksite.sector, '').trim()}</span>
        <span class="prio">${worksite.save_priority === null || worksite.save_priority === undefined
          ? '&mdash;' : worksite.save_priority.toFixed(2)}</span>
      </div>
      <div class="ws-meta">
        <span class="tag">${rankLabel(worksite.save_priority)}</span>
        <span class="tag ev-${worksite.evidence_status}">${worksite.evidence_status}</span>
        <span class="tag ${worksite.operational_status}">${worksite.operational_status.replace('_', ' ')}</span>
        ${blockedRoute ? '<span class="tag blockedroute">no passable route</span>' : ''}
      </div>`;
    card.onclick = () => { selectedWorksite = worksite.worksite_id; render(latest); };
    container.appendChild(card);
  }
}

function paintSites(worksites) {
  for (const worksite of worksites) {
    const position = [worksite.position.latitude, worksite.position.longitude];
    let marker = siteMarkers.get(worksite.worksite_id);
    const colour = worksite.operational_status === 'resolved' ? '#7d92ad'
      : worksite.save_priority >= 0.75 ? '#ff3b5c'
      : worksite.save_priority >= 0.55 ? '#ff8a3d'
      : worksite.eligible ? '#ffc750' : '#5d748c';
    const icon = L.divIcon({
      className: 'site-icon',
      html: `<div class="site-dot" style="background:${colour}">${worksite.sector}</div>`,
      iconSize: [18, 18],
    });
    if (!marker) {
      marker = L.marker(position, { icon }).addTo(siteLayer);
      marker.on('click', () => { selectedWorksite = worksite.worksite_id; render(latest); });
      siteMarkers.set(worksite.worksite_id, marker);
    } else {
      marker.setIcon(icon);
    }
    marker.bindTooltip(
      `<b>${worksite.sector}</b> ${worksite.display_name.replace(worksite.sector, '').trim()}<br>` +
      `SavePriority ${worksite.save_priority === null || worksite.save_priority === undefined
        ? 'n/a (evidence gate)' : worksite.save_priority.toFixed(2)}<br>` +
      `<span style="opacity:.7">simulated label on a real building</span>`,
      { direction: 'top' });
  }
}

/* ---------------------------------------------------------------- units --- */

function paintUnits(resources) {
  const now = performance.now();
  resources.forEach((resource, index) => {
    if (!unitColour.has(resource.resource_id)) {
      unitColour.set(resource.resource_id, UNIT_COLOURS[index % UNIT_COLOURS.length]);
    }
    const colour = unitColour.get(resource.resource_id);
    const target = [resource.position.latitude, resource.position.longitude];
    let entry = unitMarkers.get(resource.resource_id);
    if (!entry) {
      const icon = L.divIcon({
        className: 'unit-icon',
        html: `<div class="unit-dot" style="background:${colour}">${resource.display_name[0]}</div>` +
              `<div class="unit-label">${resource.display_name}</div>`,
        iconSize: [22, 22],
      });
      const marker = L.marker(target, { icon, zIndexOffset: 1000 }).addTo(unitLayer);
      entry = { marker, from: target, to: target, startedAt: now };
      unitMarkers.set(resource.resource_id, entry);
    } else {
      // Interpolate between server frames so motion along the street looks continuous
      // rather than stepping at the tick rate.
      entry.from = currentLatLng(entry, now);
      entry.to = target;
      entry.startedAt = now;
    }
    entry.marker.bindTooltip(
      `<b>${resource.display_name}</b> &middot; ${resource.status.replace('_', ' ')}<br>` +
      (resource.eta_s ? `ETA ${formatDuration(resource.eta_s)}` : 'stationary'),
      { direction: 'top' });
  });
}

function currentLatLng(entry, now) {
  const t = Math.min(1, (now - entry.startedAt) / 110);
  return [entry.from[0] + (entry.to[0] - entry.from[0]) * t,
          entry.from[1] + (entry.to[1] - entry.from[1]) * t];
}

function animate() {
  const now = performance.now();
  for (const entry of unitMarkers.values()) {
    entry.marker.setLatLng(currentLatLng(entry, now));
  }
  requestAnimationFrame(animate);
}

/* -------------------------------------------------------------- panels --- */

function paintResources(resources) {
  const body = document.querySelector('#resources tbody');
  body.innerHTML = '';
  for (const resource of resources) {
    const colour = unitColour.get(resource.resource_id) || '#4da3ff';
    const worksite = latest.worksites.find((w) => w.worksite_id === resource.current_worksite_id);
    const row = document.createElement('tr');
    row.innerHTML = `
      <td><span class="dot" style="background:${colour}"></span>${resource.display_name}</td>
      <td>${resource.status.replace('_', ' ')}</td>
      <td>${worksite ? `${worksite.sector} <span class="muted">${worksite.display_name
        .replace(worksite.sector, '').trim()}</span>` : '<span class="muted">&mdash;</span>'}</td>
      <td><div class="progress"><i style="width:${(resource.route_progress * 100).toFixed(0)}%;
          background:${colour}"></i></div></td>
      <td>${resource.distance_remaining_m ? `${Math.round(resource.distance_remaining_m)} m`
        : '<span class="muted">&mdash;</span>'}</td>
      <td>${resource.eta_s ? formatDuration(resource.eta_s) : '<span class="muted">&mdash;</span>'}</td>
      <td class="muted">${resource.capability_tags.join(', ')}</td>`;
    body.appendChild(row);
  }
}

function paintExplain(worksites) {
  const panel = $('explain');
  const worksite = worksites.find((w) => w.worksite_id === selectedWorksite)
    || worksites.filter((w) => w.save_priority !== null && w.save_priority !== undefined)
        .sort((a, b) => b.save_priority - a.save_priority)[0];
  if (!worksite) {
    panel.innerHTML = '<div class="empty">No worksite has passed the evidence gate yet.</div>';
    return;
  }
  selectedWorksite = worksite.worksite_id;
  if (!worksite.score_breakdown) {
    panel.innerHTML = `<div class="headline">${worksite.sector} &mdash; not eligible</div>
      <div class="empty">Evidence status is <b>${worksite.evidence_status}</b>. Save planning requires
      corroborated or verified evidence; this is a Search problem until then.</div>`;
    return;
  }
  const weights = { victim_impact: 0.40, time_sensitivity: 0.25, hazard_severity: 0.20, criticality: 0.15 };
  const labels = {
    victim_impact: 'Victim impact', time_sensitivity: 'Time sensitivity',
    hazard_severity: 'Hazard severity', criticality: 'Criticality',
  };
  const rows = Object.entries(worksite.score_breakdown).map(([key, value]) => `
    <tr><td>${labels[key]} &times;${weights[key].toFixed(2)}</td>
    <td><div class="bar-cell"><div class="bar-track"><div class="bar-fill"
      style="width:${(value * 100).toFixed(0)}%"></div></div>
      ${(weights[key] * value).toFixed(3)}</div></td></tr>`).join('');
  const best = [...worksite.candidate_assignments].sort((a, b) => b.assignment_utility - a.assignment_utility)[0];
  panel.innerHTML = `
    <div class="headline">${worksite.sector} &mdash;
      ${worksite.display_name.replace(worksite.sector, '').trim()}</div>
    <table>${rows}
      <tr class="total"><td>SavePriority</td><td>${worksite.save_priority.toFixed(3)}</td></tr>
    </table>
    ${best ? `<table style="margin-top:9px">
      <tr><td>Best unit</td><td>${best.resource_id.replace('rescue-', '')}</td></tr>
      <tr><td>Travel cost &times;0.25</td><td>-${(0.25 * best.travel_cost).toFixed(3)}</td></tr>
      <tr><td>Responder risk &times;0.20</td><td>-${(0.20 * best.responder_risk).toFixed(3)}</td></tr>
      <tr><td>Capability mismatch &times;0.20</td><td>-${(0.20 * best.capability_mismatch).toFixed(3)}</td></tr>
      <tr class="total"><td>AssignmentUtility</td><td>${best.assignment_utility.toFixed(3)}</td></tr>
    </table>` : '<div class="empty" style="margin-top:9px">No passable route from any unit right now.</div>'}
    <ul class="reasons">${worksite.reasons.map((r) => `<li>${r}</li>`).join('')}</ul>`;
}

function paintTrace(trace) {
  $('trace').innerHTML = trace.map((entry) => `
    <div class="row">
      <div class="when">${entry.at.slice(11, 19)} &middot; ${entry.state_version}</div>
      <div class="what">${entry.headline}</div>
      ${entry.detail ? `<div class="detail">${entry.detail}</div>` : ''}
    </div>`).join('');
}

function paintGate(proposal) {
  const gate = $('gate');
  if (!proposal || proposal.status !== 'pending') {
    gate.classList.add('hidden');
    return;
  }
  gate.classList.remove('hidden');
  $('gate-unit').textContent = proposal.resource_name;
  $('gate-from').textContent = proposal.current_sector;
  $('gate-to').textContent = proposal.proposed_sector;
  const b = proposal.breakdown;
  $('gate-math').innerHTML = `
    <tr><td>Proposed utility</td><td>+${b.proposed_utility.toFixed(3)}</td></tr>
    <tr><td>Current utility</td><td>-${b.current_utility.toFixed(3)}</td></tr>
    <tr><td>Progress loss &times;0.5</td><td>-${(0.5 * b.progress_loss).toFixed(3)}</td></tr>
    <tr><td>Additional travel &times;0.5</td><td>-${(0.5 * b.additional_travel).toFixed(3)}</td></tr>
    <tr class="total"><td>Reassignment gain</td><td>+${proposal.reassignment_gain.toFixed(3)}</td></tr>
    <tr><td>Threshold</td><td>+${proposal.threshold.toFixed(2)}</td></tr>`;
  const left = Math.max(0, proposal.seconds_left);
  $('gate-timer').textContent = `00:${String(Math.ceil(left)).padStart(2, '0')}`;
}

/* --------------------------------------------------------------- render --- */

function render(state) {
  if (!state) return;
  latest = state;
  $('scenario-name').textContent = state.scenario;
  $('clock').textContent = `T+ ${formatDuration(state.elapsed_s, true)}`;
  $('state-version').textContent = state.state_version;
  $('pause').textContent = state.running ? 'Pause' : 'Resume';
  paintBlocked(state.blocked_edge_ids);
  paintRoutes(state.routes);
  paintWorksites(state.worksites);
  paintSites(state.worksites);
  paintUnits(state.resources);
  paintResources(state.resources);
  paintExplain(state.worksites);
  paintTrace(state.trace);
  paintGate(state.proposal);
}

function formatDuration(seconds, long = false) {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (long) return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return h ? `${h}h ${m}m` : `${m}:${String(s).padStart(2, '0')}`;
}

let toastTimer = null;
function toast(message, alert = false) {
  const element = $('toast');
  element.textContent = message;
  element.className = `toast show${alert ? ' alert' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.className = 'toast'; }, 2600);
}

/* --------------------------------------------------------------- wiring --- */

$('pause').onclick = () => post('/api/control', { running: !latest.running });
$('speed').onchange = (event) => post('/api/control', { sim_speed: Number(event.target.value) });
$('gate-approve').onclick = () => post('/api/proposal', { approve: true });
$('gate-keep').onclick = () => post('/api/proposal', { approve: false });

function post(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function connect() {
  const source = new EventSource('/api/stream');
  source.onmessage = (event) => render(JSON.parse(event.data));
  source.onerror = () => { source.close(); setTimeout(connect, 1200); };
}

loadRoads().then(() => { connect(); requestAnimationFrame(animate); });
