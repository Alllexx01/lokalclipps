const form = document.querySelector('#clipForm');
const fileInput = document.querySelector('#video');
const fileLabel = document.querySelector('#fileLabel');
const urlInput = document.querySelector('#videoUrl');
const dropzone = document.querySelector('#dropzone');
const setupPanel = document.querySelector('#setupPanel');
const progressPanel = document.querySelector('#progressPanel');
const resultsPanel = document.querySelector('#resultsPanel');
const progressBar = document.querySelector('#progressBar');
const progressPercent = document.querySelector('#progressPercent');
const progressMessage = document.querySelector('#progressMessage');
const clipGrid = document.querySelector('#clipGrid');
const potentialCard = document.querySelector('#potentialCard');
const finderQuery = document.querySelector('#finderQuery');
const finderCategory = document.querySelector('#finderCategory');
const finderSearch = document.querySelector('#finderSearch');
const finderStatus = document.querySelector('#finderStatus');
const finderResults = document.querySelector('#finderResults');
const finderLength = document.querySelector('#finderLength');
const finderSort = document.querySelector('#finderSort');
const finderBar = document.querySelector('#finderBar');
const finderSelected = document.querySelector('#finderSelected');
const finderQueue = document.querySelector('#finderQueue');
const finderMore = document.querySelector('#finderMore');
const videoTitle = document.querySelector('#videoTitle');
const finderSelection = new Map();
let finderLimit = 12;
let recentTimer = null;
const recentPanel = document.querySelector('#recentPanel');
const recentGrid = document.querySelector('#recentGrid');
const retentionDays = document.querySelector('#retentionDays');
const retentionLabel = document.querySelector('#retentionLabel');
const retentionState = document.querySelector('#retentionState');
const captionPreview = document.querySelector('#captionPreview');
const captionStyle = document.querySelector('#captionStyle');
const captionPosition = document.querySelector('#captionPosition');
const captionSize = document.querySelector('#captionSize');
const captionColor = document.querySelector('#captionColor');
const highlightColor = document.querySelector('#highlightColor');
const captionDialog = document.querySelector('#captionDialog');
const captionEnabled = document.querySelector('#captionEnabled');
const renderStatus = document.querySelector('#renderStatus');
const renderCaptionButton = document.querySelector('#renderCaptionButton');
const tiktokButton = document.querySelector('#tiktokButton');
const tiktokDot = document.querySelector('#tiktokDot');
const tiktokSettingsDialog = document.querySelector('#tiktokSettingsDialog');
const tiktokPostDialog = document.querySelector('#tiktokPostDialog');
const tiktokSetupStatus = document.querySelector('#tiktokSetupStatus');
const tiktokPostStatus = document.querySelector('#tiktokPostStatus');
let currentJobId = null;
let currentClipIndex = null;
let currentClips = [];
let recentJobs = new Map();
let tiktokState = {configured:false, connected:false};
let activeTikTokClip = null;
const shareFileCache = new Map();

async function loadRecentJobs() {
  clearTimeout(recentTimer);
  try {
    const response = await fetch('/api/jobs');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Projekte konnten nicht geladen werden');
    retentionDays.value = data.retention_days;
    retentionLabel.textContent = `${data.retention_days} ${data.retention_days === 1 ? 'Tag' : 'Tage'}`;
    const finished = data.jobs.filter(job => job.status === 'done' && job.clips?.length);
    const active = data.jobs.filter(job => ['waiting_for_pc', 'helper_downloading', 'queued', 'running'].includes(job.status));
    recentJobs = new Map(finished.map(job => [job.id, job]));
    if (!setupPanel.classList.contains('hidden')) recentPanel.classList.remove('hidden');
    if (!finished.length && !active.length) {
      recentGrid.innerHTML = '<p class="recent-empty">Noch keine fertigen Projekte. Dein nächster Auftrag erscheint automatisch hier.</p>';
      return;
    }
    const activeCards = active.map(job => `<article class="recent-progress"><h3>${escapeHtml(job.name || 'Video-Projekt')}</h3><div class="recent-meta"><span>${job.status === 'waiting_for_pc' ? 'Wartet auf PC' : job.status === 'helper_downloading' ? 'PC lädt Video' : job.status === 'queued' ? 'Wartet' : 'Wird verarbeitet'}</span><span>${Number(job.progress || 0)} %</span></div><div class="recent-empty" style="padding:0">${escapeHtml(job.message || '')}</div><div class="bar"><i style="width:${Number(job.progress || 0)}%"></i></div></article>`);
    const finishedCards = finished.map(job => {
      const date = new Date((job.updated_at || job.created_at) * 1000).toLocaleString('de-DE', {dateStyle:'medium', timeStyle:'short'});
      return `<article class="recent-card"><h3>${escapeHtml(job.name || 'Video-Projekt')}</h3><div class="recent-meta"><span>${job.clips.length} Clips</span><span>${date}</span></div><button class="recent-open" data-job-id="${job.id}">Projekt öffnen</button></article>`;
    });
    recentGrid.innerHTML = [...activeCards, ...finishedCards].join('');
    document.querySelectorAll('.recent-open').forEach(button => button.addEventListener('click', () => {
      const job = recentJobs.get(button.dataset.jobId);
      if (!job) return;
      setupPanel.classList.add('hidden');
      recentPanel.classList.add('hidden');
      progressPanel.classList.add('hidden');
      showResults(job.clips, job.id, job.analysis);
      resultsPanel.scrollIntoView({behavior:'smooth'});
    }));
    if (active.length) recentTimer = setTimeout(loadRecentJobs, 6000);
  } catch (error) {
    if (!setupPanel.classList.contains('hidden')) recentPanel.classList.remove('hidden');
    recentGrid.innerHTML = `<p class="recent-empty">${escapeHtml(error.message)}</p>`;
  }
}

