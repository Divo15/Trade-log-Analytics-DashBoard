let activeRun = null;
let pollTimer = null;
let elapsedTimer = null;
let projectDatasets = [];
let datasetLoadPending = false;
let datasetRetryTimer = null;
function setDatasetHint(message) {
  const hint = $("datasetHint");
  if (hint) hint.textContent = message;
}
let sweepRun = null;
let selectedSweepIndex = null;
let recommendedSweepIndex = null;
let inspectedParquet = null;
let rankedSweepPayload = null;
const rankingPresetStorageKey = "trade-log-dashboard.ranking-presets.v1";
let rankingStartedAt = null;
let rankingElapsedTimer = null;
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
  setDatasetHint(dataset ? `Uses ${dataset.symbol} data from the ${dataset.folder} folder.` : "Choose the saved dataset to backtest.");
  if (dataset?.symbol) {
    try {
      const config = JSON.parse($("runConfig").value);
      if (config && !Array.isArray(config) && typeof config === "object") {
        const instrument = config.instrument && !Array.isArray(config.instrument) && typeof config.instrument === "object"
          ? config.instrument : {};
        config.instrument = {...instrument, symbol: dataset.symbol};
        $("runConfig").value = JSON.stringify(config, null, 2);
      }
    } catch (_) {
      // Preserve invalid user text so submission can show the existing JSON error.
    }
  }
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
  $("parquetPanel").hidden = true;
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
  $("parquetPanel").hidden = true;
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
  recommendedSweepIndex = null;
  showRunner();
});
$("backToSweepButton").addEventListener("click", () => {
  if (!sweepRun || state.uploading) return;
  $("runnerPanel").hidden = true;
  $("parquetPanel").hidden = true;
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
  $("parquetPanel").hidden = true;
  $("uploadView").hidden = false;
  $("dashboard").hidden = true;
  $("sweepPanel").hidden = true;
  $("historyPanel").hidden = true;
  hideStorage();
});

function showParquetMode() {
  if (activeRun) return;
  $("runnerPanel").hidden = true;
  $("uploadView").hidden = true;
  $("dashboard").hidden = true;
  $("sweepPanel").hidden = true;
  $("historyPanel").hidden = true;
  hideStorage();
  $("replaceButton").hidden = true;
  $("backToSweepButton").hidden = true;
  $("parquetPanel").hidden = false;
  $("parquetTitle").focus({preventScroll:true});
}

function schemaCompatible(expected, actual) {
  if (expected === "numeric") return actual === "numeric";
  if (expected === "time") return ["time", "text", "numeric"].includes(actual);
  if (expected === "text") return ["text", "boolean"].includes(actual);
  return true;
}

function schemaIssue(code, field, message, severity = "error") {
  return {code, field, message, severity};
}

function schemaMapping() {
  if (!inspectedParquet) return {};
  return Object.fromEntries(inspectedParquet.fields.map(field => {
    const select = document.querySelector(`[data-schema-field="${field.key}"]`);
    return [field.key, select?.value || ""];
  }));
}

function rankingColumnOptions() {
  if (!inspectedParquet) return [];
  return [
    {value: "__ranking_drawdown", label: "Ranking drawdown (derived)"},
    ...inspectedParquet.columns
      .filter(column => column.category === "numeric")
      .map(column => ({value: column.name, label: column.name})),
  ];
}

function defaultRankingCriteria() {
  const mapping = schemaMapping();
  return [
    {column: mapping.pnl_2025 || "", weight: 30, direction: "higher"},
    {column: mapping.pnl_2026 || "", weight: 30, direction: "higher"},
    {column: "__ranking_drawdown", weight: 40, direction: "lower"},
  ];
}

function readRankingCriteria() {
  const rows = [...document.querySelectorAll("[data-ranking-row]")];
  if (!rows.length) {
    $("rankingBuilderStatus").textContent = "Add at least one ranking criterion.";
    return null;
  }
  const available = new Set(rankingColumnOptions().map(option => option.value));
  const used = new Set();
  const criteria = [];
  let error = "";
  for (const row of rows) {
    const column = row.querySelector("[data-ranking-column]").value;
    const weight = Number(row.querySelector("[data-ranking-weight]").value);
    const direction = row.querySelector("[data-ranking-direction]").value;
    if (!column || !available.has(column)) error = "Choose a numeric column for every criterion.";
    else if (used.has(column)) error = "Use each ranking column only once.";
    else if (!Number.isFinite(weight) || weight <= 0) error = "Every ranking weight must be greater than zero.";
    else if (!["higher", "lower"].includes(direction)) error = "Choose higher or lower for every criterion.";
    used.add(column);
    criteria.push({column, weight, direction});
  }
  const totalWeight = criteria.reduce((total, criterion) => total + criterion.weight, 0);
  if (!error && Math.abs(totalWeight - 100) > 0.001) error = `Weights currently total ${totalWeight}%. They must total 100%.`;
  $("rankingBuilderStatus").textContent = error || `Weights total ${totalWeight}%.`;
  $("rankingBuilderStatus").className = error ? "negative" : "positive";
  return error ? null : criteria;
}

function renderRankingBuilder(criteria = defaultRankingCriteria()) {
  const container = $("rankingBuilderRows");
  const options = rankingColumnOptions();
  const rows = criteria.map(criterion => {
    const row = document.createElement("div");
    row.className = "ranking-builder-row";
    row.dataset.rankingRow = "1";

    const column = document.createElement("select");
    column.dataset.rankingColumn = "1";
    column.setAttribute("aria-label", "Ranking column");
    column.append(new Option("Choose a numeric metric", ""));
    options.forEach(option => column.append(new Option(option.label, option.value)));
    column.value = options.some(option => option.value === criterion.column) ? criterion.column : "";

    const weight = document.createElement("input");
    weight.dataset.rankingWeight = "1";
    weight.type = "number";
    weight.min = "0.01";
    weight.max = "100";
    weight.step = "any";
    weight.value = criterion.weight;
    weight.setAttribute("aria-label", "Ranking weight percent");

    const direction = document.createElement("select");
    direction.dataset.rankingDirection = "1";
    direction.setAttribute("aria-label", "Ranking direction");
    direction.append(new Option("Higher is better", "higher"), new Option("Lower is better", "lower"));
    direction.value = criterion.direction;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button";
    remove.textContent = "Remove";
    remove.disabled = criteria.length === 1;
    remove.addEventListener("click", () => {
      row.remove();
      renderRankingBuilder([...document.querySelectorAll("[data-ranking-row]")].map(item => ({
        column: item.querySelector("[data-ranking-column]").value,
        weight: item.querySelector("[data-ranking-weight]").value,
        direction: item.querySelector("[data-ranking-direction]").value,
      })));
      validateSchemaMapping();
    });
    [column, weight, direction].forEach(control => control.addEventListener("input", validateSchemaMapping));
    [column, direction].forEach(control => control.addEventListener("change", validateSchemaMapping));
    row.append(column, weight, direction, remove);
    return row;
  });
  container.replaceChildren(...rows);
  $("addRankingCriterion").disabled = criteria.length >= 6;
}

function readSweepFilters() {
  return {
    status: $("filterStatus").value.trim(),
    minimum_pnl: $("filterMinimumPnl").value,
    maximum_pnl: $("filterMaximumPnl").value,
    maximum_drawdown: $("filterMaximumDrawdown").value,
    latest_entry: $("filterLatestEntry").value,
    minimum_trades: $("filterMinimumTrades").value,
  };
}

function savedRankingPresets() {
  try {
    const stored = JSON.parse(localStorage.getItem(rankingPresetStorageKey) || "[]");
    return Array.isArray(stored) ? stored.filter(item => item && typeof item.name === "string") : [];
  } catch (_) {
    return [];
  }
}

