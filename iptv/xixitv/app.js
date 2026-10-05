"use strict";

/**
 * xixitv.live — minimal live TV frontend.
 * Playback: ArtPlayer + hls.js (+ optional P2P).
 * Favorites / history: localStorage only (no login).
 */

const LS_FAV = "xixitv:favorites";
const LS_RECENT = "xixitv:recent";
const LS_QUALITY = "xixitv:qualityPref";
const LS_SOURCE = "xixitv:sourcePref";
const LS_COLLAPSED_GROUPS = "xixitv:collapsedGroups";
const LS_SIDEBAR = "sidebarCollapsed";
const LS_P2P = "xixitv:p2pMode";
const MAX_RECENT = 8;
const MIN_P2P_ONLINE = 2;
// How many segments in a row may fail to patch before the official CCTV stream
// is abandoned. One or two are normal (a segment the edge has not published
// yet, which the server retries); a run of them means we cannot decode it.
const MAX_PATCH_FAILURES = 3;
const P2P_MODES = ["auto", "on", "off"];

const state = {
  config: null,
  channels: [],
  activeId: null,
  art: null,
  bytes: { http: 0, p2p: 0 },
  peers: new Set(),
  favorites: new Set(),
  recent: [],
  collapsedGroups: new Set(),
  filterQuery: "",
  sourceLatency: {},
  p2pMode: "on",
  onlineCount: null,
  channelCount: null,
  channelCounts: null,
  presenceES: null,
  presenceURL: "",
};

const els = {
  app: document.querySelector(".app"),
  toggle: document.getElementById("toggle-sidebar"),
  toggleFav: document.getElementById("toggle-fav"),
  siteTitle: document.getElementById("site-title"),
  list: document.getElementById("channel-list"),
  search: document.getElementById("search"),
  nowPlaying: document.getElementById("now-playing-name"),
  placeholder: document.getElementById("placeholder"),
  playError: document.getElementById("play-error"),
  playErrorMsg: document.getElementById("play-error-msg"),
  playErrorRetry: document.getElementById("play-error-retry"),
  statOnline: document.getElementById("stat-online"),
  statHttp: document.getElementById("stat-http"),
  statP2p: document.getElementById("stat-p2p"),
  statPeers: document.getElementById("stat-peers"),
  adSlot: document.getElementById("ad-slot"),
  adsenseUnit: document.getElementById("adsense-unit"),
  audioOnly: document.getElementById("audio-only"),
};

function loadJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (e) {
    return fallback;
  }
}

function saveJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {}
}

function getQualityPref(channelId) {
  const all = loadJSON(LS_QUALITY, {});
  return all[channelId] || "";
}

function setQualityPref(channelId, label) {
  const all = loadJSON(LS_QUALITY, {});
  all[channelId] = label;
  saveJSON(LS_QUALITY, all);
}

function getSourcePref(channelId) {
  const all = loadJSON(LS_SOURCE, {});
  return all[channelId] || "";
}

function setSourcePref(channelId, label) {
  const all = loadJSON(LS_SOURCE, {});
  all[channelId] = label;
  saveJSON(LS_SOURCE, all);
}

function stripProbeLabel(label) {
  if (!label) return label;
  const sep = " · ";
  const i = label.lastIndexOf(sep);
  if (i >= 0 && /\d+ms$/.test(label.slice(i + sep.length))) {
    return label.slice(0, i);
  }
  return label;
}

function formatSourceLabel(base, ms) {
  if (ms == null || ms < 0) return base;
  return base + " · " + ms + "ms";
}

async function probeSourceLatency(url) {
  const t0 = performance.now();
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    const res = await fetch(url, {
      method: "GET",
      headers: { Range: "bytes=0-0" },
      cache: "no-store",
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!res.ok && res.status !== 206) return null;
    return Math.round(performance.now() - t0);
  } catch (e) {
    return null;
  }
}

async function probeChannelSources(ch, art) {
  const items = allSourceUrls(ch).map((s) => ({
    label: stripProbeLabel(s.label),
    url: s.url,
  }));
  const probed = await Promise.all(
    items.map(async (item) => ({
      ...item,
      ms: await probeSourceLatency(item.url),
    }))
  );
  probed.sort((a, b) => (a.ms == null ? 1e9 : a.ms) - (b.ms == null ? 1e9 : b.ms));
  state.sourceLatency[ch.id] = probed;
  refreshSourceMenu(art, ch);
}

function refreshSourceMenu(art, ch) {
  const opts = buildSourceMenuOptions(ch);
  if (!opts || opts.length < 2 || !art.setting) return;
  setupSourceMenu(art, opts, ch.id, ch);
}

function buildSourceMenuOptions(ch) {
  const items = [];
  const pref = getSourcePref(ch.id);
  const latencies = state.sourceLatency[ch.id];
  let picked = false;

  function labelFor(url, baseLabel) {
    const base = stripProbeLabel(baseLabel);
    const hit = latencies && latencies.find((p) => p.url === url);
    if (hit && hit.ms != null) return formatSourceLabel(base, hit.ms);
    return base;
  }

  if (ch.url) {
    const html = labelFor(ch.url, "默认");
    const isDefault = !pref || pref === "默认" || pref === html || stripProbeLabel(pref) === "默认";
    if (isDefault) picked = true;
    items.push({ html: html, url: ch.url, default: isDefault });
  }
  for (const s of ch.sources || []) {
    const html = labelFor(s.url, s.label);
    const base = stripProbeLabel(s.label);
    const isDefault = pref === s.label || pref === html || stripProbeLabel(pref) === base;
    if (isDefault) picked = true;
    items.push({ html: html, url: s.url, default: isDefault });
  }
  if (items.length < 2) return null;
  if (!picked) items[0].default = true;
  return items;
}

function initialSourceUrl(ch, sourceOpts) {
  if (!sourceOpts || !sourceOpts.length) return ch.url;
  const chosen = sourceOpts.find((o) => o.default) || sourceOpts[0];
  return chosen.url || ch.url;
}