document.querySelector('#saveRetention').addEventListener('click', async () => {
  retentionState.textContent = 'Speichere …';
  try {
    const response = await fetch('/api/settings', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({retention_days:Number(retentionDays.value)})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Speichern fehlgeschlagen');
    retentionDays.value = data.retention_days;
    retentionLabel.textContent = `${data.retention_days} ${data.retention_days === 1 ? 'Tag' : 'Tage'}`;
    retentionState.textContent = 'Gespeichert';
    setTimeout(() => retentionState.textContent = '', 2200);
    loadRecentJobs();
  } catch (error) { retentionState.textContent = error.message; }
});

function updateCaptionPreview() {
  const positions = {top:'flex-start', middle:'center', bottom:'flex-end'};
  captionPreview.style.alignItems = positions[captionPosition.value];
  const text = captionPreview.querySelector('span');
  text.style.fontSize = `${Math.max(13, Number(captionSize.value) / 4)}px`;
  text.style.color = captionColor.value;
  captionPreview.querySelector('em').style.color = highlightColor.value;
  text.style.background = captionStyle.value === 'boxed' ? 'rgba(0,0,0,.82)' : 'transparent';
  text.style.padding = captionStyle.value === 'boxed' ? '7px 10px' : '0';
  text.style.textShadow = captionStyle.value === 'minimal' ? '0 1px 3px #000' : '0 2px 0 #000,2px 0 0 #000,-2px 0 0 #000';
}
[captionStyle, captionPosition, captionSize, captionColor, highlightColor].forEach(control => control.addEventListener('input', updateCaptionPreview));
captionEnabled.addEventListener('change', () => { captionPreview.style.opacity = captionEnabled.checked ? '1' : '.35'; });
updateCaptionPreview();

function updateFinderBar() {
  const count = finderSelection.size;
  finderSelected.textContent = count ? `${count} ${count === 1 ? 'Video' : 'Videos'} ausgewählt` : 'Nichts ausgewählt';
  finderQueue.disabled = count === 0;
  finderQueue.textContent = count > 1 ? `${count} Videos nacheinander clippen` : 'Ausgewähltes Video clippen';
}

async function runFinder(limit) {
  finderLimit = limit;
  finderSearch.disabled = true;
  finderMore.disabled = true;
  finderStatus.textContent = 'Öffentliche Videos werden durchsucht und bewertet …';
  if (limit <= 12) finderSelection.clear();
  try {
    const [minMinutes, maxMinutes] = (finderLength.value || '0-0').split('-').map(Number);
    const params = new URLSearchParams({
      q: finderQuery.value.trim(), category: finderCategory.value, limit: String(limit),
      sort: finderSort.value, min_minutes: String(minMinutes), max_minutes: String(maxMinutes)
    });
    const response = await fetch(`/api/discover?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Videosuche fehlgeschlagen');
    const results = data.results || [];
    finderStatus.textContent = results.length ? `${results.length} Vorschläge gefunden. Du kannst mehrere auswählen und nacheinander clippen lassen.` : 'Keine passenden Vorschläge gefunden. Versuche einen anderen Suchbegriff oder eine andere Länge.';
    finderResults.innerHTML = results.map(video => {
      const minutes = video.duration ? `${Math.floor(video.duration / 60)}:${String(video.duration % 60).padStart(2, '0')} Min.` : 'Länge unbekannt';
      const views = video.views ? `${new Intl.NumberFormat('de-DE', {notation:'compact'}).format(video.views)} Aufrufe` : 'Aufrufe unbekannt';
      const days = Number.isFinite(Number(video.age_days)) && video.age_days !== null ? Number(video.age_days) : null;
      const age = days === null ? 'Alter unbekannt' : days < 1 ? 'heute' : days < 7 ? `vor ${days} ${days === 1 ? 'Tag' : 'Tagen'}` : days < 60 ? `vor ${Math.floor(days / 7)} ${Math.floor(days / 7) === 1 ? 'Woche' : 'Wochen'}` : days < 730 ? `vor ${Math.floor(days / 30)} Monaten` : `vor ${Math.floor(days / 365)} Jahren`;
      const attributes = `data-url="${escapeAttribute(video.url)}" data-title="${escapeAttribute(video.title)}"`;
      return `<article class="finder-card${video.already_used ? ' used' : ''}">
        ${video.already_used ? `<span class="finder-used${video.thumbnail ? '' : ' inline'}">Schon geclippt</span>` : ''}
        ${video.thumbnail ? `<img loading="lazy" src="${escapeAttribute(video.thumbnail)}" alt="">` : ''}
        <div class="finder-card-body"><h3>${escapeHtml(video.title)}</h3>
        <div class="finder-card-meta">${escapeHtml(video.channel)} · ${minutes} · ${views} · ${age}</div>
        <div class="finder-score">Finder-Score ${Number(video.score || 0)}</div>
        <p class="finder-reasons">${(video.reasons || []).map(escapeHtml).join(' · ')}</p>
        <div class="finder-actions">
          <label class="finder-pick"><input type="checkbox" class="finder-check" ${attributes} ${finderSelection.has(video.url) ? 'checked' : ''}> Auswählen</label>
          <a class="finder-watch" href="${escapeAttribute(video.url)}" target="_blank" rel="noopener noreferrer">Ansehen ↗</a>
        </div>
        <button class="finder-use" type="button" ${attributes} style="margin-top:8px">Dieses Video clippen</button></div>
      </article>`;
    }).join('');
    document.querySelectorAll('.finder-check').forEach(box => box.addEventListener('change', () => {
      if (box.checked) finderSelection.set(box.dataset.url, box.dataset.title);
      else finderSelection.delete(box.dataset.url);
      updateFinderBar();
    }));
    document.querySelectorAll('.finder-use').forEach(button => button.addEventListener('click', () => {
      urlInput.value = button.dataset.url;
      videoTitle.value = button.dataset.title || '';
      fileInput.value = '';
      fileLabel.textContent = 'Video hier ablegen oder auswählen';
      finderStatus.textContent = 'Video übernommen. Prüfe die Einstellungen und starte „Clips erstellen“.';
      setupPanel.scrollIntoView({behavior:'smooth', block:'start'});
    }));
    finderBar.classList.toggle('hidden', !results.length);
    finderMore.disabled = results.length < limit || limit >= 36;
    updateFinderBar();
  } catch (error) {
    finderStatus.textContent = error.message;
  } finally {
    finderSearch.disabled = false;
  }
}

finderSearch.addEventListener('click', () => runFinder(12));
finderMore.addEventListener('click', () => runFinder(Math.min(36, finderLimit + 12)));

finderQueue.addEventListener('click', async () => {
  const items = [...finderSelection.entries()];
  if (!items.length) return;
  finderQueue.disabled = true;
  let started = 0;
  let lastError = '';
  for (const [index, [url, title]] of items.entries()) {
    finderStatus.textContent = `Auftrag ${index + 1} von ${items.length} wird gestartet …`;
    const body = new FormData(form);
    body.delete('video');
    body.set('url', url);
    body.set('title', title);
    try {
      const response = await fetch('/api/jobs', {method:'POST', body});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Auftrag konnte nicht gestartet werden');
      started += 1;
    } catch (error) { lastError = error.message; }
  }
  finderSelection.clear();
  document.querySelectorAll('.finder-check').forEach(box => { box.checked = false; });
  updateFinderBar();
  finderStatus.textContent = started
    ? `${started} ${started === 1 ? 'Auftrag läuft' : 'Aufträge laufen'} nacheinander. Fertige Projekte erscheinen unten unter „Letzte Projekte“.${lastError ? ` Fehler bei einem Video: ${lastError}` : ''}`
    : lastError;
  recentPanel.classList.remove('hidden');
  loadRecentJobs();
  recentPanel.scrollIntoView({behavior:'smooth', block:'start'});
});

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) {
    fileLabel.textContent = fileInput.files[0].name;
    urlInput.value = '';
    videoTitle.value = '';
  }
});
urlInput.addEventListener('input', () => {
  videoTitle.value = '';
  if (urlInput.value.trim()) {
    fileInput.value = '';
    fileLabel.textContent = 'Video hier ablegen oder auswählen';
  }
});
['dragenter','dragover'].forEach(name => dropzone.addEventListener(name, e => { e.preventDefault(); dropzone.classList.add('dragging'); }));
['dragleave','drop'].forEach(name => dropzone.addEventListener(name, e => { e.preventDefault(); dropzone.classList.remove('dragging'); }));
dropzone.addEventListener('drop', e => {
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    fileLabel.textContent = e.dataTransfer.files[0].name;
    urlInput.value = '';
  }
});

form.addEventListener('submit', async e => {
  e.preventDefault();
  if (!fileInput.files[0] && !urlInput.value.trim()) {
    alert('Bitte ein Video auswählen oder eine Video-URL eingeben.');
    return;
  }
  setupPanel.classList.add('hidden');
  recentPanel.classList.add('hidden');
  resultsPanel.classList.add('hidden');
  progressPanel.classList.remove('hidden');
  progressMessage.classList.remove('error');
  progressMessage.textContent = fileInput.files[0] ? 'Video wird hochgeladen …' : 'Video wird von der URL geladen …';
  try {
    const response = await fetch('/api/jobs', {method:'POST', body:new FormData(form)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Upload fehlgeschlagen');
    poll(data.job_id);
  } catch (error) { showError(error.message); }
});

async function poll(id) {
  try {
    const response = await fetch(`/api/jobs/${id}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || 'Status konnte nicht geladen werden');
    progressBar.style.width = `${job.progress || 0}%`;
    progressPercent.textContent = `${job.progress || 0} %`;
    progressMessage.textContent = job.message || 'Verarbeitung läuft';
    if (job.status === 'done') {
      showResults(job.clips, id, job.analysis);
      loadRecentJobs();
      return;
    }
    if (job.status === 'error') throw new Error(job.error || job.message);
    setTimeout(() => poll(id), 1200);
  } catch (error) { showError(error.message); }
}

function clipBadges(clip) {
  const layouts = {face:'Sprecher-Tracking', center:'Feste Bildmitte', blur:'Unscharfer Hintergrund'};
  return `<span class="badge score">Score ${clip.score}</span><span class="badge">${clip.duration} Sek.</span><span class="badge">ab ${clip.start} Sek.</span>`
    + (layouts[clip.layout] ? `<span class="badge">${layouts[clip.layout]}</span>` : '')
    + (clip.tightened > 0 ? `<span class="badge">−${clip.tightened} Sek. Pausen</span>` : '')
    + (clip.hook ? '<span class="badge">Titel</span>' : '')
    + (clip.zoom === 'subtle' ? '<span class="badge">Zoom: dezent</span>' : clip.zoom === 'punch' ? '<span class="badge">Zoom: betont</span>' : '');
}

function showResults(clips, jobId, analysis = null) {
  currentJobId = jobId;
  currentClips = clips;
  recentPanel.classList.add('hidden');
  progressPanel.classList.add('hidden');
  resultsPanel.classList.remove('hidden');
  if (analysis) {
    const metrics = analysis.metrics || {};
    const strengths = (analysis.strengths || []).map(item => `<span>✓ ${escapeHtml(item)}</span>`).join('');
    const advice = (analysis.recommendations || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
    potentialCard.style.setProperty('--potential', `${Math.max(0, Math.min(100, analysis.score || 0))}%`);
    potentialCard.innerHTML = `
      <div class="potential-score"><span>${Number(analysis.score || 0)}</span></div>
      <div class="potential-copy">
        <h3>${escapeHtml(analysis.label || 'Clip-Potenzial')}</h3>
        <p>${escapeHtml(analysis.verdict || '')}</p>
        <div class="potential-signals">${strengths}<span>${Number(metrics.duration_minutes || 0)} Min.</span><span>${Number(metrics.words_per_minute || 0)} Wörter/Min.</span><span>${Number(metrics.scene_changes || 0)} Bildwechsel</span></div>
        ${advice ? `<ul class="potential-advice">${advice}</ul>` : ''}
      </div>`;
    potentialCard.classList.remove('hidden');
  } else {
    potentialCard.classList.add('hidden');
    potentialCard.innerHTML = '';
  }
  clipGrid.innerHTML = clips.map(clip => `
    <article class="clip-card" data-clip-index="${clip.index}">
      <video controls preload="metadata" src="${clip.url}"></video>
      <div class="clip-info">
        <h3>${escapeHtml(clip.title)}</h3>
        <div class="clip-meta">${clipBadges(clip)}</div>
        <p class="clip-reasons">${(clip.reasons || []).map(escapeHtml).join(' · ')}</p>
        <button class="edit-captions" data-index="${clip.index}">Clip bearbeiten (Schnitt &amp; Untertitel)</button>
        <button class="ideas-clip" data-index="${clip.index}">Bildunterschriften vorschlagen</button>
        <button class="share-clip" data-index="${clip.index}">↗ Teilen / TikTok</button>
        <button class="post-tiktok" data-index="${clip.index}">Auf TikTok posten</button>
        <a class="download" href="${clip.url}" download>MP4 herunterladen</a>
      </div>
    </article>`).join('');
  document.querySelectorAll('.edit-captions').forEach(button => button.addEventListener('click', () => openCaptionEditor(Number(button.dataset.index))));
  document.querySelectorAll('.ideas-clip').forEach(button => button.addEventListener('click', () => openCaptionIdeas(Number(button.dataset.index))));
  document.querySelectorAll('.share-clip').forEach(button => button.addEventListener('click', () => shareClip(clips.find(clip => clip.index === Number(button.dataset.index)), button)));
  document.querySelectorAll('.post-tiktok').forEach(button => button.addEventListener('click', () => openTikTokPost(clips.find(clip => clip.index === Number(button.dataset.index)))));
}

const ideaCache = new Map();

async function fetchCaptionIdeas(index) {
  const key = `${currentJobId}-${index}`;
  if (!ideaCache.has(key)) {
    const response = await fetch(`/api/jobs/${currentJobId}/clips/${index}/captions`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Vorschläge konnten nicht geladen werden');
    ideaCache.set(key, data.variants || []);
  }
  return ideaCache.get(key);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    const helper = document.createElement('textarea');
    helper.value = text;
    document.body.appendChild(helper);
    helper.select();
    document.execCommand('copy');
    helper.remove();
  }
}

async function openCaptionIdeas(index) {
  const dialog = document.querySelector('#ideasDialog');
  const list = document.querySelector('#ideasList');
  list.innerHTML = '<p class="tiktok-status">Wird geladen …</p>';
  dialog.showModal();
  try {
    const variants = await fetchCaptionIdeas(index);
    list.innerHTML = variants.map((variant, position) => `<section class="idea"><h4>${escapeHtml(variant.name)}</h4><textarea data-idea="${position}">${escapeHtml(variant.text)}</textarea><button type="button" data-copy="${position}">Kopieren</button></section>`).join('');
    list.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
      await copyText(list.querySelector(`[data-idea="${button.dataset.copy}"]`).value);
      button.textContent = '✓ Kopiert';
      setTimeout(() => button.textContent = 'Kopieren', 1800);
    }));
  } catch (error) {
    list.innerHTML = `<p class="tiktok-status">${escapeHtml(error.message)}</p>`;
  }
}
document.querySelector('#closeIdeas').addEventListener('click', () => document.querySelector('#ideasDialog').close());
document.querySelector('#ideasDone').addEventListener('click', () => document.querySelector('#ideasDialog').close());

