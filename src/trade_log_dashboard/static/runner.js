let activeRun = null;
let pollTimer = null;
let projectDatasets = [];
let datasetLoadPending = false;
let datasetRetryTimer = null;
function setDatasetHint(message) {
  const hint = $("datasetHint");
  if (hint) hint.textContent = message;
}
let sweepRun = null;
let selectedSweepIndex = null;
const displayDate = new Intl.DateTimeFormat("en-IN", {day:"numeric", month:"short", year:"numeric", timeZone:"UTC"});
const historyDate = new Intl.DateTimeFormat("en-IN", {day:"numeric", month:"short", year:"numeric", hour:"2-digit", minute:"2-digit"});
function showDatasetCoverage(dataset) {
  const coverage = $("datasetCoverage");
  if (!dataset?.available || !dataset.start_date || !dataset.end_date) {
    coverage.querySelector("strong").textContent = "Choose a dataset to see its coverage";
    coverage.querySelector("small").textContent = "The complete available period will be backtested.";
    return;
  }
  const start = displayDate.format(new Date(`${dataset.start_date}T00:00:00Z`));
  const end = displayDate.format(new Date(`${dataset.end_date}T00:00:00Z`));
  coverage.querySelector("strong").textContent = `${start} – ${end}`;
  coverage.querySelector("small").textContent = `${number.format(dataset.trading_days)} trading dates · Full available range selected automatically`;
}
async function loadDatasets() {
  if (datasetLoadPending) return;
  datasetLoadPending = true;
  clearTimeout(datasetRetryTimer);
  try {
    const response = await fetch("/api/datasets", {cache: "no-store"});
    if (!response.ok) throw new Error(`Dataset request failed (HTTP ${response.status}).`);
    const payload = await response.json();
    if (!Array.isArray(payload.datasets)) throw new Error("The server returned an invalid dataset list.");
    projectDatasets = payload.datasets;
    const options = [new Option("Choose an expiry dataset", "")];
    for (const dataset of projectDatasets) {
      const option = new Option(dataset.label + (dataset.available ? "" : " — unavailable"), dataset.id);
      option.disabled = !dataset.available;
      options.push(option);
    }
    $("datasetSelect").replaceChildren(...options);
    setDatasetHint("Choose an expiry dataset. Its available dates will appear after selection.");
    showDatasetCoverage(null);
  } catch (error) {
    $("datasetSelect").replaceChildren(new Option(`Could not load datasets: ${error.message}`, ""));
    setDatasetHint(`${error.message} Retrying in 5 seconds…`);
    console.error("Dataset loading failed:", error);
    datasetRetryTimer = setTimeout(loadDatasets, 5000);
  } finally {
    datasetLoadPending = false;
  }
}
$("datasetSelect").addEventListener("change", () => {
  const selected = $("datasetSelect").value;
  const dataset = projectDatasets.find(row => row.id === selected);
  setDatasetHint(dataset ? `Uses the project's ${dataset.folder} dataset.` : "Choose the saved dataset to backtest.");
  showDatasetCoverage(dataset);
});
loadDatasets();

function hideStorage() { $("storagePanel").hidden = true; }

async function loadStorageSettings() {
  $("storageStatus").textContent = "Loading storage locations…";
  try {
    const response = await fetch("/api/settings", {cache:"no-store"});
    const settings = await response.json();
    if (!response.ok) throw new Error(settings.error || "Storage settings could not be loaded.");
    $("datasetRoot").value = settings.dataset_root;
    $("settingsFile").textContent = settings.settings_file;
    $("logsRoot").textContent = settings.logs_root;
    $("resultsRoot").textContent = settings.results_root;
    $("storageStatus").textContent = "";
  } catch (error) {
    $("storageStatus").textContent = error.message;
  }
}

async function showStorage() {
  $("runnerPanel").hidden = true;
  $("uploadView").hidden = true;
  $("dashboard").hidden = true;
  $("sweepPanel").hidden = true;
  $("historyPanel").hidden = true;
  $("storagePanel").hidden = false;
  $("replaceButton").hidden = true;
  $("backToSweepButton").hidden = true;
  await loadStorageSettings();
  $("storageTitle").focus({preventScroll:true});
}