function persistRankingPresets(presets) {
  localStorage.setItem(rankingPresetStorageKey, JSON.stringify(presets));
}

function renderRankingPresets(selectedName = "") {
  const presets = savedRankingPresets();
  const select = $("rankingPresetSelect");
  select.replaceChildren(new Option("Choose a preset", ""), ...presets.map(preset => new Option(preset.name, preset.name)));
  select.value = presets.some(preset => preset.name === selectedName) ? selectedName : "";
  $("loadRankingPreset").disabled = !select.value;
  $("deleteRankingPreset").disabled = !select.value;
}

function saveRankingPreset() {
  const name = $("rankingPresetName").value.trim();
  const ranking = readRankingCriteria();
  if (!name) {
    $("rankingPresetStatus").textContent = "Enter a preset name before saving.";
    return;
  }
  if (!ranking || !inspectedParquet) {
    $("rankingPresetStatus").textContent = "Inspect a Parquet file and fix the ranking builder before saving.";
    return;
  }
  try {
    const presets = savedRankingPresets().filter(preset => preset.name !== name);
    presets.push({name, mapping: schemaMapping(), filters: readSweepFilters(), ranking, require_robustness: $("requireEntryRobustness").checked, saved_at: new Date().toISOString()});
    persistRankingPresets(presets);
    renderRankingPresets(name);
    $("rankingPresetStatus").textContent = `Saved “${name}” in this browser.`;
  } catch (_) {
    $("rankingPresetStatus").textContent = "This browser could not save the preset.";
  }
}

function loadRankingPreset() {
  if (!inspectedParquet) {
    $("rankingPresetStatus").textContent = "Inspect a Parquet file before loading a preset.";
    return;
  }
  const name = $("rankingPresetSelect").value;
  const preset = savedRankingPresets().find(item => item.name === name);
  if (!preset) return;
  const unavailable = [];
  for (const field of inspectedParquet.fields) {
    const select = document.querySelector(`[data-schema-field="${field.key}"]`);
    const source = preset.mapping?.[field.key] || "";
    if (source && [...select.options].some(option => option.value === source)) select.value = source;
    else {
      select.value = "";
      if (source) unavailable.push(source);
    }
  }
  const filters = preset.filters || {};
  const filterIds = {
    status: "filterStatus", minimum_pnl: "filterMinimumPnl", maximum_pnl: "filterMaximumPnl",
    maximum_drawdown: "filterMaximumDrawdown", latest_entry: "filterLatestEntry", minimum_trades: "filterMinimumTrades",
  };
  Object.entries(filterIds).forEach(([key, id]) => { $(id).value = filters[key] ?? ""; });
  $("requireEntryRobustness").checked = preset.require_robustness === true;
  renderRankingBuilder(Array.isArray(preset.ranking) ? preset.ranking : defaultRankingCriteria());
  validateSchemaMapping();
  $("rankingPresetStatus").textContent = unavailable.length
    ? `Loaded “${name}”. Remap unavailable columns: ${unavailable.join(", ")}.`
    : `Loaded “${name}”.`;
}

function deleteRankingPreset() {
  const name = $("rankingPresetSelect").value;
  if (!name) return;
  try {
    persistRankingPresets(savedRankingPresets().filter(preset => preset.name !== name));
    renderRankingPresets();
    $("rankingPresetStatus").textContent = `Deleted “${name}”.`;
  } catch (_) {
    $("rankingPresetStatus").textContent = "This browser could not delete the preset.";
  }
}

function validateSchemaMapping() {
  if (!inspectedParquet) return;
  const columns = new Map(inspectedParquet.columns.map(column => [column.name, column]));
  const selected = new Map();
  const errors = inspectedParquet.validation.errors
    .map(issue => schemaIssue(issue.code, issue.field, issue.message));
  const warnings = inspectedParquet.validation.warnings
    .map(issue => schemaIssue(issue.code, issue.field, issue.message, "warning"));

  for (const field of inspectedParquet.fields) {
    const select = document.querySelector(`[data-schema-field="${field.key}"]`);
    const source = select?.value || "";
    const typeCell = document.querySelector(`[data-schema-type="${field.key}"]`);
    if (field.required && !source) {
      errors.push(schemaIssue("missing_mapping", field.key, `Map a Parquet column to ${field.label}.`));
      if (typeCell) typeCell.textContent = "—";
      continue;
    }
    if (!source) {
      if (typeCell) typeCell.textContent = "—";
      continue;
    }
    const column = columns.get(source);
    if (!column) {
      errors.push(schemaIssue("unknown_column", field.key, `${field.label} is mapped to a column that is no longer available.`));
      continue;
    }
    if (typeCell) typeCell.textContent = column.type;
    if (!schemaCompatible(field.kind, column.category)) {
      errors.push(schemaIssue("invalid_type", field.key, `${field.label} expects ${field.kind} data; “${source}” is ${column.type}.`));
    }
    if (selected.has(source)) {
      errors.push(schemaIssue("reused_column", field.key, `“${source}” is mapped to both ${selected.get(source)} and ${field.label}.`));
    } else {
      selected.set(source, field.label);
    }
  }

  const mapping = schemaMapping();
  if (!mapping.mtm_drawdown && !(mapping.mtm_dd_2025 && mapping.mtm_dd_2026)) {
    errors.push(schemaIssue("missing_drawdown", "drawdown", "Map overall drawdown, or map both 2025 and 2026 drawdown."));
  }
  if (!mapping.completed_trade_count) {
    warnings.push(schemaIssue("optional_mapping", "completed_trade_count", "Completed trades is not mapped; trade-count filtering will be unavailable.", "warning"));
  }

  $("validationErrorCount").textContent = `${errors.length} ${errors.length === 1 ? "error" : "errors"}`;
  $("validationWarningCount").textContent = `${warnings.length} ${warnings.length === 1 ? "warning" : "warnings"}`;
  $("validationSummary").textContent = errors.length
    ? "Resolve the errors below before this sweep can be ranked."
    : "Schema is ready. These mappings can be used by the ranking controls.";
  $("schemaReadiness").textContent = errors.length ? "Needs attention" : "Ready to rank";
  $("schemaReadiness").className = errors.length ? "negative" : "positive";
  $("filterPreviewButton").disabled = errors.length > 0;
  const ranking = readRankingCriteria();
  $("rankSweepButton").disabled = errors.length > 0 || !ranking;
  $("filterMinimumTrades").disabled = !mapping.completed_trade_count;
  $("filterMinimumTrades").placeholder = mapping.completed_trade_count ? "Optional" : "Map completed trades first";
  $("filterPreviewStatus").textContent = "";
  $("rankedSweep").hidden = true;
  const issues = [...errors, ...warnings];
  if (!issues.length) {
    const item = document.createElement("li");
    item.className = "validation-ok";
    item.textContent = "All required fields are mapped with compatible data types.";
    $("validationIssues").replaceChildren(item);
    return;
  }
  $("validationIssues").replaceChildren(...issues.map(issue => {
    const item = document.createElement("li");
    item.className = `validation-${issue.severity}`;
    const label = document.createElement("strong");
    label.textContent = issue.severity === "error" ? "Error" : "Warning";
    const message = document.createElement("span");
    message.textContent = issue.message;
    item.append(label, message);
    return item;
  }));
}