async function shareClip(clip, button) {
  const originalLabel = '↗ Teilen / TikTok';
  button.disabled = true;
  try {
    let file = shareFileCache.get(`${currentJobId}-${clip.index}`);
    if (!file) {
      button.textContent = 'Clip wird vorbereitet …';
      const response = await fetch(clip.url);
      if (!response.ok) throw new Error('Clip konnte nicht geladen werden.');
      const blob = await response.blob();
      file = new File([blob], clip.filename || `lokalclipps-${clip.index}.mp4`, {type:'video/mp4'});
      shareFileCache.set(`${currentJobId}-${clip.index}`, file);
    }
    const shareData = {
      files:[file],
      title:clip.title,
      text:`${clip.title}\n\n#gaming #videospiele #deutschertiktok #shortvideo #lokalclipps`
    };
    if (navigator.share && (!navigator.canShare || navigator.canShare({files:[file]}))) {
      button.textContent = 'Teilen-Menü wird geöffnet …';
      await navigator.share(shareData);
      button.textContent = '✓ Geteilt';
      setTimeout(() => button.textContent = originalLabel, 2200);
    } else {
      const link = document.createElement('a');
      link.href = URL.createObjectURL(file);
      link.download = file.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 30000);
      button.textContent = '✓ MP4 gespeichert – in TikTok auswählen';
      setTimeout(() => button.textContent = originalLabel, 3200);
    }
  } catch (error) {
    if (error.name !== 'AbortError') {
      button.textContent = error.name === 'NotAllowedError' ? 'Nochmal tippen zum Teilen' : error.message;
      setTimeout(() => button.textContent = originalLabel, 3500);
    } else {
      button.textContent = originalLabel;
    }
  } finally {
    button.disabled = false;
  }
}