function setupSourceMenu(art, options, channelId, channel) {
  if (!art.setting || !options || options.length < 2) return;
  const setting = {
    name: "source-line",
    html: "线路",
    tooltip: "切换直播源",
    selector: options.map((o) => ({ html: o.html, url: o.url })),
    onSelect: function (item) {
      setSourcePref(channelId, item.html);
      art._playUrl = item.url;
      art._netRecoveries = 0;
      art._mediaRecoveries = 0;
      const wasPlaying = art.video && !art.video.paused;
      if (art.hls) {
        art.hls.loadSource(item.url);
        art.hls.startLoad(-1);
        if (wasPlaying) safePlay(art);
      } else if (typeof art.switchUrl === "function") {
        art.switchUrl(item.url);
      } else {
        art.url = item.url;
      }
      return item.html;
    },
  };

  // Source latency probing can finish before ArtPlayer emits "ready". Both
  // paths call this function, so update the existing item instead of adding
  // the same component name twice.
  if (art._sourceMenuAdded && typeof art.setting.update === "function") {
    art.setting.update(setting);
    return;
  }
  try {
    art.setting.add(setting);
    art._sourceMenuAdded = true;
  } catch (e) {
    if (typeof art.setting.update === "function") {
      art.setting.update(setting);
      art._sourceMenuAdded = true;
      return;
    }
    throw e;
  }
}

function buildArtQualityOptions(ch) {
  const qs = ch.qualities;
  if (!qs || qs.length < 2) return null;
  const pref = getQualityPref(ch.id);
  let picked = false;
  const opts = qs.map((q, i) => {
    const isDefault = pref ? q.label === pref : i === 0;
    if (isDefault) picked = true;
    return { default: isDefault, html: q.label, url: q.url };
  });
  if (!picked) opts[0].default = true;
  return opts;
}

function initialPlaybackUrl(ch, qualityOpt) {
  if (!qualityOpt || !qualityOpt.length) return ch.url;
  const chosen = qualityOpt.find((q) => q.default) || qualityOpt[0];
  return chosen.url || ch.url;
}

// qualityTierName is the naming Chinese video sites use, from cheapest to richest:
// 流畅 / 标清 / 高清 / 超清 / 蓝光. A bare resolution makes the viewer work out for
// themselves which option saves bandwidth.
function qualityTierName(height) {
  if (height >= 1080) return "蓝光";
  if (height >= 720) return "超清";
  if (height >= 540) return "高清";
  if (height >= 432) return "标清";
  return "流畅";
}

function hlsLevelLabel(level, index) {
  // Both the tier and the resolution: the tier says what it costs, the resolution
  // says exactly what it is. CCTV's renditions include 576p and 540p, which land in
  // the same tier and would otherwise be indistinguishable.
  if (level.height) return qualityTierName(level.height) + " " + level.height + "P";
  if (level.bitrate) return Math.round(level.bitrate / 1000) + "K";
  return "线路 " + (index + 1);
}

function setupHlsQualityMenu(art, hls, channelId) {
  if (!hls.levels || hls.levels.length <= 1 || !art.setting) return;

  const selector = [{ html: "自动", value: -1 }];
  hls.levels.forEach((level, i) => {
    selector.push({ html: hlsLevelLabel(level, i), value: i });
  });

  const pref = getQualityPref(channelId);
  if (pref) {
    const item = selector.find((s) => s.html === pref);
    if (item) {
      hls.currentLevel = item.value;
    }
  }

  art.setting.add({
    name: "quality-hls",
    html: "清晰度",
    tooltip: "清晰度",
    selector: selector,
    onSelect: function (item) {
      hls.currentLevel = item.value;
      setQualityPref(channelId, item.html);
      return item.html;
    },
  });
}

function loadLocalState() {
  state.favorites = new Set(loadJSON(LS_FAV, []));
  state.recent = loadJSON(LS_RECENT, []);
  state.collapsedGroups = new Set(loadJSON(LS_COLLAPSED_GROUPS, []));
  state.p2pMode = loadP2pMode();
}

function loadP2pMode() {
  try {
    const v = localStorage.getItem(LS_P2P);
    if (v === "force") return "on";
    if (P2P_MODES.indexOf(v) >= 0) return v;
    const legacy = localStorage.getItem("xixitv:p2pEnabled");
    if (legacy === "0") return "off";
    if (legacy === "1") return "auto";
  } catch (e) {}
  return "on";
}

function saveP2pMode() {
  try {
    localStorage.setItem(LS_P2P, state.p2pMode);
  } catch (e) {}
}

function p2pCapable() {
  return (
    !!state.config &&
    state.config.p2p &&
    state.config.p2p.enabled &&
    typeof Hls !== "undefined" &&
    Hls.isSupported() &&
    window.p2pml &&
    window.p2pml.hlsjs &&
    typeof window.p2pml.hlsjs.HlsJsP2PEngine === "function"
  );
}

function p2pCrowdOk(online) {
  const n = online !== undefined ? online : state.onlineCount;
  if (n == null) return true;
  return n >= MIN_P2P_ONLINE;
}

function p2pEffective(online) {
  if (!p2pCapable()) return false;
  if (state.p2pMode === "off") return false;
  if (state.p2pMode === "on") return true;
  return p2pCrowdOk(online);
}

function p2pModeLabel(mode) {
  if (mode === "auto") return "自动";
  if (mode === "on") return "开启";
  if (mode === "off") return "关闭";
  return "自动";
}

function p2pModeTitle(mode, standby) {
  if (mode === "auto") {
    if (standby) {
      return "自动：当前人少，暂走 HTTP；点击切换为 开启";
    }
    return "自动：在线 ≥ " + MIN_P2P_ONLINE + " 时启用 P2P；点击切换为 开启";
  }
  if (mode === "on") return "开启：始终启用 P2P；点击切换为 关闭";
  return "关闭：始终 HTTP；点击切换为 自动";
}

function p2pToggleNodes() {
  return document.querySelectorAll(".p2p-toggle");
}