function renderSchemaInspection(payload) {
  inspectedParquet = payload;
  $("schemaFileName").textContent = payload.file.name;
  $("schemaRowCount").textContent = number.format(payload.file.row_count);
  $("schemaColumnCount").textContent = number.format(payload.columns.length);
  const rows = payload.fields.map(field => {
    const row = document.createElement("tr");
    const labelCell = document.createElement("td");
    const label = document.createElement("strong");
    label.textContent = field.label;
    labelCell.append(label);
    if (!field.required) {
      const optional = document.createElement("small");
      optional.textContent = "Optional";
      labelCell.append(optional);
    }
    const kindCell = document.createElement("td");
    kindCell.textContent = field.kind;
    const selectCell = document.createElement("td");
    const select = document.createElement("select");
    select.dataset.schemaField = field.key;
    select.setAttribute("aria-label", `Parquet column for ${field.label}`);
    select.append(new Option(field.required ? "Choose a column" : "Not mapped", ""));
    for (const column of payload.columns) select.append(new Option(column.name, column.name));
    select.value = field.suggested_source || "";
    select.addEventListener("change", validateSchemaMapping);
    selectCell.append(select);
    if (field.confidence) {
      const hint = document.createElement("small");
      hint.textContent = field.confidence === "exact" ? "Automatically matched" : "Suggested from a similar name";
      selectCell.append(hint);
    }
    const typeCell = document.createElement("td");
    typeCell.dataset.schemaType = field.key;
    typeCell.textContent = payload.columns.find(column => column.name === select.value)?.type || "—";
    row.append(labelCell, kindCell, selectCell, typeCell);
    return row;
  });
  $("schemaMappingRows").replaceChildren(...rows);
  $("parquetWorkspace").hidden = false;
  renderRankingBuilder();
  renderRankingPresets();
  validateSchemaMapping();
}

async function inspectParquet() {
  const file = $("parquetInput").files[0];
  if (!file) {
    $("parquetStatus").textContent = "Choose a Parquet sweep file first.";
    return;
  }
  if (!file.name.toLowerCase().endsWith(".parquet") || file.size === 0) {
    $("parquetStatus").textContent = "Choose a nonempty .parquet file.";
    return;
  }
  if (file.size > 256 * 1024 * 1024) {
    $("parquetStatus").textContent = "This Parquet file is larger than the 256 MiB limit.";
    return;
  }
  $("inspectParquetButton").disabled = true;
  $("parquetInput").disabled = true;
  $("parquetStatus").textContent = "Reading Parquet metadata and detecting column mappings…";
  try {
    const response = await fetch("/api/sweep-schema", {
      method:"POST",
      headers:{"Content-Type":"application/octet-stream", "X-Local-Runner":"1", "X-File-Name":file.name},
      body:file,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "The Parquet schema could not be inspected.");
    renderSchemaInspection(payload);
    $("parquetStatus").textContent = "Schema inspection complete. Review the mappings and validation report.";
  } catch (error) {
    $("parquetWorkspace").hidden = true;
    $("parquetStatus").textContent = error.message;
  } finally {
    $("inspectParquetButton").disabled = false;
    $("parquetInput").disabled = false;
  }
}

async function previewSweepFilters() {
  const file = $("parquetInput").files[0];
  if (!file || !inspectedParquet) {
    $("filterPreviewStatus").textContent = "Inspect a Parquet file before previewing filters.";
    return;
  }
  const filters = readSweepFilters();
  $("filterPreviewButton").disabled = true;
  $("filterPreviewStatus").textContent = "Applying filters to the sweep…";
  try {
    const response = await fetch("/api/sweep-preview", {
      method:"POST",
      headers:{
        "Content-Type":"application/octet-stream",
        "X-Local-Runner":"1",
        "X-Sweep-Mapping":JSON.stringify(schemaMapping()),
        "X-Sweep-Filters":JSON.stringify(filters),
      },
      body:file,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "The filters could not be applied.");
    $("filterPreviewStatus").textContent = `${number.format(payload.matching_rows)} of ${number.format(payload.total_rows)} combinations meet the selected filters.`;
  } catch (error) {
    $("filterPreviewStatus").textContent = error.message;
  } finally {
    $("filterPreviewButton").disabled = false;
  }
}

function parameterColumnLabel(key) {
  return String(key).replace(/_/g, " ").replace(/\b\w/g, character => character.toUpperCase());
}

function rankedParameterColumns(rows) {
  const columns = [];
  const seen = new Set();
  rows.forEach(row => Object.keys(row.parameters || {}).forEach(key => {
    if (!seen.has(key)) { seen.add(key); columns.push(key); }
  }));
  return columns;
}

function renderRankedSweepHeader(parameterColumns) {
  const headerRow = document.createElement("tr");
  const fixedHeaders = [
    ["Rank", ""], ["Final score", "numeric"], ["Net P&L", "numeric"],
    ["2025 P&L", "numeric"], ["2026 P&L", "numeric"], ["Drawdown", "numeric"],
    ["Robustness", ""], ["Entry", ""],
  ];
  fixedHeaders.forEach(([label, className]) => {
    const cell = document.createElement("th");
    cell.textContent = label;
    cell.className = className;
    headerRow.append(cell);
  });
  parameterColumns.forEach(key => {
    const cell = document.createElement("th");
    cell.className = "parameter-column";
    cell.textContent = parameterColumnLabel(key);
    cell.title = key;
    headerRow.append(cell);
  });
  const details = document.createElement("th");
  details.innerHTML = '<span class="sr-only">Details</span>';
  headerRow.append(details);
  $("rankedSweepHead").replaceChildren(headerRow);
}

function robustnessLabel(value, beforeCount = 0, afterCount = 0) {
  const labels = {before: "Before", after: "After", both: "Both"};
  if (value === "not_confirmed") return beforeCount || afterCount ? "Not confirmed" : "No nearby variants";
  const label = labels[value] || "Not confirmed";
  if (value === "both") return `${label} · ${beforeCount}/${afterCount}`;
  return `${label} · ${value === "before" ? beforeCount : afterCount}`;
}

function formatDetailNumber(value) {
  return Number.isFinite(Number(value))
    ? new Intl.NumberFormat("en-IN", {maximumFractionDigits: 4}).format(value)
    : String(value ?? "—");
}

function showStrategyDetail(row, trigger) {
  $("strategyDetailSummary").textContent = `Rank ${row.rank} · Final score ${formatDetailNumber(row.final_score)} · Entry ${row.entry_start} · ${robustnessLabel(row.entry_robustness)} robustness`;
  const componentRows = (row.ranking_components || []).map(component => {
    const element = document.createElement("tr");
    [
      component.label,
      formatDetailNumber(component.value),
      component.direction === "higher" ? "Higher is better" : "Lower is better",
      `${formatDetailNumber(component.weight)}%`,
      formatDetailNumber(component.percentile_score),
      formatDetailNumber(component.weighted_contribution),
    ].forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if ([1, 3, 4, 5].includes(index)) cell.className = "numeric";
      element.append(cell);
    });
    return element;
  });
  $("strategyComponentsRows").replaceChildren(...componentRows);
  const parameterEntries = Object.entries(row.parameters || {});
  const parameterNodes = parameterEntries.length
    ? parameterEntries.flatMap(([key, value]) => {
      const term = document.createElement("dt");
      term.textContent = key;
      const definition = document.createElement("dd");
      definition.textContent = String(value ?? "—");
      return [term, definition];
    })
    : [Object.assign(document.createElement("dd"), {textContent: "No unmapped strategy parameters were found."})];
  $("strategyParameters").replaceChildren(...parameterNodes);
  $("strategyDetail").hidden = false;
  trigger?.setAttribute("aria-expanded", "true");
  $("strategyDetailTitle").focus({preventScroll:true});
}

function closeStrategyDetail() {
  $("strategyDetail").hidden = true;
  document.querySelectorAll("[data-strategy-detail-button]").forEach(button => button.setAttribute("aria-expanded", "false"));
}

function chartScale(values, lowerBound = 0) {
  let low = Math.min(lowerBound, ...values);
  let high = Math.max(lowerBound, ...values);
  if (low === high) { low -= 1; high += 1; }
  return {low, high};
}