function openCaptionEditor(index) {
  currentClipIndex = index;
  const clip = currentClips.find(item => item.index === index) || {};
  const options = clip.options || {};
  document.querySelector('#editReframe').value = options.reframe || 'blur';
  document.querySelector('#editTighten').checked = Boolean(options.tighten);
  document.querySelector('#editHook').checked = Boolean(options.hook);
  document.querySelector('#editZoom').value = options.zoom || 'off';
  renderStatus.textContent = '';
  renderCaptionButton.disabled = false;
  renderCaptionButton.textContent = 'Clip neu rendern';
  captionDialog.showModal();
}

function closeCaptionEditor() { captionDialog.close(); }
document.querySelector('#closeCaptionDialog').addEventListener('click', closeCaptionEditor);
document.querySelector('#cancelCaptionEdit').addEventListener('click', closeCaptionEditor);

renderCaptionButton.addEventListener('click', async () => {
  if (!currentJobId || !currentClipIndex) return;
  renderCaptionButton.disabled = true;
  renderCaptionButton.textContent = 'Wird gerendert …';
  renderStatus.textContent = 'Der Clip wird lokal neu erstellt. Das kann kurz dauern.';
  try {
    const response = await fetch(`/api/jobs/${currentJobId}/clips/${currentClipIndex}/render`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        enabled: captionEnabled.checked,
        style: captionStyle.value,
        position: captionPosition.value,
        size: Number(captionSize.value),
        text_color: captionColor.value,
        highlight_color: highlightColor.value,
        reframe: document.querySelector('#editReframe').value,
        tighten: document.querySelector('#editTighten').checked,
        hook: document.querySelector('#editHook').checked,
        zoom: document.querySelector('#editZoom').value
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Rendern fehlgeschlagen');
    const card = document.querySelector(`[data-clip-index="${currentClipIndex}"]`);
    const current = currentClips.find(item => item.index === currentClipIndex);
    if (current && data.clip) {
      Object.assign(current, data.clip);          // Teilen/TikTok greifen danach auf die neue Datei zu
      card.querySelector('.clip-meta').innerHTML = clipBadges(current);
    }
    const video = card.querySelector('video');
    video.src = `${data.url}?v=${Date.now()}`;
    video.load();
    const download = card.querySelector('.download');
    download.href = data.url;
    download.download = data.filename;
    renderStatus.textContent = 'Fertig – der Clip wurde aktualisiert.';
    renderCaptionButton.textContent = 'Erneut rendern';
  } catch (error) {
    renderStatus.textContent = error.message;
    renderCaptionButton.textContent = 'Erneut versuchen';
  } finally {
    renderCaptionButton.disabled = false;
  }
});

async function loadTikTokStatus() {
  try {
    const response = await fetch('/api/tiktok/settings');
    tiktokState = await response.json();
    if (tiktokState.redirect_uri) document.querySelector('#tiktokRedirectUri').value = tiktokState.redirect_uri;
    tiktokDot.classList.toggle('online', Boolean(tiktokState.connected));
    tiktokSetupStatus.innerHTML = tiktokState.connected
      ? '<span class="tiktok-connected">✓ TikTok ist verbunden und bereit zum Posten.</span>'
      : tiktokState.configured
        ? `App eingerichtet (${escapeHtml(tiktokState.client_key_hint)}). Jetzt mit TikTok anmelden.`
        : 'Noch nicht eingerichtet. Trage einmal die Daten deiner TikTok-Desktop-App ein.';
    document.querySelector('#tiktokLogout').disabled = !tiktokState.connected;
  } catch (error) {
    tiktokSetupStatus.textContent = 'TikTok-Status konnte nicht geladen werden.';
  }
}

tiktokButton.addEventListener('click', async () => {
  await loadTikTokStatus();
  tiktokSettingsDialog.showModal();
});
document.querySelector('#closeTikTokSettings').addEventListener('click', () => tiktokSettingsDialog.close());

document.querySelector('#saveTikTokConfig').addEventListener('click', async () => {
  tiktokSetupStatus.textContent = 'Zugang wird sicher gespeichert …';
  try {
    const response = await fetch('/api/tiktok/settings', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        client_key:document.querySelector('#tiktokClientKey').value,
        client_secret:document.querySelector('#tiktokClientSecret').value
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Speichern fehlgeschlagen');
    document.querySelector('#tiktokClientSecret').value = '';
    await loadTikTokStatus();
  } catch (error) { tiktokSetupStatus.textContent = error.message; }
});

document.querySelector('#loginTikTok').addEventListener('click', async () => {
  await loadTikTokStatus();
  if (!tiktokState.configured) {
    tiktokSetupStatus.textContent = 'Bitte zuerst Client Key und Client Secret speichern.';
    return;
  }
  window.location.href = '/auth/tiktok/login';
});

document.querySelector('#tiktokLogout').addEventListener('click', async () => {
  await fetch('/api/tiktok/logout', {method:'POST'});
  await loadTikTokStatus();
});

async function openTikTokPost(clip) {
  activeTikTokClip = clip;
  await loadTikTokStatus();
  if (!tiktokState.connected) {
    tiktokSettingsDialog.showModal();
    return;
  }
  tiktokPostStatus.textContent = 'TikTok-Kontoeinstellungen werden geladen …';
  document.querySelector('#tiktokCaption').value = clip.title;
  document.querySelector('#hashtagPackages').innerHTML = '';
  tiktokPostDialog.showModal();
  try {
    const [creatorResponse, hashtagsResponse] = await Promise.all([
      fetch('/api/tiktok/creator'),
      fetch(`/api/tiktok/hashtags?title=${encodeURIComponent(clip.title)}`)
    ]);
    const creator = await creatorResponse.json();
    const hashtags = await hashtagsResponse.json();
    if (!creatorResponse.ok) throw new Error(creator.error || 'TikTok-Konto konnte nicht geladen werden');
    const privacy = document.querySelector('#tiktokPrivacy');
    const privacyNames = {PUBLIC_TO_EVERYONE:'Öffentlich', MUTUAL_FOLLOW_FRIENDS:'Freunde', FOLLOWER_OF_CREATOR:'Follower', SELF_ONLY:'Nur ich'};
    privacy.innerHTML = (creator.privacy_level_options || []).map(value => `<option value="${value}">${privacyNames[value] || value}</option>`).join('');
    const comments = document.querySelector('#tiktokComments');
    const duet = document.querySelector('#tiktokDuet');
    const stitch = document.querySelector('#tiktokStitch');
    comments.disabled = Boolean(creator.comment_disabled);
    duet.disabled = Boolean(creator.duet_disabled);
    stitch.disabled = Boolean(creator.stitch_disabled);
    if (comments.disabled) comments.checked = false;
    if (duet.disabled) duet.checked = false;
    if (stitch.disabled) stitch.checked = false;
    const ideaBox = document.querySelector('#ideaPackages');
    ideaBox.innerHTML = '';
    try {
      (await fetchCaptionIdeas(clip.index)).forEach(variant => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'hashtag-package';
        button.textContent = variant.name;
        button.addEventListener('click', () => { document.querySelector('#tiktokCaption').value = variant.text.slice(0, 2200); });
        ideaBox.appendChild(button);
      });
    } catch (error) { /* Vorschläge sind optional */ }
    const packageBox = document.querySelector('#hashtagPackages');
    (hashtags.packages || []).forEach(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'hashtag-package';
      button.textContent = `${item.name}: ${item.tags.map(tag => `#${tag}`).join(' ')}`;
      button.addEventListener('click', () => {
        const caption = document.querySelector('#tiktokCaption');
        const tags = item.tags.map(tag => `#${tag}`).join(' ');
        caption.value = `${caption.value.replace(/\s+$/,'')}\n${tags}`.slice(0, 2200);
      });
      packageBox.appendChild(button);
    });
    tiktokPostStatus.textContent = creator.creator_nickname
      ? `Bereit für ${creator.creator_nickname}. Maximal ${creator.max_video_post_duration_sec || '?'} Sekunden.`
      : 'Bereit zum Posten.';
  } catch (error) { tiktokPostStatus.textContent = error.message; }
}

