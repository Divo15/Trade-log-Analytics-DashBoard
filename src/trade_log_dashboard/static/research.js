/* Research has its own state; the existing backtest and analytics remain authoritative. */
(() => {
  const el = id => document.getElementById(id);
  const roles = {lead: 'Research lead', data: 'Data analyst', experiment: 'Experiment engineer', strategy: 'Strategy engineer', review: 'Validation reviewer', user: 'You', controller: 'Controller', team: 'Team'};
  let selected = null;
  let timer = null;
  let connected = false;
  let renderedVersion = null;
  let requestVersion = 0;
  const text = (tag, content, className) => {
    const node = document.createElement(tag);
    node.textContent = content;
    if (className) node.className = className;
    return node;
  };
  async function api(path, payload) {
    const options = payload === undefined ? {cache: 'no-store'} : {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Local-Runner': '1'}, body: JSON.stringify(payload)
    };
    const response = await fetch(`/api/research/${path}`, options);
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || `Research request failed (${response.status}).`);
    return value;
  }
  const fail = error => { el('researchError').textContent = error.message || String(error); };
  async function connection() {
    el('researchReconnect').disabled = true;
    try {
      const result = await api('connection');
      connected = result.ready;
      el('researchConnection').textContent = result.message;
    } catch (error) { connected = false; fail(error); }
    finally { el('researchStart').disabled = !connected; el('researchReconnect').disabled = false; }
  }
  async function listRuns() {
    const data = await api('runs');
    el('researchRunList').replaceChildren();
    el('researchListStatus').textContent = data.runs.length ? `${data.runs.length} saved runs` : 'Your research assignments and evidence will appear here.';
    for (const run of data.runs) {
      const button = text('button', run.objective.length > 76 ? `${run.objective.slice(0, 76)}…` : run.objective, 'research-run-link');
      button.type = 'button';
      button.setAttribute('aria-current', String(run.id === selected));
      button.append(text('small', `${run.status.replaceAll('_', ' ')} · ${run.calls_used}/${run.max_calls} calls`));
      button.addEventListener('click', () => openRun(run.id));
      el('researchRunList').append(button);
    }
  }
  async function datasets() {
    const response = await fetch('/api/datasets');
    if (!response.ok) throw new Error('Could not load datasets. Check Storage and try again.');
    const data = await response.json();
    const current = el('datasetSelect').value || el('researchDataset').value;
    el('researchDataset').replaceChildren(new Option('Choose an expiry dataset', ''));
    for (const dataset of data.datasets) {
      const option = new Option(dataset.label + (dataset.available ? '' : ' — unavailable'), dataset.id);
      option.disabled = !dataset.available;
      el('researchDataset').append(option);
    }
    el('researchDataset').value = current;
  }
  function showResearch() {
    for (const id of ['runnerPanel', 'parquetPanel', 'uploadView', 'dashboard', 'sweepPanel', 'historyPanel', 'storagePanel', 'runDownloads', 'replaceButton', 'backToSweepButton']) el(id).hidden = true;
    el('researchPanel').hidden = false;
    el('researchTitle').focus({preventScroll: true});
    Promise.all([connection(), datasets(), listRuns()]).catch(fail);
    if (selected) poll();
  }
  function hideResearch() { el('researchPanel').hidden = true; clearTimeout(timer); }
  window.hideResearch = hideResearch;
  el('researchButton').addEventListener('click', showResearch);
  el('researchBack').addEventListener('click', () => { hideResearch(); showRunner(); });
  el('researchReconnect').addEventListener('click', connection);
  for (const id of ['newRunButton', 'historyButton', 'storageButton', 'backToSweepButton', 'replaceButton']) el(id).addEventListener('click', hideResearch);
  el('researchNew').addEventListener('click', () => {
    selected = null; requestVersion++; clearTimeout(timer); renderedVersion = null;
    el('researchSetup').hidden = false; el('researchWorkspace').hidden = true; el('researchError').textContent = '';
    listRuns().catch(fail);
  });
  el('researchForm').addEventListener('submit', async event => {
    event.preventDefault();
    el('researchStart').disabled = true; el('researchError').textContent = '';
    try {
      const result = await api('runs', {dataset_id: el('researchDataset').value, objective: el('researchObjective').value,
        constraints: el('researchConstraints').value, max_calls: Number(el('researchBudget').value), config: JSON.parse(el('runConfig').value)});
      await openRun(result.id);
    } catch (error) { fail(error); }
    finally { el('researchStart').disabled = !connected; }
  });
  async function openRun(id) {
    selected = id; requestVersion++; renderedVersion = null; clearTimeout(timer);
    el('researchSetup').hidden = true; el('researchWorkspace').hidden = false;
    el('researchError').textContent = '';
    await poll();
  }
  function artifactLink(run, task, name) {
    const link = text('a', name);
    link.href = `/api/research/runs/${run.id}/tasks/${task.sequence}/files/${name.split('/').map(encodeURIComponent).join('/')}`;
    link.download = name.split('/').pop();
    return link;
  }
  function renderRun(run) {
    if (renderedVersion === run.updated_at) return;
    renderedVersion = run.updated_at;
    el('researchRunTitle').textContent = run.objective;
    el('researchRunMeta').textContent = `${run.dataset_label} · ${run.calls_used}/${run.max_calls} agent calls · ChatGPT subscription`;
    el('researchRunStatus').textContent = `${run.status.replaceAll('_', ' ')}${run.current_role ? ` · ${roles[run.current_role]}` : ''}${run.error ? ` — ${run.error}` : ''}`;
    el('researchStop').hidden = !['running', 'stopping'].includes(run.status);
    el('researchStop').disabled = run.status === 'stopping';
    el('researchResume').hidden = !['stopped', 'interrupted', 'failed', 'needs_input'].includes(run.status) || run.calls_used >= run.max_calls;
    el('researchCandidate').hidden = run.status !== 'candidate_ready';
    el('researchTeam').replaceChildren();
    for (const key of ['lead', 'data', 'experiment', 'strategy', 'review']) {
      const item = document.createElement('div'); item.dataset.active = String(run.current_role === key);
      item.append(text('strong', roles[key]), text('small', key === 'lead' ? 'Astra' : 'GPT-5.6 Sol'));
      el('researchTeam').append(item);
    }
    el('researchJournal').replaceChildren();
    for (const message of run.messages) {
      const item = text('article', '', 'research-entry');
      const heading = document.createElement('header');
      heading.append(text('strong', `${roles[message.sender] || message.sender} → ${roles[message.recipient] || message.recipient}`));
      const time = text('time', new Date(message.at).toLocaleString()); time.dateTime = message.at; heading.append(time);
      item.append(heading, text('p', message.text));
      if (message.limitations?.length) {
        const list = document.createElement('ul');
        message.limitations.forEach(value => list.append(text('li', value))); item.append(list);
      }
      el('researchJournal').append(item);
    }
    el('researchTasks').replaceChildren();
    for (const task of run.tasks) {
      const item = text('section', '', 'research-task');
      item.append(text('strong', `${task.sequence}. ${roles[task.role]} · ${task.status}`));
      if (task.reply?.journey) item.append(text('pre', task.reply.journey));
      const files = new Set(task.artifacts);
      if (task.status === 'complete') files.add('reply.json');
      files.add('events.jsonl');
      if (run.candidate?.task === task.sequence) files.add(run.candidate.file);
      for (const name of files) item.append(artifactLink(run, task, name));
      el('researchTasks').append(item);
    }
  }
  async function poll() {
    clearTimeout(timer);
    if (!selected || el('researchPanel').hidden) return;
    const id = selected, version = requestVersion;
    try {
      const run = await api(`runs/${id}`);
      if (version !== requestVersion || id !== selected) return;
      renderRun(run); await listRuns();
      if (['running', 'stopping'].includes(run.status)) timer = setTimeout(poll, 2000);
    } catch (error) { fail(error); timer = setTimeout(poll, 5000); }
  }
  for (const action of ['stop', 'resume']) {
    el(action === 'stop' ? 'researchStop' : 'researchResume').addEventListener('click', async event => {
      const button = event.currentTarget; button.disabled = true;
      try { await api(`runs/${selected}/${action}`, {}); await poll(); }
      catch (error) { fail(error); }
      finally { button.disabled = false; }
    });
  }
  el('researchMessageForm').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.currentTarget.querySelector('button'); button.disabled = true;
    try { await api(`runs/${selected}/message`, {text: el('researchMessage').value}); el('researchMessage').value = ''; await poll(); }
    catch (error) { fail(error); } finally { button.disabled = false; }
  });
  el('researchHandoff').addEventListener('click', async event => {
    const button = event.currentTarget; button.disabled = true;
    try {
      if (activeRun) throw new Error('Finish the active backtest before loading a candidate.');
      const result = await api(`runs/${selected}/handoff`, {});
      const transfer = new DataTransfer();
      transfer.items.add(new File([result.source], result.filename, {type: 'text/x-python'}));
      el('strategyFiles').files = transfer.files;
      el('strategyFiles').dispatchEvent(new Event('change', {bubbles: true}));
      el('entrypoint').value = result.filename;
      el('runConfig').value = JSON.stringify(result.config, null, 2);
      el('datasetSelect').value = result.dataset_id;
      el('datasetSelect').dispatchEvent(new Event('change', {bubbles: true}));
      hideResearch(); showRunner(); el('runStatus').textContent = result.note;
    } catch (error) { fail(error); } finally { button.disabled = false; }
  });
})();