function renderSweepScatter(rows) {
  const host = $("sweepScatterChart");
  if (!rows.length) { host.textContent = "No eligible combinations to chart."; return; }
  const width = 560, height = 220, left = 70, right = 24, top = 18, bottom = 38;
  const xScale = chartScale(rows.map(row => Number(row.net_pnl)));
  const yScale = chartScale(rows.map(row => Number(row.ranking_drawdown)));
  const x = value => left + (value - xScale.low) / (xScale.high - xScale.low) * (width - left - right);
  const y = value => top + (yScale.high - value) / (yScale.high - yScale.low) * (height - top - bottom);
  const grid = [0, .5, 1].map(part => {
    const xValue = xScale.low + (xScale.high - xScale.low) * part;
    const yValue = yScale.high - (yScale.high - yScale.low) * part;
    return `<line class="chart-grid" x1="${x(xValue)}" y1="${top}" x2="${x(xValue)}" y2="${height-bottom}"/><line class="chart-grid" x1="${left}" y1="${y(yValue)}" x2="${width-right}" y2="${y(yValue)}"/>`;
  }).join("");
  const points = rows.map(row => `<circle class="sweep-point robustness-${row.entry_robustness}" cx="${x(Number(row.net_pnl))}" cy="${y(Number(row.ranking_drawdown))}" r="4"><title>Rank ${row.rank}: Net P&L ${money.format(row.net_pnl)}, drawdown ${money.format(row.ranking_drawdown)}</title></circle>`).join("");
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${grid}${points}<text class="axis-label" x="${left}" y="${height-8}">Net P&L ${money.format(xScale.low)} – ${money.format(xScale.high)}</text><text class="axis-label" x="${left}" y="14">Drawdown ${money.format(yScale.high)} – ${money.format(yScale.low)}</text></svg>`;
  host.setAttribute("aria-label", `P and L versus drawdown for ${rows.length} displayed ranked strategies.`);
}

function renderSweepYearlyChart(rows) {
  const host = $("sweepYearlyChart");
  if (!rows.length) { host.textContent = "No eligible combinations to chart."; return; }
  const width = 560, height = 220, left = 64, right = 20, top = 18, bottom = 38;
  const scale = chartScale(rows.flatMap(row => [Number(row.pnl_2025), Number(row.pnl_2026)]));
  const y = value => top + (scale.high - value) / (scale.high - scale.low) * (height - top - bottom);
  const slot = (width - left - right) / rows.length;
  const barWidth = Math.max(3, Math.min(15, slot * .3));
  const zero = y(0);
  const bars = rows.map((row, index) => {
    const origin = left + slot * index + slot / 2;
    return [[row.pnl_2025, "sweep-bar-2025", -barWidth - 1, "2025"], [row.pnl_2026, "sweep-bar-2026", 1, "2026"]].map(([value, className, offset, year]) => {
      const py = y(Number(value));
      return `<rect class="${className}" x="${origin + offset}" y="${Math.min(py, zero)}" width="${barWidth}" height="${Math.max(2, Math.abs(zero - py))}" rx="2"><title>Rank ${row.rank}, ${year}: ${money.format(value)}</title></rect>`;
    }).join("") + (index % Math.ceil(rows.length / 8) === 0 || index === rows.length - 1 ? `<text class="axis-label" x="${origin}" y="${height-10}" text-anchor="middle">#${row.rank}</text>` : "");
  }).join("");
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true"><line class="zero-line" x1="${left}" y1="${zero}" x2="${width-right}" y2="${zero}"/>${bars}<text class="axis-label" x="${left}" y="14">${money.format(scale.high)}</text><text class="axis-label" x="${left}" y="${height-25}">${money.format(scale.low)}</text></svg>`;
  host.setAttribute("aria-label", `2025 and 2026 P and L comparison for ${rows.length} displayed ranked strategies.`);
}