document.querySelector('#closeTikTokPost').addEventListener('click', () => tiktokPostDialog.close());
document.querySelector('#cancelTikTokPost').addEventListener('click', () => tiktokPostDialog.close());
document.querySelector('#publishTikTok').addEventListener('click', async () => {
  if (!currentJobId || !activeTikTokClip) return;
  const publishButton = document.querySelector('#publishTikTok');
  publishButton.disabled = true;
  tiktokPostStatus.textContent = 'Clip wird sicher an TikTok übertragen …';
  try {
    const response = await fetch(`/api/jobs/${currentJobId}/clips/${activeTikTokClip.index}/tiktok`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        caption:document.querySelector('#tiktokCaption').value,
        privacy_level:document.querySelector('#tiktokPrivacy').value,
        allow_comment:document.querySelector('#tiktokComments').checked,
        allow_duet:document.querySelector('#tiktokDuet').checked,
        allow_stitch:document.querySelector('#tiktokStitch').checked
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'TikTok-Post fehlgeschlagen');
    tiktokPostStatus.textContent = `Upload abgeschlossen. TikTok verarbeitet den Post (${data.publish_id}).`;
    pollTikTokPost(data.publish_id, 0);
  } catch (error) {
    tiktokPostStatus.textContent = error.message;
    publishButton.disabled = false;
  }
});