function updateP2PBadge() {
  const nodes = p2pToggleNodes();
  if (!nodes.length) return;
  const capable = p2pCapable();
  const active = p2pEffective();
  const standby = capable && state.p2pMode === "auto" && !p2pCrowdOk();
  let text = "P2P";
  let title = "P2P";
  if (!capable) {
    text = "P2P 不可用";
    title = "站点未启用 P2P 或浏览器不支持";
  } else {
    text = "P2P " + p2pModeLabel(state.p2pMode);
    title = p2pModeTitle(state.p2pMode, standby);
  }
  nodes.forEach(function (el) {
    el.classList.toggle("on", active);
    el.classList.toggle("standby", standby);
    el.classList.toggle("mode-on", state.p2pMode === "on");
    el.classList.toggle("mode-off", state.p2pMode === "off");
    el.textContent = text;
    el.title = title;
    el.setAttribute("aria-pressed", String(active));
  });
}

function cycleP2pMode() {
  if (!p2pCapable()) {
    if (state.art && state.art.notice) {
      state.art.notice.show = "站点未启用 P2P";
    }
    return;
  }
  const i = P2P_MODES.indexOf(state.p2pMode);
  state.p2pMode = P2P_MODES[(i + 1) % P2P_MODES.length];
  saveP2pMode();
  updateP2PBadge();
  if (state.activeId) playChannel(state.activeId);
}

function bindP2pToggles() {
  p2pToggleNodes().forEach(function (el) {
    el.addEventListener("click", cycleP2pMode);
  });
}

// One id per tab. EventSource reconnects by itself, and changing channel opens
// a new stream; a fresh server id each time counted the same person twice until
// the previous stream expired.
function presenceClientID() {
  const key = "xixitv:presenceSid";
  try {
    let id = sessionStorage.getItem(key);
    if (!/^[0-9a-f]{32}$/.test(id || "")) {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      id = Array.from(bytes, function (b) {
        return b.toString(16).padStart(2, "0");
      }).join("");
      sessionStorage.setItem(key, id);
    }
    return id;
  } catch (e) {
    return "";
  }
}

function presenceStreamURL() {
  const params = new URLSearchParams();
  const sid = presenceClientID();
  if (sid) params.set("sid", sid);
  if (state.activeId) params.set("channel", state.activeId);
  const q = params.toString();
  return q ? "/api/presence?" + q : "/api/presence";
}

function watchingTotal(counts, fallback) {
  if (!counts) return fallback;
  let n = 0;
  for (const id in counts) {
    const v = counts[id];
    if (typeof v === "number" && v > 0) n += v;
  }
  return n;
}

function renderOnlineCount() {
  if (!els.statOnline) return;
  if (state.onlineCount == null) {
    els.statOnline.textContent = "–";
    return;
  }
  // Numerator is this channel; denominator is the whole site. With nothing
  // playing there is no numerator, so only the site total is shown.
  if (state.activeId && typeof state.channelCount === "number") {
    els.statOnline.textContent = state.channelCount + "/" + state.onlineCount;
    return;
  }
  els.statOnline.textContent = String(state.onlineCount);
}

function connectPresence() {
  if (typeof EventSource === "undefined") return;
  const url = presenceStreamURL();
  if (state.presenceES && state.presenceURL === url) return;

  if (state.presenceES) {
    const prev = state.presenceES;
    state.presenceES = null;
    state.presenceURL = "";
    prev.close();
  }

  let es;
  try {
    es = new EventSource(url);
  } catch (err) {
    return;
  }
  state.presenceES = es;
  state.presenceURL = url;
  es.onmessage = (e) => {
    if (state.presenceES !== es) return;
    try {
      const data = JSON.parse(e.data);
      if (typeof data.online === "number") {
        const prev = state.onlineCount;
        state.channelCount = typeof data.channel === "number" ? data.channel : null;
        state.channelCounts =
          data.channels && typeof data.channels === "object" ? data.channels : null;
        // The site total is people actually on a channel, which is what the
        // sidebar numbers add up to. data.online also counts tabs sitting on
        // the channel list.
        state.onlineCount = watchingTotal(state.channelCounts, data.online);
        renderOnlineCount();
        renderChannelViewerCounts();
        // Crowd check stays on the site-wide total (state.onlineCount).
        const wasEffective = p2pEffective(prev);
        updateP2PBadge();
        if (state.activeId && wasEffective !== p2pEffective()) {
          playChannel(state.activeId);
        }
      }
    } catch (err) {}
  };
  es.onerror = () => {
    if (state.presenceES !== es) return;
    els.statOnline.textContent = "–";
  };
}

function initPresence() {
  connectPresence();
}

// setupAnalytics loads GA4 only when a measurement id is configured. Without one
// nothing is fetched and no cookie is set, which is the point of keeping it in the
// config rather than in index.html.
function setupAnalytics(analytics) {
  if (!analytics || !analytics.googleId) return;
  if (document.querySelector('script[data-ga="1"]')) return;

  window.dataLayer = window.dataLayer || [];
  window.gtag = function () {
    window.dataLayer.push(arguments);
  };
  gtag("js", new Date());
  gtag("config", analytics.googleId);

  const s = document.createElement("script");
  s.async = true;
  s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(analytics.googleId);
  s.dataset.ga = "1";
  document.head.appendChild(s);
}

// trackChannel reports which channel a viewer switched to. This is a single-page
// app, so switching channels is not a navigation and would otherwise be invisible
// — and which channels people actually watch is the one thing worth measuring on a
// channel list.
// audioOnlyForUrl reports whether the URL the player is on carries no video.
// The server marks it per source; the URL check covers a source picked from the
// menu after the page loaded.
function audioOnlyForUrl(ch, url) {
  if (!ch || !url) return false;
  const hit = (ch.sources || []).find((s) => s.url === url);
  if (hit) return !!hit.audioOnly;
  if (url === ch.url) return !!ch.audioOnly;
  return /\/audio\//i.test(url);
}

// showAudioOnly tells the viewer the channel is audio, instead of leaving a black
// frame that is indistinguishable from a broken stream.
function showAudioOnly(on) {
  if (!els.audioOnly) return;
  els.audioOnly.classList.toggle("hidden", !on);
}

function trackChannel(ch) {
  const analytics = state.config && state.config.analytics;
  if (!analytics || !analytics.googleId || !analytics.channelEvents) return;
  if (typeof window.gtag !== "function") return;
  try {
    gtag("event", "select_channel", {
      channel_id: ch.id,
      channel_name: ch.name,
      channel_group: ch.group || "",
    });
  } catch (e) {}
}