function renderSweepDistribution(distribution) {
  const host = $("sweepDistributionChart");
  const items = [["Before", distribution.before || 0, "robustness-before"], ["After", distribution.after || 0, "robustness-after"], ["Both", distribution.both || 0, "robustness-both"], ["Not confirmed", distribution.not_confirmed || 0, "robustness-not_confirmed"]];
  const width = 360, height = 220, left = 38, right = 18, top = 18, bottom = 38;
  const high = Math.max(1, ...items.map(([, value]) => Number(value)));
  const slot = (width - left - right) / items.length;
  const y = value => top + (high - value) / high * (height - top - bottom);
  const bars = items.map(([label, value, className], index) => {
    const barWidth = slot * .55, x = left + index * slot + (slot - barWidth) / 2, py = y(Number(value));
    return `<rect class="sweep-distribution-bar ${className}" x="${x}" y="${py}" width="${barWidth}" height="${height-bottom-py}" rx="3"><title>${label}: ${number.format(value)}</title></rect><text class="axis-label" x="${x+barWidth/2}" y="${height-10}" text-anchor="middle">${label}</text><text class="axis-label" x="${x+barWidth/2}" y="${py-5}" text-anchor="middle">${number.format(value)}</text>`;
  }).join("");
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${bars}</svg>`;
  host.setAttribute("aria-label", `Eligible strategy distribution: ${items.map(([label, value]) => `${label} ${value}`).join(", ")}.`);
}

function renderSweepCharts(payload) {
  renderSweepScatter(payload.rows || []);
  renderSweepYearlyChart(payload.rows || []);
  renderSweepDistribution(payload.robustness_distribution || {});
}

function zeroEligibleReason(payload) {
  const diagnostics = payload.diagnostics || {};
  const baseCount = Number(diagnostics.base_count || 0);
  const windowCount = Number(diagnostics.base_with_window_variants || 0);
  const passCount = Number(diagnostics.robustness_pass_count || 0);
  const windowMinutes = diagnostics.robustness_window_minutes || 20;
  if (!baseCount) {
    return "No rows passed the base filters. Check status, P&L, drawdown, trade count, and latest entry time.";
  }
  if (!windowCount) {
    const nearest = diagnostics.nearest_variant_minutes == null
      ? "No same-parameter entry variants were found."
      : `Nearest same-parameter variant is ${diagnostics.nearest_variant_minutes} minutes away.`;
    return `${number.format(baseCount)} rows passed the base filters, but none had same-parameter entry variants within ±${windowMinutes} minutes. ${nearest}`;
  }
  if (!passCount) {
    return `${number.format(baseCount)} rows passed the base filters and ${number.format(windowCount)} had nearby variants, but none retained at least 70% of base P&L on a full before or after side.`;
  }
  return "No combination passed the selected filters and available entry-time robustness checks.";
}

function formatFileSize(bytes) {
  if (!Number.isFinite(Number(bytes))) return "—";
  if (bytes < 1024 * 1024) return `${number.format(bytes / 1024)} KiB`;
  return `${number.format(bytes / (1024 * 1024))} MiB`;
}

function runSummaryEntries(summary) {
  const filters = summary.filters || {};
  const filterSummary = [
    `Status: ${filters.status || "any"}`,
    `Net P&L > ${filters.minimum_pnl || "none"}`,
    `Net P&L ≤ ${filters.maximum_pnl || "none"}`,
    `Drawdown ≤ ${filters.maximum_drawdown || "none"}`,
    `Entry ≤ ${filters.latest_entry || "any"}`,
    `Trades ≥ ${filters.minimum_trades || "none"}`,
  ].join(" · ");
  const rankingSummary = (summary.ranking || []).map(item =>
    `${item.weight}% ${item.label} (${item.direction === "higher" ? "higher" : "lower"})`
  ).join(" · ");
  const mappingSummary = Object.entries(summary.column_mapping || {})
    .filter(([, source]) => source).map(([field, source]) => `${field} → ${source}`).join(" · ");
  const robustness = summary.entry_robustness || {};
  return [
    ["Input", `${summary.input_file || "—"} · ${formatFileSize(summary.input_size_bytes)} · ${number.format(summary.input_rows || 0)} rows`],
    ["Timestamp (UTC)", summary.timestamp_utc || "—"],
    ["Filters", filterSummary],
    ["Ranking", rankingSummary],
    ["Column mapping", mappingSummary || "—"],
    ["Robustness rule", `${robustness.required ? "Required for inclusion" : "Shown as an annotation"} · ${Math.round((robustness.threshold_ratio || 0) * 100)}% of base P&L · all available variants within ±${robustness.window_minutes || "—"} minutes`],
  ];
}

function renderRunSummary(summary) {
  const section = $("runSummary");
  if (!summary) { section.hidden = true; return; }
  section.hidden = false;
  const nodes = runSummaryEntries(summary).flatMap(([key, value]) => {
    const term = document.createElement("dt");
    term.textContent = key;
    const definition = document.createElement("dd");
    definition.textContent = value;
    return [term, definition];
  });
  $("runSummaryRows").replaceChildren(...nodes);
  $("copyRunSummaryStatus").textContent = "";
}

async function copyRunSummary() {
  const summary = rankedSweepPayload?.run_summary;
  if (!summary) return;
  const text = runSummaryEntries(summary).map(([key, value]) => `${key}: ${value}`).join("\n");
  try {
    await navigator.clipboard.writeText(text);
    $("copyRunSummaryStatus").textContent = "Copied.";
  } catch (_) {
    $("copyRunSummaryStatus").textContent = "Copy was blocked by this browser. Select the summary text instead.";
  }
}

function renderRankedSweep(payload) {
  rankedSweepPayload = payload;
  const robustnessRequired = Boolean(payload.run_summary?.entry_robustness?.required);
  const parameterColumns = rankedParameterColumns(payload.rows || []);
  renderRankedSweepHeader(parameterColumns);
  const rankingDescription = (payload.ranking || []).map(criterion =>
    `${criterion.weight}% ${criterion.label} (${criterion.direction === "higher" ? "higher" : "lower"} is better)`
  ).join(" · ");
  $("rankedSweepTitle").textContent = robustnessRequired ? "Top 20 robust combinations" : "Top 20 ranked combinations";
  $("rankedSweepSummary").textContent = payload.eligible_count
    ? robustnessRequired
      ? `${number.format(payload.eligible_count)} combinations passed all filters and required entry-time robustness. Score: ${rankingDescription}.`
      : `${number.format(payload.eligible_count)} base combinations passed the selected filters and were ranked one by one. Robustness is shown as an annotation. Score: ${rankingDescription}.`
    : zeroEligibleReason(payload);
  const rows = payload.rows.map(row => {
    const element = document.createElement("tr");
    const values = [
      row.rank,
      number.format(row.final_score),
      money.format(row.net_pnl),
      money.format(row.pnl_2025),
      money.format(row.pnl_2026),
      money.format(row.ranking_drawdown),
      robustnessLabel(row.entry_robustness, row.before_variant_count, row.after_variant_count),
      row.entry_start,
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if ([1, 2, 3, 4, 5].includes(index)) cell.className = "numeric";
      if (index === 6) {
        cell.className = "robustness-cell";
        const badge = document.createElement("span");
        badge.className = `robustness-badge robustness-${row.entry_robustness}`;
        badge.textContent = value;
        badge.title = `${row.before_variant_count || 0} earlier and ${row.after_variant_count || 0} later available variants were tested.`;
        cell.replaceChildren(badge);
      }
      element.append(cell);
    });
    parameterColumns.forEach(key => {
      const cell = document.createElement("td");
      const value = row.parameters?.[key];
      cell.className = "parameter-value";
      cell.textContent = value == null || value === "" ? "—" : formatDetailNumber(value);
      element.append(cell);
    });
    const detailCell = document.createElement("td");
    const detailButton = document.createElement("button");
    detailButton.type = "button";
    detailButton.className = "secondary-button strategy-detail-button";
    detailButton.dataset.strategyDetailButton = "1";
    detailButton.textContent = "Details";
    detailButton.setAttribute("aria-expanded", "false");
    detailButton.addEventListener("click", () => {
      document.querySelectorAll("[data-strategy-detail-button]").forEach(button => button.setAttribute("aria-expanded", "false"));
      showStrategyDetail(row, detailButton);
    });
    detailCell.append(detailButton);
    element.append(detailCell);
    return element;
  });
  if (!rows.length) {
    const empty = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 9 + parameterColumns.length;
    cell.className = "empty-table";
    cell.textContent = zeroEligibleReason(payload);
    empty.append(cell);
    rows.push(empty);
  }
  $("rankedSweepRows").replaceChildren(...rows);
  closeStrategyDetail();
  renderRunSummary(payload.run_summary);
  renderSweepCharts(payload);
  ["exportRankedCsv", "exportRankedParquet", "exportRankedExcel"].forEach(id => {
    $(id).disabled = !payload.rows?.length;
  });
  $("rankedSweep").hidden = false;
  $("rankedSweepTitle").focus({preventScroll:true});
}

async function exportRankedSweep(exportFormat) {
  if (!rankedSweepPayload?.rows?.length) {
    $("filterPreviewStatus").textContent = "Rank at least one eligible combination before exporting.";
    return;
  }
  const buttons = ["exportRankedCsv", "exportRankedParquet", "exportRankedExcel"].map($);
  buttons.forEach(button => button.disabled = true);
  $("filterPreviewStatus").textContent = `Creating ${exportFormat.toUpperCase()} export…`;
  try {
    const response = await fetch("/api/sweep-export", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Local-Runner": "1"},
      body: JSON.stringify({format: exportFormat, rows: rankedSweepPayload.rows}),
    });
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(payload.error || "The export could not be created.");
    }
    const blob = await response.blob();
    const extension = {csv: "csv", parquet: "parquet", xlsx: "xlsx"}[exportFormat];
    const link = document.createElement("a");
    const objectUrl = URL.createObjectURL(blob);
    link.href = objectUrl;
    link.download = `ranked_strategies.${extension}`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    $("filterPreviewStatus").textContent = `${exportFormat.toUpperCase()} export downloaded.`;
  } catch (error) {
    $("filterPreviewStatus").textContent = error.message;
  } finally {
    buttons.forEach(button => button.disabled = false);
  }
}

function setRankingProgress(value, label) {
  const meter = $("rankProgressMeter");
  $("rankProgress").hidden = false;
  $("rankProgressLabel").textContent = label;
  if (value == null) meter.removeAttribute("value");
  else meter.value = value;
}

function startRankingProgress() {
  rankingStartedAt = performance.now();
  clearInterval(rankingElapsedTimer);
  $("rankProgressElapsed").textContent = "Elapsed 0s";
  setRankingProgress(0, "Preparing Parquet ranking…");
  rankingElapsedTimer = setInterval(() => {
    const seconds = Math.floor((performance.now() - rankingStartedAt) / 1000);
    $("rankProgressElapsed").textContent = `Elapsed ${seconds}s`;
  }, 250);
}

function finishRankingProgress(value, label) {
  clearInterval(rankingElapsedTimer);
  const seconds = (performance.now() - rankingStartedAt) / 1000;
  $("rankProgressElapsed").textContent = `Elapsed ${seconds.toFixed(1)}s`;
  setRankingProgress(value, label);
  return seconds;
}

async function rankSweep() {
  const file = $("parquetInput").files[0];
  if (!file || !inspectedParquet) {
    $("filterPreviewStatus").textContent = "Inspect a Parquet file before ranking.";
    return;
  }
  const filters = readSweepFilters();
  const ranking = readRankingCriteria();
  if (!ranking) return;
  $("rankSweepButton").disabled = true;
  $("filterPreviewButton").disabled = true;
  $("filterPreviewStatus").textContent = "Uploading and ranking the Parquet sweep…";
  startRankingProgress();
  try {
    const payload = await new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", "/api/sweep-rank");
      request.responseType = "json";
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.setRequestHeader("X-Local-Runner", "1");
      request.setRequestHeader("X-Sweep-File-Name", file.name);
      request.setRequestHeader("X-Sweep-Mapping", JSON.stringify(schemaMapping()));
      request.setRequestHeader("X-Sweep-Filters", JSON.stringify(filters));
      request.setRequestHeader("X-Sweep-Ranking", JSON.stringify(ranking));
      request.setRequestHeader("X-Sweep-Robustness", String($("requireEntryRobustness").checked));
      request.upload.addEventListener("progress", event => {
        if (!event.lengthComputable) return;
        const percentage = Math.round(event.loaded / event.total * 100);
        setRankingProgress(Math.min(50, percentage / 2), `Uploading Parquet sweep: ${percentage}%`);
      });
      request.upload.addEventListener("load", () => {
        setRankingProgress(null, $("requireEntryRobustness").checked
          ? "Upload complete. Server is applying filters and requiring entry-time robustness…"
          : "Upload complete. Server is applying filters, ranking base combinations, and annotating robustness…");
      });
      request.addEventListener("load", () => {
        const response = request.response || {};
        if (request.status >= 200 && request.status < 300) resolve(response);
        else reject(new Error(response.error || "The sweep could not be ranked."));
      });
      request.addEventListener("error", () => reject(new Error("The ranked sweep upload failed. Check the local dashboard connection.")));
      request.send(file);
    });
    renderRankedSweep(payload);
    const elapsed = finishRankingProgress(100, "Ranking complete.");
    const summary = `${number.format(payload.source_row_count || 0)} source rows scanned · ${number.format(payload.eligible_count)} eligible · ${number.format(payload.rows.length)} displayed · ${elapsed.toFixed(1)}s`;
    $("filterPreviewStatus").textContent = summary;
  } catch (error) {
    finishRankingProgress(0, "Ranking stopped before completion.");
    $("filterPreviewStatus").textContent = error.message;
  } finally {
    $("rankSweepButton").disabled = false;
    $("filterPreviewButton").disabled = false;
  }
}

$("parquetModeButton").addEventListener("click", showParquetMode);
$("parquetDoneButton").addEventListener("click", showRunner);
$("inspectParquetButton").addEventListener("click", inspectParquet);
$("filterPreviewButton").addEventListener("click", previewSweepFilters);
$("rankSweepButton").addEventListener("click", rankSweep);
$("closeStrategyDetail").addEventListener("click", closeStrategyDetail);
$("copyRunSummary").addEventListener("click", copyRunSummary);
$("exportRankedCsv").addEventListener("click", () => exportRankedSweep("csv"));
$("exportRankedParquet").addEventListener("click", () => exportRankedSweep("parquet"));
$("exportRankedExcel").addEventListener("click", () => exportRankedSweep("xlsx"));
$("saveRankingPreset").addEventListener("click", saveRankingPreset);
$("loadRankingPreset").addEventListener("click", loadRankingPreset);
$("deleteRankingPreset").addEventListener("click", deleteRankingPreset);
$("rankingPresetSelect").addEventListener("change", () => {
  const selected = $("rankingPresetSelect").value;
  $("loadRankingPreset").disabled = !selected;
  $("deleteRankingPreset").disabled = !selected;
});
$("addRankingCriterion").addEventListener("click", () => {
  const criteria = [...document.querySelectorAll("[data-ranking-row]")].map(row => ({
    column: row.querySelector("[data-ranking-column]").value,
    weight: row.querySelector("[data-ranking-weight]").value,
    direction: row.querySelector("[data-ranking-direction]").value,
  }));
  if (criteria.length >= 6) return;
  const used = new Set(criteria.map(criterion => criterion.column));
  const next = rankingColumnOptions().find(option => !used.has(option.value));
  criteria.push({column: next?.value || "", weight: 1, direction: "higher"});
  renderRankingBuilder(criteria);
  validateSchemaMapping();
});

$("strategyFiles").addEventListener("change", () => {
  $("entrypoint").value = $("strategyFiles").files[0]?.name || "";
});

function busyRun(busy) {
  state.uploading = busy;
  for (const id of ["runButton", "csvModeButton", "parquetModeButton", "chooseButton", "replaceButton", "equityButton", "historyButton", "storageButton"]) $(id).disabled = busy;
  if (busy) $("cancelRun").textContent = "Cancel run";
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
  $("runDownloads").replaceChildren(document.createTextNode("Saved run outputs:"), ...names.map(name => {
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
    state.fileName = `Saved run · ${record.strategy}`;
    $("historyPanel").hidden = true;
    hideStorage();
    $("sweepPanel").hidden = true;
    $("runnerPanel").hidden = true;
    $("parquetPanel").hidden = true;
    render(record.analysis, record.parameters);
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
  $("parquetPanel").hidden = true;
  $("uploadView").hidden = true;
  $("dashboard").hidden = true;
  $("sweepPanel").hidden = true;
  $("runDownloads").hidden = true;
  $("replaceButton").hidden = true;
  $("backToSweepButton").hidden = true;
  hideStorage();
  $("historyPanel").hidden = false;
  $("historyStatus").textContent = "Loading saved results…";
  try {
    const response = await fetch("/api/history");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Saved-result history could not be loaded.");
    const rows = result.history || [];
    $("historyCount").textContent = `${number.format(rows.length)} saved`;
    $("historyStatus").textContent = rows.length ? "Saved parameters and sweep metrics are available here without rerunning." : "No sweep result has been saved yet.";
    $("historyRows").innerHTML = rows.map(record => {
      const metrics = record.metrics || {};
      const drawdown = metrics.intraday_drawdown == null ? metrics.max_drawdown : metrics.intraday_drawdown;
      const dataset = record.dataset?.label || "Custom data";
      const action = record.kind === "combination"
        ? "Parameters saved"
        : `<button class="secondary-button history-open" data-history-id="${record.id}" type="button">View analytics</button>`;
      return `<tr><td>${historyDate.format(new Date(record.created_at))}</td><td>${escapeHtml(record.strategy || "Strategy")}</td><td>${escapeHtml(dataset)}</td><td class="sweep-parameters">${escapeHtml(parameterText(record.parameters))}</td><td class="numeric">${record.selection_score == null ? "—" : number.format(record.selection_score)}</td><td class="numeric ${Number(metrics.net_pnl) > 0 ? "positive" : ""}">${money.format(metrics.net_pnl)}</td><td class="numeric ${Number(drawdown) < 0 ? "negative" : ""}">${money.format(drawdown)}</td><td>${action}</td></tr>`;
    }).join("") || '<tr><td colspan="8" class="empty-table">Run a sweep and save any completed combination here.</td></tr>';
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

async function saveSweepCombination(index, button) {
  if (!sweepRun || state.uploading) return;
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    const response = await fetch(`/api/backtests/${sweepRun}/iterations/${index}/save`, {
      method: "POST", headers: {"X-Local-Runner": "1"}
    });
    const record = await response.json();
    if (!response.ok) throw new Error(record.error || "Could not save this combination.");
    button.textContent = "Saved";
    $("sweepSaveStatus").textContent = `Combination ${index + 1} saved to history.`;
  } catch (error) {
    button.disabled = false;
    button.textContent = "Save combination";
    $("sweepSaveStatus").textContent = error.message;
  }
}

async function openSweepIteration(index, button) {
  if (!sweepRun || state.uploading) return;
  const original = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "Starting…";
  }
  try {
    const response = await fetch(`/api/backtests/${sweepRun}/iterations/${index}/run`, {
      method:"POST", headers: {"X-Local-Runner":"1"}
    });
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

function renderSweep(sweep, id, {partial = false} = {}) {
  sweepRun = id;
  $("sweepSaveStatus").textContent = "";
  selectedSweepIndex = null;
  recommendedSweepIndex = sweep.recommended_index;
  $("viewSelectedSweep").disabled = true;
  $("saveSelectedSweep").disabled = true;
  const processed = sweep.processed_count ?? sweep.iterations.length;
  $("sweepCount").textContent = partial ? `${processed} of ${sweep.iteration_count} complete` : `${sweep.iteration_count} variations`;
  $("resumeSweepButton").hidden = !partial || processed >= sweep.iteration_count;
  $("resumeSweepButton").disabled = false;
  $("resumeSweepButton").textContent = "Resume remaining";
  $("sweepDescription").textContent = partial
    ? "This sweep was stopped. These are the combinations completed before it stopped; unfinished combinations are not shown."
    : "Each row is an independent backtest against the same dataset and dates. Review the results before choosing a full analytics run.";
  const partialPrefix = partial ? `Stopped after ${number.format(processed)} of ${number.format(sweep.iteration_count)} combinations. ` : "";
  $("sweepNote").textContent = sweep.provisional
    ? `${number.format(sweep.processed_count || 0)} combinations screened in Numba chunks. Showing the top ${number.format(sweep.displayed_count || sweep.iterations.length)} provisional candidates by numeric net P&L. These rows are not final analytics; rerun a candidate through the oracle before saving.`
    : `${partialPrefix}${number.format(sweep.ranked_count || 0)} profitable candidates ranked · ${number.format(sweep.no_trade_count || 0)} without trades · ${number.format(sweep.failed_count || 0)} failed. Each metric is split into four groups: +3, +2, −2, and −3 points from best to worst. Drawdown and loss streak run in reverse, so lower values earn more points. Recent yearly P&L still gives newer years more weight. Review the comparison, then run any combination for full analytics or save it to history.`;
  const parameterKeys = [...new Set(sweep.iterations.flatMap(item => Object.keys(item.parameters || {})))];
  $("sweepHead").innerHTML = `<tr><th>Select</th><th>Rank</th><th>Combination</th>${parameterKeys.map(key => `<th>${escapeHtml(key)}</th>`).join("")}<th class="numeric">Score</th><th class="numeric">Net P&amp;L</th><th>Recent P&amp;L</th><th class="numeric">Max drawdown</th><th class="numeric">Win rate</th><th class="numeric">Max consecutive losses</th><th class="numeric">Average profit</th><th class="numeric">Average loss</th><th class="numeric">Avg win/loss</th><th class="numeric">Profit factor</th><th class="numeric">Trades</th><th><span class="sr-only">Action</span></th></tr>`;
  const rankedRows = [...sweep.iterations].sort((left, right) =>
    (left.rank ?? Number.MAX_SAFE_INTEGER) - (right.rank ?? Number.MAX_SAFE_INTEGER) || left.index - right.index
  );
  $("sweepRows").innerHTML = rankedRows.map(item => {
    const metrics = item.metrics;
    const selector = `<input class="sweep-select" type="radio" name="sweep-selection" value="${item.index}" aria-label="Select combination ${item.index + 1}" ${metrics ? "" : "disabled"}>`;
    const parameterCells = parameterKeys.map(key => `<td class="sweep-parameter-value">${item.parameters && Object.hasOwn(item.parameters, key) ? escapeHtml(JSON.stringify(item.parameters[key])) : "—"}</td>`).join("");
    if (!metrics) {
      const message = item.status === "failed" ? escapeHtml(item.error || "Variation failed") : "No completed trades";
      return `<tr data-index="${item.index}"><td>${selector}</td><td>—</td><td>${item.index + 1}</td>${parameterCells}<td colspan="11" class="sweep-empty">${message}</td><td><button class="secondary-button" type="button" disabled>Unavailable</button></td></tr>`;
    }
    const net = Number(metrics.net_pnl);
    const drawdown = metrics.intraday_drawdown == null ? metrics.max_drawdown : metrics.intraday_drawdown;
    const ratio = metrics.average_win_loss_ratio == null ? (metrics.average_profit != null && metrics.average_loss == null ? "∞" : "—") : number.format(metrics.average_win_loss_ratio);
    const recentPnl = Object.entries(metrics.yearly_net_pnl || {}).sort(([left], [right]) => right.localeCompare(left)).slice(0, 2).map(([year, pnl]) => `${escapeHtml(year)} ${money.format(pnl)}`).join(" · ") || "—";
    const recommended = item.index === sweep.recommended_index ? `<span class="comparison-badge">${sweep.provisional ? "Top provisional" : "Recommended"}</span>` : '';
    const componentTitle = metrics.selection_components ? escapeHtml(`Points — Total P&L ${metrics.selection_components.net_pnl} · Recent years ${metrics.selection_components.recent_year_pnl} · Drawdown ${metrics.selection_components.drawdown} · Win/loss ${metrics.selection_components.average_win_loss_ratio} · Win rate ${metrics.selection_components.win_rate} · Loss streak ${metrics.selection_components.max_consecutive_losses}`) : "";
    const provisionalCells = sweep.provisional
      ? `<td class="numeric">—</td><td class="numeric ${net > 0 ? "positive" : net < 0 ? "negative" : ""}">${money.format(net)}</td><td>—</td><td class="numeric ${Number(drawdown) < 0 ? "negative" : ""}">${money.format(drawdown)}</td><td class="numeric">—</td><td class="numeric">—</td><td class="numeric">—</td><td class="numeric">—</td><td class="numeric">—</td><td class="numeric">${number.format(metrics.completed_trade_count)}</td>`
      : `<td class="numeric" title="${componentTitle}">${metrics.selection_score == null ? "—" : number.format(metrics.selection_score)}</td><td class="numeric ${net > 0 ? "positive" : net < 0 ? "negative" : ""}">${money.format(net)}</td><td>${recentPnl}</td><td class="numeric ${Number(drawdown) < 0 ? "negative" : ""}">${money.format(drawdown)}</td><td class="numeric">${number.format(metrics.win_rate)}%</td><td class="numeric">${number.format(metrics.max_consecutive_losses)}</td><td class="numeric positive">${metrics.average_profit == null ? "—" : money.format(metrics.average_profit)}</td><td class="numeric ${Number(metrics.average_loss) < 0 ? "negative" : ""}">${metrics.average_loss == null ? "—" : money.format(metrics.average_loss)}</td><td class="numeric">${ratio}</td><td class="numeric">${metrics.profit_factor == null ? "—" : number.format(metrics.profit_factor)}</td><td class="numeric">${metrics.batch_count}</td>`;
    const saveButton = sweep.provisional ? `<button class="primary-button sweep-save" data-index="${item.index}" type="button" disabled>Save after oracle rerun</button>` : `<button class="primary-button sweep-save" data-index="${item.index}" type="button">Save combination</button>`;
    return `<tr data-index="${item.index}"><td>${selector}</td><td>${item.rank ?? "—"}</td><td>${item.index + 1}${recommended}</td>${parameterCells}${provisionalCells}<td><div class="sweep-row-actions"><button class="secondary-button sweep-open" data-index="${item.index}" type="button">${sweep.provisional ? "Rerun oracle" : "Run &amp; view"}</button>${saveButton}</div></td></tr>`;
  }).join("");
  $("sweepRows").querySelectorAll(".sweep-select").forEach(input => input.addEventListener("change", () => {
    selectedSweepIndex = Number(input.value);
    $("viewSelectedSweep").disabled = false;
    $("saveSelectedSweep").disabled = Boolean(sweep.provisional);
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
  $("sweepRows").querySelectorAll(".sweep-save").forEach(button => button.addEventListener("click", () => {
    const index = Number(button.dataset.index);
    const input = document.querySelector(`.sweep-select[value="${index}"]`);
    if (input) {
      input.checked = true;
      input.dispatchEvent(new Event("change"));
    }
    saveSweepCombination(index, button);
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
  if (selectedSweepIndex !== null) {
    openSweepIteration(
      selectedSweepIndex,
      $("viewSelectedSweep"),
    );
  }
});

$("saveSelectedSweep").addEventListener("click", () => {
  if (selectedSweepIndex !== null) {
    saveSweepCombination(selectedSweepIndex, $("saveSelectedSweep"));
  }
});

$("resumeSweepButton").addEventListener("click", async () => {
  if (!sweepRun || state.uploading) return;
  const button = $("resumeSweepButton");
  button.disabled = true;
  button.textContent = "Resuming…";
  try {
    const response = await fetch(`/api/backtests/${sweepRun}/resume`, {
      method: "POST", headers: {"X-Local-Runner": "1"}
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not resume this sweep.");
    activeRun = result.id;
    sessionStorage.setItem("activeBacktest", activeRun);
    busyRun(true);
    $("sweepPanel").hidden = true;
    $("runnerPanel").hidden = false;
    $("runStatus").textContent = "Resuming the unfinished sweep combinations…";
    $("runLog").textContent = "";
    $("runLogPanel").hidden = false;
    $("runnerPanel").scrollIntoView();
    await pollRun();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Resume remaining";
    $("sweepNote").textContent = error.message;
  }
});

function duration(seconds) {
  if (seconds == null) return "";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.ceil((seconds % 3600) / 60)}m`;
}

function elapsedTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainingSeconds = total % 60;
  return [hours, minutes, remainingSeconds]
    .map(value => String(value).padStart(2, "0"))
    .join(":");
}

function finishTime(secondsRemaining) {
  if (secondsRemaining == null) return "estimating";
  const finish = new Date(Date.now() + secondsRemaining * 1000);
  return finish.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
}

function showBacktestTimer(seconds, running) {
  clearInterval(elapsedTimer);
  const timer = $("backtestTimer");
  const value = $("backtestTimerValue");
  timer.hidden = false;
  const baseSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const observedAt = Date.now();
  const render = () => {
    const increment = running ? Math.floor((Date.now() - observedAt) / 1000) : 0;
    value.textContent = elapsedTime(baseSeconds + increment);
  };
  render();
  if (running) elapsedTimer = setInterval(render, 1000);
}

function stopBacktestTimer(hide = false) {
  clearInterval(elapsedTimer);
  elapsedTimer = null;
  if (hide) $("backtestTimer").hidden = true;
}

async function pollRun() {
  const id = activeRun;
  if (!id) return;
  try {
    const response = await fetch(`/api/backtests/${id}`);
    const job = await response.json();
    if (!response.ok) {
      if (response.status === 404) {
        stopBacktestTimer(true);
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
    showBacktestTimer(job.elapsed_seconds, job.status === "running");
    if (job.progress?.mode === "sweep") {
      $("cancelRun").textContent = "Stop & review completed";
      const progress = job.progress;
      const current = progress.current;
      const currentElapsed = Number(progress.combination_elapsed_seconds || 0)
        + (job.status === "running" ? Math.max(0, Date.now() / 1000 - Number(progress.updated_at || Date.now() / 1000)) : 0);
      const currentPercent = progress.current_percentage == null
        ? "preparing current combination"
        : `${Number(progress.current_percentage).toFixed(1)}% through current combination`;
      const currentEta = progress.current_estimated_remaining_seconds == null
        ? "ETA starts after its first completed work unit"
        : `~${duration(progress.current_estimated_remaining_seconds)} remaining`;
      const average = progress.average_combination_seconds == null
        ? "Average per combination: calculating"
        : `Average per combination: ${duration(progress.average_combination_seconds)}`;
      const overallEta = progress.estimated_remaining_seconds == null
        ? "Estimated remaining: calculating"
        : `Estimated remaining: ${duration(progress.estimated_remaining_seconds)}`;
      const lines = [
        `Sweep ${job.status} · ${progress.completed} of ${progress.total} combinations complete`,
        current ? `Running combination ${current} of ${progress.total} · ${currentPercent}` : "All combinations processed",
        current ? `Current run: ${elapsedTime(currentElapsed)} elapsed · ${currentEta}` : null,
        `Overall: ${progress.completed}/${progress.total} complete · ${elapsedTime(job.elapsed_seconds)} elapsed`,
        average,
        overallEta,
        `Estimated finish: ${finishTime(progress.estimated_remaining_seconds)}`,
      ];
      $("runStatus").textContent = lines.filter(Boolean).join("\n");
    } else if (job.progress?.mode === "single") {
      const completed = job.progress.completed || 0;
      const total = job.progress.total || 0;
      const remaining = Math.max(0, total - completed);
      const unit = job.progress.unit || "item";
      const plural = total === 1 ? unit : `${unit}s`;
      const percentage = Number(job.progress.percentage || 0).toFixed(1);
      const speed = job.progress.average_per_second == null
        ? "estimating speed"
        : `${Number(job.progress.average_per_second).toFixed(2)} ${plural}/sec`;
      const eta = job.progress.estimated_remaining_seconds == null
        ? "estimating ETA"
        : `${duration(job.progress.estimated_remaining_seconds)} remaining`;
      const phase = job.progress.phase ? `${job.progress.phase} · ` : "";
      $("runStatus").textContent = `Backtest ${job.status} · ${phase}${completed}/${total} ${plural} complete (${percentage}%) · ${remaining} remaining · ${speed} · ${eta} · finishes ${finishTime(job.progress.estimated_remaining_seconds)}`;
    } else {
      $("runStatus").textContent = `Backtest ${job.status} · preparing market data; ETA starts with the first completed trading day`;
    }
    if (job.status === "running") {
      pollTimer = setTimeout(pollRun, 1500);
      return;
    }
    if (job.status === "succeeded") {
      if (job.result.mode === "sweep") {
        renderSweep(job.result.sweep, id);
        activeRun = null;
        sessionStorage.removeItem("activeBacktest");
        busyRun(false);
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
      if (job.history_id) $("runMeta").textContent += " · Saved to history";
      if (job.history_error) $("runStatus").textContent = job.history_error;
    } else if (job.status === "cancelled" && job.partial_sweep?.iterations?.length) {
      renderSweep(job.partial_sweep, id, {partial: true});
      activeRun = null;
      sessionStorage.removeItem("activeBacktest");
      busyRun(false);
      return;
    } else if (job.status === "cancelled") {
      $("runStatus").textContent = "Sweep stopped before a combination completed.";
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
    stopBacktestTimer();
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
    showBacktestTimer(0, true);
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
  stopBacktestTimer();
  $("runStatus").textContent = $("cancelRun").textContent === "Stop & review completed"
    ? "Stopping sweep and preparing completed combinations…"
    : "Cancelling backtest…";
  try {
    const response = await fetch(`/api/backtests/${activeRun}/cancel`, {method:"POST", headers:{"X-Local-Runner":"1"}});
    if (!response.ok) throw new Error("Could not cancel the run. Check that the local server is running.");
    clearTimeout(pollTimer);
    await pollRun();
  } catch (error) {
    $("runStatus").textContent = error.message;
  }
});

const linkedRun = location.hash.match(/^#backtest=([a-f0-9]{32})$/)?.[1];
if (linkedRun) {
  sessionStorage.setItem("activeBacktest", linkedRun);
  history.replaceState(null, "", location.pathname);
}
const savedRun = sessionStorage.getItem("activeBacktest");
if (savedRun) { activeRun = savedRun; busyRun(true); pollRun(); }