$("storageButton").addEventListener("click", showStorage);
$("storageDoneButton").addEventListener("click", showRunner);
$("storageForm").addEventListener("submit", async event => {
  event.preventDefault();
  $("saveStorage").disabled = true;
  $("storageStatus").textContent = "Saving and checking datasets…";
  try {
    const response = await fetch("/api/settings", {
      method:"POST",
      headers:{"Content-Type":"application/json", "X-Local-Runner":"1"},
      body:JSON.stringify({dataset_root:$("datasetRoot").value.trim()}),
    });
    const settings = await response.json();
    if (!response.ok) throw new Error(settings.error || "Dataset folder could not be saved.");
    $("datasetRoot").value = settings.dataset_root;
    $("storageStatus").textContent = "Dataset folder saved.";
    await loadDatasets();
  } catch (error) {
    $("storageStatus").textContent = error.message;
  } finally {
    $("saveStorage").disabled = false;
  }
});

function showRunner() {
  $("runnerPanel").hidden = false;
  $("uploadView").hidden = true;
  $("dashboard").hidden = true;
  $("sweepPanel").hidden = true;
  $("historyPanel").hidden = true;
  hideStorage();
  $("replaceButton").hidden = true;
  $("backToSweepButton").hidden = true;
  $("runnerPanel").scrollIntoView();
}
$("newRunButton").addEventListener("click", () => {
  sweepRun = null;
  selectedSweepIndex = null;
  showRunner();
});
$("backToSweepButton").addEventListener("click", () => {
  if (!sweepRun || state.uploading) return;
  $("runnerPanel").hidden = true;
  $("uploadView").hidden = true;
  $("dashboard").hidden = true;
  $("historyPanel").hidden = true;
  hideStorage();
  $("runDownloads").hidden = true;
  $("sweepPanel").hidden = false;
  $("replaceButton").hidden = true;
  $("backToSweepButton").hidden = true;
  $("sweepTitle").focus({preventScroll:true});
  window.scrollTo({top:0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"});
});
$("csvModeButton").addEventListener("click", () => {
  if (activeRun) return;
  $("runnerPanel").hidden = true;
  $("uploadView").hidden = false;
  $("dashboard").hidden = true;
  $("sweepPanel").hidden = true;
  $("historyPanel").hidden = true;
  hideStorage();
});
$("strategyFiles").addEventListener("change", () => {
  $("entrypoint").value = $("strategyFiles").files[0]?.name || "";
});

function busyRun(busy) {
  state.uploading = busy;
  for (const id of ["runButton", "csvModeButton", "chooseButton", "replaceButton", "equityButton", "historyButton", "storageButton"]) $(id).disabled = busy;
  $("cancelRun").hidden = !busy || !activeRun;
  $("runForm").setAttribute("aria-busy", String(busy));
}

function downloads(id, hasEquity, iteration = null) {
  const names = ["trades.csv", "trades.csv.manifest.json", "run.log"];
  if (iteration !== null) names.pop();
  if (hasEquity) names.push("equity.csv");
  const prefix = iteration === null ? `/api/backtests/${id}` : `/api/backtests/${id}/iterations/${iteration}`;
  $("runDownloads").replaceChildren(document.createTextNode(iteration === null ? "Local run outputs:" : "Selected variation outputs:"), ...names.map(name => {
    const link = document.createElement("a");
    link.href = `${prefix}/${name}`;
    link.textContent = name;
    link.download = name;
    return link;
  }));
  $("runDownloads").hidden = false;
}

function historyDownloads(record) {
  const names = record.artifacts || [];
  $("runDownloads").replaceChildren(document.createTextNode("Saved best outputs:"), ...names.map(name => {
    const link = document.createElement("a");
    link.href = `/api/history/${record.id}/${name}`;
    link.textContent = name;
    link.download = name;
    return link;
  }));
  $("runDownloads").hidden = names.length === 0;
}

async function openHistory(identifier, button) {
  if (state.uploading) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Opening…";
  try {
    const response = await fetch(`/api/history/${identifier}`);
    const record = await response.json();
    if (!response.ok) throw new Error(record.error || "Saved result could not be opened.");
    const tradeResponse = await fetch(`/api/history/${identifier}/trades.csv`);
    state.tradeFile = tradeResponse.ok ? new File([await tradeResponse.blob()], "trades.csv", {type:"text/csv"}) : null;
    state.fileName = `Saved best · ${record.strategy}`;
    $("historyPanel").hidden = true;
    hideStorage();
    $("sweepPanel").hidden = true;
    $("runnerPanel").hidden = true;
    render(record.analysis);
    if (record.analysis.intraday) renderIntraday(record.analysis.intraday, "saved equity.csv");
    historyDownloads(record);
    $("replaceButton").hidden = true;
    $("backToSweepButton").hidden = true;
    $("runMeta").textContent += ` · Saved ${historyDate.format(new Date(record.created_at))}`;
  } catch (error) {
    $("historyStatus").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function showHistory() {
  if (state.uploading) return;
  $("runnerPanel").hidden = true;
  $("uploadView").hidden = true;
  $("dashboard").hidden = true;
  $("sweepPanel").hidden = true;
  $("runDownloads").hidden = true;
  $("replaceButton").hidden = true;
  $("backToSweepButton").hidden = true;
  hideStorage();
  $("historyPanel").hidden = false;
  $("historyStatus").textContent = "Loading saved best results…";
  try {
    const response = await fetch("/api/history");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Best-result history could not be loaded.");
    const rows = result.history || [];
    $("historyCount").textContent = `${number.format(rows.length)} saved`;
    $("historyStatus").textContent = rows.length ? "Only automatically recommended sweep winners are stored." : "No completed sweep winner has been saved yet.";
    $("historyRows").innerHTML = rows.map(record => {
      const metrics = record.metrics || {};
      const drawdown = metrics.intraday_drawdown == null ? metrics.max_drawdown : metrics.intraday_drawdown;
      const dataset = record.dataset?.label || "Custom data";
      return `<tr><td>${historyDate.format(new Date(record.created_at))}</td><td>${escapeHtml(record.strategy || "Strategy")}</td><td>${escapeHtml(dataset)}</td><td class="sweep-parameters">${escapeHtml(parameterText(record.parameters))}</td><td class="numeric">${record.selection_score == null ? "—" : number.format(record.selection_score)}</td><td class="numeric ${Number(metrics.net_pnl) > 0 ? "positive" : ""}">${money.format(metrics.net_pnl)}</td><td class="numeric ${Number(drawdown) < 0 ? "negative" : ""}">${money.format(drawdown)}</td><td><button class="secondary-button history-open" data-history-id="${record.id}" type="button">View analytics</button></td></tr>`;
    }).join("") || '<tr><td colspan="8" class="empty-table">Run a sweep to save its recommended result here.</td></tr>';
    $("historyRows").querySelectorAll(".history-open").forEach(button => button.addEventListener("click", () => openHistory(button.dataset.historyId, button)));
  } catch (error) {
    $("historyStatus").textContent = error.message;
    $("historyRows").innerHTML = '<tr><td colspan="8" class="empty-table">History is unavailable.</td></tr>';
  }
  $("historyTitle").focus({preventScroll:true});
  window.scrollTo({top:0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"});
}
$("historyButton").addEventListener("click", showHistory);

function parameterText(parameters) {
  const entries = Object.entries(parameters || {});
  return entries.length ? entries.map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(" · ") : "Strategy defaults";
}

async function openSweepIteration(index, button, saveBest = false) {
  if (!sweepRun || state.uploading) return;
  const original = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "Starting…";
  }
  try {
    const headers = {"X-Local-Runner":"1"};
    if (saveBest) headers["X-Save-Best"] = "1";
    const response = await fetch(`/api/backtests/${sweepRun}/iterations/${index}/run`, {method:"POST", headers});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not start the selected combination.");
    activeRun = result.id;
    sessionStorage.setItem("activeBacktest", activeRun);
    busyRun(true);
    $("sweepPanel").hidden = true;
    $("runnerPanel").hidden = false;
    $("runStatus").textContent = `Rerunning combination ${index + 1} to create its full analytics…`;
    $("runLog").textContent = "";
    $("runLogPanel").hidden = false;
    $("runnerPanel").scrollIntoView();
    await pollRun();
  } catch (error) {
    $("runStatus").textContent = error.message;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

function renderSweep(sweep, id) {
  sweepRun = id;
  selectedSweepIndex = null;
  $("viewSelectedSweep").disabled = true;
  $("sweepCount").textContent = `${sweep.iteration_count} variations`;
  $("sweepNote").textContent = `${number.format(sweep.ranked_count || 0)} profitable candidates ranked · ${number.format(sweep.no_trade_count || 0)} without trades · ${number.format(sweep.failed_count || 0)} failed. Score: 35% P&L, 35% lower drawdown, 15% average win/loss ratio, 10% win rate, and 5% fewer consecutive losses. The recommended combination opens automatically; you can return here and override it.`;
  const parameterKeys = [...new Set(sweep.iterations.flatMap(item => Object.keys(item.parameters || {})))];
  $("sweepHead").innerHTML = `<tr><th>Select</th><th>Rank</th><th>Combination</th>${parameterKeys.map(key => `<th>${escapeHtml(key)}</th>`).join("")}<th class="numeric">Score</th><th class="numeric">Net P&amp;L</th><th class="numeric">Max drawdown</th><th class="numeric">Win rate</th><th class="numeric">Max consecutive losses</th><th class="numeric">Average profit</th><th class="numeric">Average loss</th><th class="numeric">Avg win/loss</th><th class="numeric">Profit factor</th><th class="numeric">Trades</th><th><span class="sr-only">Action</span></th></tr>`;
  const rankedRows = [...sweep.iterations].sort((left, right) =>
    (left.rank ?? Number.MAX_SAFE_INTEGER) - (right.rank ?? Number.MAX_SAFE_INTEGER) || left.index - right.index
  );
  $("sweepRows").innerHTML = rankedRows.map(item => {
    const metrics = item.metrics;
    const selector = `<input class="sweep-select" type="radio" name="sweep-selection" value="${item.index}" aria-label="Select combination ${item.index + 1}" ${metrics ? "" : "disabled"}>`;
    const parameterCells = parameterKeys.map(key => `<td class="sweep-parameter-value">${item.parameters && Object.hasOwn(item.parameters, key) ? escapeHtml(JSON.stringify(item.parameters[key])) : "—"}</td>`).join("");
    if (!metrics) {
      const message = item.status === "failed" ? escapeHtml(item.error || "Variation failed") : "No completed trades";
      return `<tr data-index="${item.index}"><td>${selector}</td><td>—</td><td>${item.index + 1}</td>${parameterCells}<td colspan="10" class="sweep-empty">${message}</td><td><button class="secondary-button" type="button" disabled>Unavailable</button></td></tr>`;
    }
    const net = Number(metrics.net_pnl);
    const drawdown = metrics.intraday_drawdown == null ? metrics.max_drawdown : metrics.intraday_drawdown;
    const ratio = metrics.average_win_loss_ratio == null ? (metrics.average_profit != null && metrics.average_loss == null ? "∞" : "—") : number.format(metrics.average_win_loss_ratio);
    const recommended = item.index === sweep.recommended_index ? '<span class="comparison-badge">Recommended</span>' : '';
    const componentTitle = metrics.selection_components ? escapeHtml(`P&L ${metrics.selection_components.net_pnl} · Drawdown ${metrics.selection_components.drawdown} · Win/loss ${metrics.selection_components.average_win_loss_ratio} · Win rate ${metrics.selection_components.win_rate} · Loss streak ${metrics.selection_components.max_consecutive_losses}`) : "";
    return `<tr data-index="${item.index}"><td>${selector}</td><td>${item.rank ?? "—"}</td><td>${item.index + 1}${recommended}</td>${parameterCells}<td class="numeric" title="${componentTitle}">${metrics.selection_score == null ? "—" : number.format(metrics.selection_score)}</td><td class="numeric ${net > 0 ? "positive" : net < 0 ? "negative" : ""}">${money.format(net)}</td><td class="numeric ${Number(drawdown) < 0 ? "negative" : ""}">${money.format(drawdown)}</td><td class="numeric">${number.format(metrics.win_rate)}%</td><td class="numeric">${number.format(metrics.max_consecutive_losses)}</td><td class="numeric positive">${metrics.average_profit == null ? "—" : money.format(metrics.average_profit)}</td><td class="numeric ${Number(metrics.average_loss) < 0 ? "negative" : ""}">${metrics.average_loss == null ? "—" : money.format(metrics.average_loss)}</td><td class="numeric">${ratio}</td><td class="numeric">${metrics.profit_factor == null ? "—" : number.format(metrics.profit_factor)}</td><td class="numeric">${metrics.batch_count}</td><td><button class="secondary-button sweep-open" data-index="${item.index}" type="button">Run &amp; view analytics</button></td></tr>`;
  }).join("");
  $("sweepRows").querySelectorAll(".sweep-select").forEach(input => input.addEventListener("change", () => {
    selectedSweepIndex = Number(input.value);
    $("viewSelectedSweep").disabled = false;
    document.querySelectorAll("#sweepRows tr").forEach(row => row.removeAttribute("aria-current"));
    input.closest("tr").setAttribute("aria-current", "true");
  }));
  $("sweepRows").querySelectorAll(".sweep-open").forEach(button => button.addEventListener("click", () => {
    const index = Number(button.dataset.index);
    const input = document.querySelector(`.sweep-select[value="${index}"]`);
    if (input) {
      input.checked = true;
      input.dispatchEvent(new Event("change"));
    }
    openSweepIteration(index, button);
  }));
  if (sweep.recommended_index !== null && sweep.recommended_index !== undefined) {
    const recommendedInput = document.querySelector(`.sweep-select[value="${sweep.recommended_index}"]`);
    if (recommendedInput) {
      recommendedInput.checked = true;
      recommendedInput.dispatchEvent(new Event("change"));
    }
  }
  $("runnerPanel").hidden = true;
  $("uploadView").hidden = true;
  $("dashboard").hidden = true;
  $("historyPanel").hidden = true;
  hideStorage();
  $("runDownloads").hidden = true;
  $("sweepPanel").hidden = false;
  $("replaceButton").hidden = true;
  $("sweepTitle").focus({preventScroll:true});
  window.scrollTo({top:0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"});
}

$("viewSelectedSweep").addEventListener("click", () => {
  if (selectedSweepIndex !== null) openSweepIteration(selectedSweepIndex, $("viewSelectedSweep"));
});

function duration(seconds) {
  if (seconds == null) return "";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.ceil((seconds % 3600) / 60)}m`;
}

async function pollRun() {
  const id = activeRun;
  if (!id) return;
  try {
    const response = await fetch(`/api/backtests/${id}`);
    const job = await response.json();
    if (!response.ok) {
      if (response.status === 404) {
        activeRun = null;
        sessionStorage.removeItem("activeBacktest");
        busyRun(false);
        $("runStatus").textContent = job.error;
        return;
      }
      throw new Error(job.error || "Could not read run status.");
    }
    $("runLogPanel").hidden = false;
    $("runLog").textContent = job.log;
    if (job.progress?.mode === "sweep") {
      const estimate = job.progress.estimated_remaining_seconds == null ? "" : ` · about ${duration(job.progress.estimated_remaining_seconds)} remaining`;
      $("runStatus").textContent = `Sweep ${job.status} · ${job.progress.completed} of ${job.progress.total} combinations complete · ${duration(job.elapsed_seconds)} elapsed${estimate}`;
    } else {
      $("runStatus").textContent = `Backtest ${job.status} · ${job.elapsed_seconds} seconds`;
    }
    if (job.status === "running") {
      pollTimer = setTimeout(pollRun, 1500);
      return;
    }
    if (job.status === "succeeded") {
      if (job.result.mode === "sweep") {
        const recommendedIndex = job.result.sweep.recommended_index;
        renderSweep(job.result.sweep, id);
        activeRun = null;
        sessionStorage.removeItem("activeBacktest");
        busyRun(false);
        if (recommendedIndex !== null && recommendedIndex !== undefined) {
          $("runStatus").textContent = `Sweep complete. Opening recommended combination ${recommendedIndex + 1}…`;
          await openSweepIteration(recommendedIndex, null, true);
        }
        return;
      }
      const tradeResponse = await fetch(`/api/backtests/${id}/trades.csv`);
      if (!tradeResponse.ok) throw new Error("Run completed but the trade file could not be downloaded.");
      state.tradeFile = new File([await tradeResponse.blob()], "trades.csv", {type:"text/csv"});
      state.fileName = "trades.csv";
      $("intradayReport").hidden = true;
      $("equityStatus").textContent = "This strategy did not supply equity snapshots. Add equity.csv to inspect intraday risk.";
      render(job.result.analysis);
      if (job.result.analysis.intraday) renderIntraday(job.result.analysis.intraday, "equity.csv");
      downloads(id, Boolean(job.result.analysis.intraday));
      $("runnerPanel").hidden = true;
      if (sweepRun) {
        $("backToSweepButton").hidden = false;
        $("replaceButton").hidden = true;
      } else {
        $("backToSweepButton").hidden = true;
      }
      if (job.history_id) $("runMeta").textContent += " · Saved to Best history";
      if (job.history_error) $("runStatus").textContent = job.history_error;
    } else if (job.status === "empty") {
      $("runStatus").textContent = job.result.message;
      downloads(id, false);
    } else if (job.status === "failed") {
      $("runStatus").textContent = job.error;
      $("runLogPanel").open = true;
    }
    if (sweepRun) {
      $("backToSweepButton").hidden = false;
      $("replaceButton").hidden = true;
    }
    activeRun = null;
    sessionStorage.removeItem("activeBacktest");
    busyRun(false);
  } catch (error) {
    $("runStatus").textContent = `${error.message} Reconnecting to the local runner…`;
    pollTimer = setTimeout(pollRun, 4000);
  }
}

$("runForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (state.uploading || activeRun) return;
  try {
    const config = JSON.parse($("runConfig").value);
    if (!config || Array.isArray(config) || typeof config !== "object") throw new Error("Configuration must be a JSON object.");
    const selectedDataset = $("datasetSelect").value;
    if (!selectedDataset) throw new Error("Choose a dataset to backtest.");
    const form = new FormData();
    let total = 0;
    for (const file of $("strategyFiles").files) {
      if (!file.name.endsWith(".py") || file.size > 2 * 1024 * 1024) throw new Error("Each strategy file must be a .py file under 2 MiB.");
      total += file.size;
      form.append("strategy", file);
    }
    if (total > 255 * 1024 * 1024) throw new Error("Upload exceeds 255 MiB. Use a local data path for a large dataset.");
    form.append("entrypoint", $("entrypoint").value);
    form.append("config", JSON.stringify(config));
    form.append("dataset_id", selectedDataset);
    busyRun(true);
    $("runStatus").textContent = "Uploading files and starting the local Python process…";
    $("runDownloads").hidden = true;
    $("runLog").textContent = "";
    const response = await fetch("/api/backtests", {method:"POST", headers:{"X-Local-Runner":"1"}, body:form});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not start the backtest.");
    activeRun = result.id;
    sessionStorage.setItem("activeBacktest", activeRun);
    busyRun(true);
    await pollRun();
  } catch (error) {
    $("runStatus").textContent = error.message;
    busyRun(false);
  }
});

$("cancelRun").addEventListener("click", async () => {
  if (!activeRun) return;
  try {
    const response = await fetch(`/api/backtests/${activeRun}/cancel`, {method:"POST", headers:{"X-Local-Runner":"1"}});
    if (!response.ok) throw new Error("Could not cancel the run. Check that the local server is running.");
    clearTimeout(pollTimer);
    await pollRun();
  } catch (error) { $("runStatus").textContent = error.message; }
});

const savedRun = sessionStorage.getItem("activeBacktest");
if (savedRun) { activeRun = savedRun; busyRun(true); pollRun(); }