function renderAds(ads) {
  if (!els.adSlot) return;
  els.adSlot.hidden = true;
  els.adSlot.innerHTML = "";
  if (!ads || !ads.enabled) return;

  const sense = ads.adsense || {};
  if (sense.client && sense.slot) {
    els.adSlot.hidden = false;
    const ins = document.createElement("ins");
    ins.className = "adsbygoogle";
    ins.style.display = "block";
    ins.setAttribute("data-ad-client", sense.client);
    ins.setAttribute("data-ad-slot", sense.slot);
    ins.setAttribute("data-ad-format", "auto");
    ins.setAttribute("data-full-width-responsive", "true");
    els.adSlot.appendChild(ins);

    if (!document.querySelector('script[data-adsense="1"]')) {
      const s = document.createElement("script");
      s.async = true;
      s.src = "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=" + encodeURIComponent(sense.client);
      s.crossOrigin = "anonymous";
      s.dataset.adsense = "1";
      document.head.appendChild(s);
      s.onload = () => {
        try {
          (window.adsbygoogle = window.adsbygoogle || []).push({});
        } catch (e) {}
      };
    } else {
      try {
        (window.adsbygoogle = window.adsbygoogle || []).push({});
      } catch (e) {}
    }
    return;
  }

  const b = ads.banner || {};
  let html = "";
  if (b.html) html = b.html;
  else if (b.image) {
    const img = '<img src="' + escapeAttr(b.image) + '" alt="广告" />';
    html = b.link ? '<a href="' + escapeAttr(b.link) + '" target="_blank" rel="noopener">' + img + "</a>" : img;
  } else if (b.text) {
    html = b.link
      ? '<a href="' + escapeAttr(b.link) + '" target="_blank" rel="noopener">' + escapeHtml(b.text) + "</a>"
      : escapeHtml(b.text);
  }
  if (!html) return;
  els.adSlot.innerHTML = html;
  els.adSlot.hidden = false;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

function setSidebarCollapsed(collapsed) {
  els.app.classList.toggle("sidebar-collapsed", collapsed);
  els.toggle.setAttribute("aria-expanded", String(!collapsed));
  try {
    localStorage.setItem(LS_SIDEBAR, collapsed ? "1" : "0");
  } catch (e) {}
}

function initSidebarToggle() {
  let collapsed = false;
  try {
    collapsed = localStorage.getItem(LS_SIDEBAR) === "1";
  } catch (e) {}
  if (window.matchMedia("(max-width: 720px)").matches) {
    collapsed = true;
  }
  setSidebarCollapsed(collapsed);
  els.toggle.addEventListener("click", () => {
    setSidebarCollapsed(!els.app.classList.contains("sidebar-collapsed"));
  });
}

function cctvSortKey(name) {
  const m = String(name).match(/CCTV[-\s]?(\d+)/i);
  return m ? parseInt(m[1], 10) : 999;
}

function sortChannels(a, b) {
  const ga = a.group || "";
  const gb = b.group || "";
  const order = (state.config && state.config.groupOrder) || [];
  const ia = order.indexOf(ga);
  const ib = order.indexOf(gb);
  const ra = ia >= 0 ? ia : 999;
  const rb = ib >= 0 ? ib : 999;
  if (ra !== rb) return ra - rb;
  if (ga === "央视" || ga.includes("央视")) {
    const da = cctvSortKey(a.name);
    const db = cctvSortKey(b.name);
    if (da !== db) return da - db;
  }
  return a.name.localeCompare(b.name, "zh-CN");
}

function channelById(id) {
  return state.channels.find((c) => c.id === id);
}

function buildDisplayGroups(filtered) {
  const groups = new Map();
  const favItems = [];
  for (const id of state.favorites) {
    const ch = channelById(id);
    if (ch && filtered.some((c) => c.id === id)) favItems.push(ch);
  }
  if (favItems.length) groups.set("收藏", favItems);

  const recentItems = [];
  for (const id of state.recent) {
    const ch = channelById(id);
    if (ch && filtered.some((c) => c.id === id) && !state.favorites.has(id)) {
      recentItems.push(ch);
    }
  }
  if (recentItems.length) groups.set("最近", recentItems);

  for (const ch of filtered) {
    if (state.favorites.has(ch.id)) continue;
    const g = ch.group || "频道";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(ch);
  }
  for (const [g, items] of groups) {
    if (g !== "收藏" && g !== "最近") {
      items.sort((a, b) => {
        if ((a.group || "") === "央视" || (a.group || "").includes("央视")) {
          return cctvSortKey(a.name) - cctvSortKey(b.name);
        }
        return a.name.localeCompare(b.name, "zh-CN");
      });
    }
  }
  return groups;
}

function defaultLogoUrl(name, id) {
  name = (name || "").trim();
  id = (id || "").trim();
  const base = "https://live.fanmingming.cn/tv/";
  if (/CCTV[-\s]?5\+/i.test(name) || id === "cctv-5plus") {
    return base + "CCTV5+.png";
  }
  const cctv = name.match(/CCTV[-\s]?(\d+)/i);
  if (cctv) return base + "CCTV" + cctv[1] + ".png";
  if (id.startsWith("cctv-")) {
    const num = id.slice(5);
    if (num === "5plus") return base + "CCTV5+.png";
    if (/^\d+$/.test(num)) return base + "CCTV" + num + ".png";
  }
  if (id.startsWith("cgtn")) {
    const map = {
      cgtn记录: "CGTN纪录.png",
      cgtn俄语: "CGTN俄语.png",
      cgtn法语: "CGTN法语.png",
      cgtn西语: "CGTN西语.png",
      cgtn阿语: "CGTN阿语.png",
    };
    return base + (map[id] || "CGTN.png");
  }
  let display = name;
  const sep = display.indexOf(" · ");
  if (sep >= 0) display = display.slice(sep + 3).trim();
  if (display) return base + display + ".png";
  return "";
}

function channelLogoUrl(ch) {
  const cur = (ch.logo || "").trim();
  const def = defaultLogoUrl(ch.name, ch.id);
  if (!cur || cur.includes("gitee.com/mytv-android")) return def || cur;
  return cur || def;
}

function onLogoError(img) {
  const id = img.closest(".channel") && img.closest(".channel").dataset.id;
  const ch = state.channels.find((c) => c.id === id);
  if (!ch) return;
  const alt = defaultLogoUrl(ch.name, ch.id);
  if (alt && img.src !== alt) {
    img.onerror = null;
    img.src = alt;
    return;
  }
  img.onerror = null;
  img.replaceWith(Object.assign(document.createElement("span"), {
    className: "ch-logo ch-logo-text",
    textContent: (ch.name || "?").slice(0, 1),
  }));
}

function channelViewerText(id) {
  if (!state.channelCounts) return "";
  const n = state.channelCounts[id];
  if (typeof n !== "number" || n <= 0) return "";
  return String(n);
}

function renderChannelViewerCounts() {
  if (!els.list) return;
  for (const btn of els.list.querySelectorAll(".channel")) {
    const text = channelViewerText(btn.dataset.id);
    let el = btn.querySelector(".ch-viewers");
    if (!text) {
      if (el) el.remove();
      continue;
    }
    if (!el) {
      el = document.createElement("span");
      el.className = "ch-viewers";
      el.title = "本频道在线人数";
      btn.appendChild(el);
    }
    if (el.textContent !== text) el.textContent = text;
  }
}

function renderChannelIcon(ch) {
  const url = channelLogoUrl(ch);
  if (!url) {
    return (
      '<span class="ch-logo ch-logo-text">' +
      escapeHtml((ch.name || "?").slice(0, 1)) +
      "</span>"
    );
  }
  return (
    '<img class="ch-logo" src="' +
    escapeAttr(url) +
    '" alt="" loading="lazy" onerror="onLogoError(this)" />'
  );
}

function renderChannels(channels) {
  els.list.innerHTML = "";
  const filtered = channels.slice().sort(sortChannels);
  const groups = buildDisplayGroups(filtered);
  const order = (state.config && state.config.groupOrder) || [];

  const sortedGroupNames = [...groups.keys()].sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    const ra = ia >= 0 ? ia : 999;
    const rb = ib >= 0 ? ib : 999;
    if (ra !== rb) return ra - rb;
    return a.localeCompare(b, "zh-CN");
  });

  for (const group of sortedGroupNames) {
    const items = groups.get(group) || [];
    if (!items.length) continue;

    const collapsed = state.collapsedGroups.has(group);
    const head = document.createElement("button");
    head.type = "button";
    head.className = "channel-group-label" + (collapsed ? " collapsed" : "");
    head.innerHTML =
      '<span class="group-chevron">' +
      (collapsed ? "▸" : "▾") +
      "</span><span>" +
      escapeHtml(group) +
      "</span><span class=\"group-count\">" +
      items.length +
      "</span>";
    head.addEventListener("click", () => {
      if (state.collapsedGroups.has(group)) state.collapsedGroups.delete(group);
      else state.collapsedGroups.add(group);
      saveJSON(LS_COLLAPSED_GROUPS, [...state.collapsedGroups]);
      renderChannels(getFilteredChannels());
    });
    els.list.appendChild(head);

    const body = document.createElement("div");
    body.className = "channel-group-body" + (collapsed ? " hidden" : "");
    for (const ch of items) {
      const btn = document.createElement("button");
      btn.className = "channel" + (ch.id === state.activeId ? " active" : "");
      btn.dataset.id = ch.id;
      const viewers = channelViewerText(ch.id);
      btn.innerHTML =
        renderChannelIcon(ch) +
        '<span class="name"></span>' +
        (state.favorites.has(ch.id) ? '<span class="fav-mark">★</span>' : "") +
        (viewers
          ? '<span class="ch-viewers" title="本频道在线人数">' + viewers + "</span>"
          : "");
      btn.querySelector(".name").textContent = ch.name;
      btn.addEventListener("click", () => {
        playChannel(ch.id);
        if (window.matchMedia("(max-width: 720px)").matches) {
          setSidebarCollapsed(true);
        }
      });
      body.appendChild(btn);
    }
    els.list.appendChild(body);
  }
}