async function pollTikTokPost(publishId, attempt) {
  if (attempt > 30) {
    tiktokPostStatus.textContent += ' Der Status kann später bei TikTok geprüft werden.';
    return;
  }
  try {
    const response = await fetch('/api/tiktok/publish-status', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({publish_id:publishId})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Status nicht verfügbar');
    if (data.status === 'PUBLISH_COMPLETE') {
      tiktokPostStatus.textContent = '✓ Der Clip wurde erfolgreich auf TikTok veröffentlicht.';
      return;
    }
    if (data.status === 'FAILED') throw new Error(data.fail_reason || 'TikTok hat den Post abgelehnt.');
    tiktokPostStatus.textContent = `TikTok verarbeitet den Post: ${data.status || 'läuft'} …`;
    setTimeout(() => pollTikTokPost(publishId, attempt + 1), 3000);
  } catch (error) {
    tiktokPostStatus.textContent = error.message;
    document.querySelector('#publishTikTok').disabled = false;
  }
}

function showError(message) {
  progressMessage.textContent = message;
  progressMessage.classList.add('error');
  document.querySelector('#progressTitle').textContent = 'Verarbeitung fehlgeschlagen';
  document.querySelector('.spinner').style.display = 'none';
}

function escapeHtml(text) {
  const div = document.createElement('div'); div.textContent = text; return div.innerHTML;
}

function escapeAttribute(text) {
  return escapeHtml(String(text || '')).replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

document.querySelector('#newJob').addEventListener('click', () => location.reload());
document.querySelector('#retryJob').addEventListener('click', () => {
  progressPanel.classList.add('hidden');
  setupPanel.classList.remove('hidden');
  recentPanel.classList.remove('hidden');
  document.querySelector('.spinner').style.display = '';
  document.querySelector('#progressTitle').textContent = 'Video wird verarbeitet';
});
loadRecentJobs();
loadTikTokStatus();

const tiktokQuery = new URLSearchParams(location.search);
if (tiktokQuery.get('tiktok') === 'connected' || tiktokQuery.get('tiktok_error')) {
  loadTikTokStatus().then(() => {
    if (tiktokQuery.get('tiktok_error')) tiktokSetupStatus.textContent = tiktokQuery.get('tiktok_error');
    tiktokSettingsDialog.showModal();
    history.replaceState({}, '', '/');
  });
}