function getFilteredChannels() {
  const q = state.filterQuery.trim().toLowerCase();
  if (!q) return state.channels;
  return state.channels.filter(
    (c) => c.name.toLowerCase().includes(q) || (c.group || "").toLowerCase().includes(q)
  );
}

function toggleFavorite(id) {
  if (state.favorites.has(id)) state.favorites.delete(id);
  else state.favorites.add(id);
  saveJSON(LS_FAV, [...state.favorites]);
  updateFavButton();
  renderChannels(getFilteredChannels());
}

function updateFavButton() {
  if (!els.toggleFav) return;
  if (!state.activeId) {
    els.toggleFav.hidden = true;
    return;
  }
  els.toggleFav.hidden = false;
  const on = state.favorites.has(state.activeId);
  els.toggleFav.textContent = on ? "★" : "☆";
  els.toggleFav.classList.toggle("on", on);
  els.toggleFav.setAttribute("aria-label", on ? "取消收藏" : "收藏频道");
}

function pushRecent(id) {
  state.recent = [id, ...state.recent.filter((x) => x !== id)].slice(0, MAX_RECENT);
  saveJSON(LS_RECENT, state.recent);
}

function markActive(id) {
  const next = id || null;
  const changed = next !== state.activeId;
  state.activeId = next;
  for (const btn of els.list.querySelectorAll(".channel")) {
    btn.classList.toggle("active", btn.dataset.id === id);
  }
  updateFavButton();
  if (!changed) return;
  // Drop the previous channel's numerator until the new stream reports it.
  // Clearing activeId (playback stopped) reconnects without ?channel= so this
  // viewer leaves that channel's count.
  state.channelCount = null;
  renderOnlineCount();
  connectPresence();
}

// Header shows download speed, not the lifetime total in state.bytes.
// Samples cover about a second; the DOM is rewritten on that same cadence
// so a fragment burst does not flicker the figures.
const RATE_WINDOW_MS = 1000;
let rateSamples = { http: [], p2p: [] };
let rateSeen = { http: 0, p2p: 0 };

function resetStats() {
  state.bytes.http = 0;
  state.bytes.p2p = 0;
  state.peers.clear();
  clearRateWindow();
  updateStats();
  paintRates(performance.now());
}

function clearRateWindow() {
  rateSamples.http.length = 0;
  rateSamples.p2p.length = 0;
  rateSeen.http = 0;
  rateSeen.p2p = 0;
}

function noteRateSamples(now) {
  const httpDelta = state.bytes.http - rateSeen.http;
  const p2pDelta = state.bytes.p2p - rateSeen.p2p;
  rateSeen.http = state.bytes.http;
  rateSeen.p2p = state.bytes.p2p;
  if (httpDelta > 0) rateSamples.http.push({ t: now, n: httpDelta });
  if (p2pDelta > 0) rateSamples.p2p.push({ t: now, n: p2pDelta });
  pruneRateSamples(rateSamples.http, now);
  pruneRateSamples(rateSamples.p2p, now);
}

function pruneRateSamples(samples, now) {
  const cutoff = now - RATE_WINDOW_MS;
  let drop = 0;
  while (drop < samples.length && samples[drop].t < cutoff) drop++;
  if (drop > 0) samples.splice(0, drop);
}

function bytesInRateWindow(samples, now) {
  pruneRateSamples(samples, now);
  let total = 0;
  for (let i = 0; i < samples.length; i++) total += samples[i].n;
  return total;
}

function formatRate(bytesPerSec) {
  if (bytesPerSec >= 1048576) {
    return (bytesPerSec / 1048576).toFixed(1) + " MB/s";
  }
  const text = (bytesPerSec / 1024).toFixed(1);
  if (text === "0.0") return "0 KB/s";
  return text + " KB/s";
}

function paintRates(now) {
  // Window is one second, so bytes inside it are already bytes/sec.
  els.statHttp.textContent = formatRate(bytesInRateWindow(rateSamples.http, now));
  els.statP2p.textContent = formatRate(bytesInRateWindow(rateSamples.p2p, now));
}

function updateStats() {
  noteRateSamples(performance.now());
  els.statPeers.textContent = String(state.peers.size);
}

function startRateTicker() {
  if (startRateTicker.started) return;
  startRateTicker.started = true;
  setInterval(function () {
    paintRates(performance.now());
  }, RATE_WINDOW_MS);
}

function showPlayError(msg) {
  if (!els.playError) return;
  els.playErrorMsg.textContent = msg;
  els.playError.classList.remove("hidden");
}

function hidePlayError() {
  if (els.playError) els.playError.classList.add("hidden");
}

function isPlayAbortError(err) {
  return !!err && (err.name === "AbortError" || err.code === 20);
}

// Browsers reject play() when a new load starts before the prior promise settles
// (common with hls.js attach/load racing ArtPlayer autoplay). Ignore that case.
function safePlay(target) {
  const node =
    target && target.video instanceof HTMLMediaElement
      ? target.video
      : target instanceof HTMLMediaElement
        ? target
        : target && typeof target.play === "function"
          ? target
          : null;
  if (!node || typeof node.play !== "function") return null;
  const p = node.play();
  if (!p || typeof p.catch !== "function") return p;
  return p.catch(function (err) {
    if (isPlayAbortError(err)) return undefined;
    throw err;
  });
}

function installPlayAbortGuard() {
  if (installPlayAbortGuard._done) return;
  installPlayAbortGuard._done = true;
  window.addEventListener("unhandledrejection", function (ev) {
    if (isPlayAbortError(ev.reason)) ev.preventDefault();
  });
}

function playChannel(id) {
  const ch = channelById(id);
  if (!ch) return;

  hidePlayError();
  showAudioOnly(false);
  markActive(id);
  pushRecent(id);
  trackChannel(ch);
  history.replaceState(null, "", "#" + encodeURIComponent(id));
  els.nowPlaying.textContent = ch.name;
  els.placeholder.classList.add("hidden");
  resetStats();

  if (state.art) {
    state.art.destroy(false);
    state.art = null;
  }

  const sourceOpts = buildSourceMenuOptions(ch);
  const qualityOpt = buildArtQualityOptions(ch);
  const playUrl = sourceOpts ? initialSourceUrl(ch, sourceOpts) : initialPlaybackUrl(ch, qualityOpt);
  showAudioOnly(audioOnlyForUrl(ch, playUrl));

  const artOpts = {
    container: "#player",
    url: playUrl,
    type: "m3u8",
    isLive: true,
    // hls.js attaches after ArtPlayer init; native autoplay races loadSource and
    // surfaces as AbortError. We start playback on MANIFEST_PARSED instead.
    autoplay: false,
    muted: true,
    autoSize: false,
    fullscreen: true,
    fullscreenWeb: true,
    setting: true,
    playbackRate: false,
    pip: true,
    theme: "#3ea6ff",
    moreVideoAttr: { playsInline: true, "webkit-playsinline": true },
    customType: {
      // The level menu is about picking a rendition of one stream; the source menu
      // is about picking which line to play. Having a source menu is no reason to
      // hide the quality one — CCTV channels have both now. qualityOpt is the other
      // way of doing quality (a URL per rendition), so only one of those shows.
      m3u8: (video, url, art) => attachHls(video, url, art, ch, !qualityOpt),
    },
  };
  if (!sourceOpts && qualityOpt) artOpts.quality = qualityOpt;

  state.art = new Artplayer(artOpts);

  // Expose alternates immediately. The official default may fail before the
  // player reaches "ready"; users still need to be able to select a verified
  // best-fan source in that case.
  if (sourceOpts) setupSourceMenu(state.art, sourceOpts, ch.id, ch);

  state.art.on("ready", () => {
    if (sourceOpts) setupSourceMenu(state.art, sourceOpts, ch.id, ch);
  });

  state.art.on("quality", (item) => {
    if (item && item.html) setQualityPref(ch.id, item.html);
  });

  state.art.on("video:error", () => {
    showPlayError("视频解码失败，请尝试其他频道或稍后重试。");
  });

  renderChannels(getFilteredChannels());
}

function allSourceUrls(ch) {
  const out = [];
  if (ch.url) out.push({ label: "默认", url: ch.url });
  for (const s of ch.sources || []) {
    if (s.url) out.push({ label: s.label || "mirror", url: s.url });
  }
  return out;
}

function switchToNextSource(art, ch, currentUrl) {
  const sources = allSourceUrls(ch);
  if (sources.length < 2) return null;
  const idx = sources.findIndex((s) => s.url === currentUrl);
  const next = sources[(idx + 1 + sources.length) % sources.length];
  if (!next || next.url === currentUrl) return null;
  art._playUrl = next.url;
  const wasPlaying = art.video && !art.video.paused;
  if (art.hls) {
    art.hls.loadSource(next.url);
    art.hls.startLoad(-1);
    if (wasPlaying) safePlay(art);
  } else if (typeof art.switchUrl === "function") {
    art.switchUrl(next.url);
  }
  setSourcePref(ch.id, next.label);
  if (art.notice && typeof art.notice.show === "string") {
    art.notice.show = "已切换线路: " + next.label;
  }
  return next;
}

function isAdSegmentUrl(url) {
  if (!url) return false;
  try {
    const path = new URL(url, location.origin).pathname.toLowerCase();
    const parts = path.split("/").filter(Boolean);
    for (const p of parts) {
      if (
        p === "ad" ||
        p === "ads" ||
        p === "adv" ||
        p === "gg" ||
        p === "guanggao" ||
        p === "advert" ||
        p === "adjump" ||
        p === "adbreak" ||
        p === "commercial"
      ) {
        return true;
      }
      if (p.includes("guanggao") || p.includes("advert") || p.includes("adsegment")) {
        return true;
      }
    }
    if (path.includes("/adjump/")) return true;
  } catch (e) {}
  return false;
}

function skipAdFragment(video, hls, frag) {
  if (!video || !frag) return;
  const skip = Math.max(0.5, frag.duration || 5);
  video.currentTime = video.currentTime + skip;
  if (hls && typeof hls.startLoad === "function") {
    hls.startLoad(-1);
  }
}

function wireAdSkip(video, hls) {
  if (!video || !hls || typeof Hls === "undefined") return;
  let avgDur = 10;
  hls.on(Hls.Events.FRAG_CHANGED, (_e, data) => {
    const frag = data && data.frag;
    if (!frag) return;
    if (frag.duration > 0) {
      avgDur = avgDur * 0.85 + frag.duration * 0.15;
    }
    if (isAdSegmentUrl(frag.url || frag.relurl)) {
      skipAdFragment(video, hls, frag);
      return;
    }
    const tags = frag.tagList || [];
    const hasDisc = tags.some(function (t) {
      return t && String(t[0]).toUpperCase() === "DISCONTINUITY";
    });
    if (hasDisc && frag.duration > 0 && frag.duration <= 15 && frag.duration < avgDur * 0.65) {
      skipAdFragment(video, hls, frag);
    }
  });
}

function isSameOriginPlaybackURL(url) {
  if (!url || typeof url !== "string") return false;
  if (url.startsWith("/")) return true;
  try {
    const u = new URL(url, location.href);
    return u.origin === location.origin;
  } catch (_) {
    return false;
  }
}

function buildHlsConfig(url) {
  // Same-origin manifests (/cntv/, /s/) must not use hls.js workers: workers
  // run in a separate context and do not carry Cloudflare clearance cookies,
  // so Bot Fight / challenge pages block segment and playlist fetches.
  const sameOrigin = isSameOriginPlaybackURL(url);
  // IPTV mirrors are standard HLS, not LL-HLS — prefer stable buffering over low latency.
  return {
    lowLatencyMode: false,
    enableWorker: !sameOrigin,
    backBufferLength: 30,
    maxBufferLength: 45,
    maxMaxBufferLength: 90,
    liveSyncDurationCount: 3,
    liveMaxLatencyDurationCount: 12,
    // 直播中国等海外慢 CDN 常见：10s 分片要拉 20–40s，25s 超时会误报「播放失败」。
    manifestLoadingTimeOut: 30000,
    levelLoadingTimeOut: 30000,
    fragLoadingTimeOut: 60000,
    fragLoadingMaxRetry: 8,
    fragLoadingRetryDelay: 800,
    levelLoadingMaxRetry: 4,
    manifestLoadingMaxRetry: 4,
    startFragPrefetch: true,
    capLevelToPlayerSize: true,
  };
}

function handleHlsFatalError(hls, art, channel, playUrl, data) {
  const detail = (data && data.details) || (data && data.type) || "unknown";
  if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
    if ((art._netRecoveries || 0) < 3) {
      art._netRecoveries = (art._netRecoveries || 0) + 1;
      hls.startLoad(-1);
      return;
    }
    const next = switchToNextSource(art, channel, art._playUrl || playUrl);
    if (next) {
      art._netRecoveries = 0;
      hidePlayError();
      return;
    }
  }
  if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
    if ((art._mediaRecoveries || 0) < 2) {
      art._mediaRecoveries = (art._mediaRecoveries || 0) + 1;
      hls.recoverMediaError();
      return;
    }
  }
  showPlayError("直播加载失败 (" + detail + ")。请换线路或稍后重试。");
  if (art.notice) art.notice.show = "播放失败";
}

// giveUpOnOfficialStream moves off the official CCTV source when its segments
// cannot be decoded. Retrying is pointless — without the plaintext the picture
// is noise — and hls.js would otherwise re-download whole segments for minutes
// before its own retries run out, showing a black screen the whole time.
function giveUpOnOfficialStream(art, channel, playUrl) {
  const next = switchToNextSource(art, channel, art._playUrl || playUrl);
  if (next) {
    art._netRecoveries = 0;
    art._mediaRecoveries = 0;
    hidePlayError();
    return;
  }
  showPlayError("央视源当前无法解码，暂无可用备用线路。请稍后重试或换个频道。");
}

function wireStallRecovery(video, hls, art, channel, playUrl) {
  let stallTimer = null;
  let stallCount = 0;

  const clearStallTimer = () => {
    if (stallTimer) clearTimeout(stallTimer);
    stallTimer = null;
  };

  const onWaiting = () => {
    clearStallTimer();
    stallTimer = setTimeout(() => {
      if (!video.paused && video.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) {
        return;
      }
      stallCount += 1;
      if (stallCount <= 2) {
        hls.startLoad(-1);
        safePlay(art);
        return;
      }
      const next = switchToNextSource(art, channel, art._playUrl || playUrl);
      if (next) {
        stallCount = 0;
        hidePlayError();
        return;
      }
      hls.startLoad(-1);
      safePlay(art);
    }, 3500);
  };

  const onPlaying = () => {
    stallCount = 0;
    art._netRecoveries = 0;
    art._mediaRecoveries = 0;
    clearStallTimer();
  };

  video.addEventListener("waiting", onWaiting);
  video.addEventListener("playing", onPlaying);
  video.addEventListener("canplay", onPlaying);
  art.on("destroy", clearStallTimer);
}

function attachHls(video, url, art, channel, enableLevelMenu) {
  if (art.hls) {
    art.hls.destroy();
    art.hls = null;
  }

  art._playUrl = url;
  showAudioOnly(audioOnlyForUrl(channel, url));
  art._netRecoveries = 0;
  art._mediaRecoveries = 0;
  const hlsConfig = buildHlsConfig(url);

  if (typeof Hls !== "undefined" && Hls.isSupported()) {
    let hls;
    if (p2pEffective()) {
      const { HlsJsP2PEngine } = window.p2pml.hlsjs;
      const HlsWithP2P = HlsJsP2PEngine.injectMixin(window.Hls);
      const core = { swarmId: channel.swarmId || channel.id };
      const announce = (state.config.p2p && state.config.p2p.announce) || [];
      if (announce.length) core.announceTrackers = announce;
      hls = new HlsWithP2P({
        ...hlsConfig,
        p2p: {
          core,
          onHlsJsCreated: (h) => wireP2PEngine(h.p2pEngine),
        },
      });
    } else {
      hls = new Hls(hlsConfig);
    }

    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (!data) return;
      if (!data.fatal) return;
      handleHlsFatalError(hls, art, channel, url, data);
    });

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      hidePlayError();
      safePlay(art);
      if (buildSourceMenuOptions(channel)) {
        probeChannelSources(channel, art);
      }
      if (enableLevelMenu) {
        const addMenu = () => setupHlsQualityMenu(art, hls, channel.id);
        if (art.isReady) addMenu();
        else art.on("ready", addMenu);
      }
    });

    // Official CCTV streams are ChinaDRM scrambled. The player fetches segments
    // from the CDN as usual and asks our patch endpoint for the plaintext of the
    // encrypted bytes; without this they decode to grey noise.
    if (
      state.config &&
      state.config.cntvPatch &&
      window.cntvPatch &&
      typeof window.cntvPatch.installPatchLoader === "function"
    ) {
      art._patchFailures = 0;
      // Ask for the current URL each time rather than capturing this one: a
      // fallback swaps the source on this same instance, and the mirror's
      // segments must not be sent to the patch endpoint.
      window.cntvPatch.installPatchLoader(hls, () => art._playUrl || url, {
        onPatched: () => {
          art._patchFailures = 0;
        },
        onPatchFailed: () => {
          art._patchFailures = (art._patchFailures || 0) + 1;
          if (art._patchFailures < MAX_PATCH_FAILURES) return;
          art._patchFailures = 0;
          giveUpOnOfficialStream(art, channel, url);
        },
      });
    }

    hls.on(Hls.Events.MEDIA_ATTACHED, () => {
      hls.loadSource(url);
    });
    hls.attachMedia(video);
    wireStallRecovery(video, hls, art, channel, url);
    wireAdSkip(video, hls);
    art.hls = hls;
    art.on("destroy", () => hls.destroy());
    return;
  }

  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
    video.addEventListener("error", () => {
      showPlayError("直播加载失败，请换频道或稍后重试。");
    });
    return;
  }

  showPlayError("当前浏览器不支持 HLS 播放。");
}

function wireP2PEngine(engine) {
  if (!engine || typeof engine.addEventListener !== "function") return;
  engine.addEventListener("onPeerConnect", (p) => {
    if (p && p.peerId) state.peers.add(p.peerId);
    updateStats();
  });
  engine.addEventListener("onPeerClose", (p) => {
    if (p && p.peerId) state.peers.delete(p.peerId);
    updateStats();
  });
  engine.addEventListener("onChunkDownloaded", (bytes, source) => {
    if (source === "p2p") state.bytes.p2p += bytes;
    else state.bytes.http += bytes;
    updateStats();
  });
  engine.addEventListener("onChunkUploaded", (bytes) => {
    state.bytes.p2p += bytes;
    updateStats();
  });
}

async function boot() {
  loadLocalState();
  installPlayAbortGuard();
  initSidebarToggle();
  startRateTicker();

  if (els.toggleFav) {
    els.toggleFav.addEventListener("click", () => {
      if (state.activeId) toggleFavorite(state.activeId);
    });
  }
  if (els.playErrorRetry) {
    els.playErrorRetry.addEventListener("click", () => {
      if (state.activeId) playChannel(state.activeId);
    });
  }

  try {
    const res = await fetch("/api/config", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    state.config = await res.json();
  } catch (err) {
    els.list.innerHTML =
      '<div class="load-error">加载配置失败: ' + escapeHtml(String(err)) + "</div>";
    return;
  }

  state.channels = state.config.channels || [];
  document.title = state.config.title || "xixitv.live";
  els.siteTitle.textContent = state.config.title || "xixitv.live";

  updateP2PBadge();
  bindP2pToggles();

  setupAnalytics(state.config.analytics);
  renderAds(state.config.ads);
  initPresence();
  renderChannels(getFilteredChannels());

  els.search.addEventListener("input", () => {
    state.filterQuery = els.search.value;
    renderChannels(getFilteredChannels());
  });

  const hashId = decodeURIComponent(location.hash.replace(/^#/, ""));
  const fromHash = channelById(hashId);
  if (fromHash) playChannel(fromHash.id);
}

boot();
