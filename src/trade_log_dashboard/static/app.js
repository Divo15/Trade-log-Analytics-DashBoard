const state = { fileName: "trades.csv", data: null, uploading: false, tradeFile: null };

const $ = (id) => document.getElementById(id);
const money = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const shortDate = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" });

function setSignedClass(element, value) {
  element.classList.remove("positive", "negative");
  if (Number(value) > 0) element.classList.add("positive");
  if (Number(value) < 0) element.classList.add("negative");
}

function setMoney(id, value) {
  const element = $(id);
  element.textContent = value == null ? "—" : money.format(value);
  setSignedClass(element, value);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function lineChart(rows) {
  const host = $("equityChart");
  if (!rows.length) return;
  const width = 760, height = 280, left = 80, right = 55, top = 20, bottom = 34;
  const values = rows.flatMap((row) => [Number(row.equity), Number(row.drawdown)]);
  let min = values.reduce((a, b) => Math.min(a, b), 0), max = values.reduce((a, b) => Math.max(a, b), 0);
  if (max === min) { max += 1; min -= 1; }
  const x = (index) => left + index * (width - left - right) / Math.max(rows.length - 1, 1);
  const y = (value) => top + (max - value) * (height - top - bottom) / (max - min);
  const equityPoints = rows.map((row, index) => `${x(index)},${y(Number(row.equity))}`).join(" ");
  const areaPoints = `${left},${y(0)} ${equityPoints} ${x(rows.length - 1)},${y(0)}`;
  const drawdownPoints = rows.map((row, index) => `${x(index)},${y(Number(row.drawdown))}`).join(" ");
  const drawdownArea = `${left},${y(0)} ${drawdownPoints} ${x(rows.length - 1)},${y(0)}`;
  const grid = [0, 0.25, 0.5, 0.75, 1].map((part) => {
    const value = max - (max - min) * part;
    const py = y(value);
    return `<line class="chart-grid" x1="${left}" y1="${py}" x2="${width-right}" y2="${py}"/><text class="axis-label" x="${left-9}" y="${py+3}" text-anchor="end">${money.format(value)}</text>`;
  }).join("");
  const labels = [0, Math.floor((rows.length - 1) / 2), rows.length - 1].filter((value, index, self) => self.indexOf(value) === index).map((index) => `<text class="axis-label" x="${x(index)}" y="${height-8}" text-anchor="middle">${shortDate.format(new Date(`${rows[index].day}T00:00:00`))}</text>`).join("");
  host.setAttribute("aria-label", `Daily cumulative net P&L ends at ${money.format(rows.at(-1).equity)}. Largest daily drawdown ${money.format(rows.reduce((v, row) => Math.min(v, row.drawdown), 0))}.`);
  const point = rows.length === 1 ? `<circle class="single-point" cx="${x(0)}" cy="${y(Number(rows[0].equity))}" r="4"/>` : "";
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${grid}<polygon class="drawdown-area" points="${drawdownArea}"/><polygon class="chart-area" points="${areaPoints}"/><polyline class="chart-line" points="${equityPoints}"/>${point}${labels}</svg>`;
}

function barChart(rows, hostId, labelForRow) {
  const host = $(hostId);
  if (!rows.length) return;
  const width = 920, height = 225, left = 80, right = 24, top = 20, bottom = 40;
  const values = rows.map((row) => Number(row.net_pnl));
  const min = Math.min(0, ...values), max = Math.max(0, ...values);
  const span = max - min || 1;
  const y = (value) => top + (max - value) * (height - top - bottom) / span;
  const slot = (width - left - right) / rows.length;
  const barWidth = Math.min(46, slot * 0.58);
  const labelEvery = Math.max(1, Math.ceil(rows.length / 12));
  const labelIndexes = new Set(rows.map((_, index) => index).filter(index => index % labelEvery === 0));
  const lastIndex = rows.length - 1;
  const precedingLabel = [...labelIndexes].filter(index => index < lastIndex).at(-1);
  if (precedingLabel != null && lastIndex - precedingLabel < labelEvery) labelIndexes.delete(precedingLabel);
  labelIndexes.add(lastIndex);
  const bars = rows.map((row, index) => {
    const value = Number(row.net_pnl), py = y(value), zero = y(0), x = left + slot * index + (slot - barWidth) / 2;
    const rectY = Math.min(py, zero), rectHeight = Math.max(2, Math.abs(zero - py));
    const label = labelForRow(row);
    const axisLabel = labelIndexes.has(index)
      ? `<text class="axis-label" x="${x+barWidth/2}" y="${height-13}" text-anchor="middle">${label}</text>`
      : "";
    return `<rect class="${value >= 0 ? "bar-positive" : "bar-negative"}" x="${x}" y="${rectY}" width="${barWidth}" height="${rectHeight}" rx="3"><title>${label}: ${money.format(value)}</title></rect>${axisLabel}`;
  }).join("");
  const grid = [min, (min + max) / 2, max].filter((v, i, a) => a.indexOf(v) === i).map(v => `<line class="chart-grid" x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}"/><text class="axis-label" x="${left-10}" y="${y(v)+4}" text-anchor="end">${money.format(v)}</text>`).join("");
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${grid}<line class="zero-line" x1="${left}" y1="${y(0)}" x2="${width-right}" y2="${y(0)}"/>${bars}</svg>`;
  host.setAttribute("aria-label", `${rows.length} periods. Net P&L ranges from ${money.format(min)} to ${money.format(max)}.`);
}

function yearlyFromDaily(rows) {
  const capital = 1200000;
  const grouped = new Map();
  for (const row of rows) {
    const year = String(row.day).slice(0, 4);
    const pnl = Number(row.net_pnl);
    if (!grouped.has(year)) grouped.set(year, { year, net_pnl: 0, best_day: -Infinity, losing_days: 0, traded_days: 0, equity: 0, peak: 0, max_drawdown: 0 });
    const result = grouped.get(year);
    result.net_pnl += pnl;
    result.best_day = Math.max(result.best_day, pnl);
    result.losing_days += pnl < 0 ? 1 : 0;
    result.traded_days += 1;
    result.equity += pnl;
    result.peak = Math.max(result.peak, result.equity);
    result.max_drawdown = Math.min(result.max_drawdown, result.equity - result.peak);
  }
  return [...grouped.values()]
    .map(row => ({...row, roi: row.net_pnl / capital * 100}))
    .sort((a, b) => a.year.localeCompare(b.year));
}

function render(data) {
  state.data = data;
  const { overview, statistics: stats, validation, concentration } = data;
  $("strategyTitle").textContent = overview.strategy;
  $("dateRange").textContent = `${shortDate.format(new Date(overview.start_time))} – ${shortDate.format(new Date(overview.end_time))}`;
  $("runMeta").textContent = `${overview.batch_count} batches · ${overview.leg_count} legs · ${overview.symbols} symbols · Run ${overview.run_id}`;
  if (data.dataset) $("runMeta").textContent += ` · Dataset: ${data.dataset.label}`;
  $("fileName").textContent = state.fileName;
  $("checksum").textContent = `SHA-256 ${validation.sha256.slice(0, 12)}…`;
  $("checksum").title = validation.sha256;
  $("feeNote").querySelector("span").textContent = data.assumptions.fees_note;
  setMoney("netPnl", overview.net_pnl);
  setMoney("grossPnl", overview.gross_pnl);
  setMoney("maxDrawdown", stats.max_drawdown);
  $("winRate").textContent = `${number.format(stats.win_rate)}%`;
  $("winLossCount").textContent = `${stats.wins} wins · ${stats.losses} losses`;
  $("sharpe").textContent = stats.sharpe_traded_days == null ? "—" : number.format(stats.sharpe_traded_days);
  $("profitFactor").textContent = stats.profit_factor == null ? "—" : number.format(stats.profit_factor);
  setMoney("equityTotal", overview.net_pnl);
  $("tradedDays").textContent = number.format(overview.traded_days);
  setMoney("averageDay", stats.average_day_pnl);
  setMoney("bestDay", stats.best_day);
  setMoney("worstDay", stats.worst_day);
  setMoney("averageBatch", stats.average_batch_pnl);
  setMoney("medianBatch", stats.median_batch_pnl);
  $("winStreak").textContent = `${stats.longest_win_streak} batches`;
  $("lossStreak").textContent = `${stats.longest_loss_streak} batches`;
  $("winDays").textContent = `${stats.win_days} · ${number.format(stats.day_win_rate)}%`;
  $("lossDays").textContent = `${stats.loss_days} · ${number.format(stats.day_loss_rate)}%`;
  $("breakevenDays").textContent = `${stats.breakeven_days} days`;
  setMoney("averageWinDay", stats.average_win_day);
  setMoney("averageLossDay", stats.average_loss_day);
  $("dayRiskReward").textContent = stats.day_risk_reward == null ? "—" : `1 : ${number.format(stats.day_risk_reward)}`;
  setMoney("medianDay", stats.median_day_pnl);
  $("underwaterDays").textContent = `${stats.longest_underwater_traded_days} traded days`;
  setMoney("expectancyDay", stats.average_day_pnl);
  $("bestDayShare").textContent = concentration.best_day_share_pct == null ? "—" : `${number.format(concentration.best_day_share_pct)}%`;
  setMoney("grossWinningDays", concentration.gross_winning_days);
  $("topThreeShare").textContent = concentration.top_three_days_share_pct == null ? "—" : `${number.format(concentration.top_three_days_share_pct)}%`;
  setMoney("netWithoutBest", concentration.net_without_best_day);
  setMoney("netWithoutBestWorst", concentration.net_without_best_and_worst);
  $("batchCount").textContent = `${overview.batch_count} total`;
  lineChart(data.daily);
  const yearly = yearlyFromDaily(data.daily);
  barChart(yearly, "yearlyChart", row => row.year);
  barChart(data.monthly, "monthlyChart", row => row.month.slice(5) + "/" + row.month.slice(2, 4));
  $("yearlyRows").innerHTML = yearly.map((row) => {
    const pnlClass = Number(row.net_pnl) > 0 ? "positive" : Number(row.net_pnl) < 0 ? "negative" : "";
    const bestClass = Number(row.best_day) >= 0 ? "positive" : "negative";
    const drawdownClass = Number(row.max_drawdown) < 0 ? "negative" : "";
    const roiClass = Number(row.roi) > 0 ? "positive" : Number(row.roi) < 0 ? "negative" : "";
    return `<tr><td>${escapeHtml(row.year)}</td><td class="numeric ${pnlClass}">${money.format(row.net_pnl)}</td><td class="numeric ${bestClass}">${money.format(row.best_day)}</td><td class="numeric ${drawdownClass}">${money.format(row.max_drawdown)}</td><td class="numeric ${roiClass}">${number.format(row.roi)}%</td><td class="numeric">${row.losing_days}</td><td class="numeric">${row.traded_days}</td></tr>`;
  }).join("");
  $("monthlyRows").innerHTML = data.monthly.map((row) => {
    const closeClass = Number(row.close) >= 0 ? "positive" : "negative";
    const bestClass = Number(row.best_day) >= 0 ? "positive" : "negative";
    const worstClass = Number(row.worst_day) >= 0 ? "positive" : "negative";
    const drawdownClass = Number(row.max_drawdown) < 0 ? "negative" : "";
    return `<tr><td>${escapeHtml(row.month)}</td><td class="numeric">${money.format(row.high)}</td><td class="numeric ${closeClass}">${money.format(row.close)}</td><td class="numeric ${bestClass}">${money.format(row.best_day)}</td><td class="numeric ${worstClass}">${money.format(row.worst_day)}</td><td class="numeric ${drawdownClass}">${money.format(row.max_drawdown)}</td><td class="numeric">${row.traded_days}</td></tr>`;
  }).join("");
  $("tradeRows").innerHTML = [...data.batches].reverse().map((row) => {
    const netClass = Number(row.net_pnl) >= 0 ? "positive" : "negative";
    return `<tr><td>${escapeHtml(row.batch_key)}</td><td>${shortDate.format(new Date(row.exit_time))}</td><td class="numeric">${row.legs}</td><td class="numeric">${money.format(row.gross_pnl)}</td><td class="numeric">${money.format(row.fees)}</td><td class="numeric ${netClass}">${money.format(row.net_pnl)}</td></tr>`;
  }).join("");
  $("runnerPanel").hidden = true;
  $("uploadView").hidden = true;
  $("dashboard").hidden = false;
  $("replaceButton").hidden = false;
  $("batchSearch").value = "";
  $("batchOutcome").value = "all";
  renderBatches();
  $("strategyTitle").focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
}

async function upload(file) {
  if (state.uploading || !file) return;
  if (!file || !file.name.toLowerCase().endsWith(".csv")) {
    $("uploadStatus").textContent = "Choose a .csv trade-log file.";
    return;
  }
  if (file.size > 25 * 1024 * 1024) {
    $("uploadStatus").textContent = "This file is larger than the 25 MB limit.";
    return;
  }
  if (file.size === 0) {
    $("uploadStatus").textContent = "This CSV is empty. Export completed trades and try again.";
    return;
  }
  state.uploading = true;
  $("uploadStatus").textContent = "Validating and calculating…";
  $("chooseButton").disabled = true;
  $("replaceButton").disabled = true;
  $("dropZone").setAttribute("aria-busy", "true");
  try {
    const response = await fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "text/csv" }, body: file });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The CSV could not be analyzed.");
    state.fileName = file.name;
    $("runDownloads").hidden = true;
    state.tradeFile = file;
    $("intradayReport").hidden = true;
    $("equityStatus").textContent = "Add equity.csv from this run to measure intraday and unrealised risk.";
    render(result);
    $("uploadStatus").textContent = "";
  } catch (error) {
    $("uploadStatus").textContent = error.message;
  } finally {
    $("chooseButton").disabled = false;
    $("replaceButton").disabled = false;
    $("dropZone").setAttribute("aria-busy", "false");
    state.uploading = false;
    fileInput.value = "";
  }
}

const fileInput = $("fileInput"), dropZone = $("dropZone");
$("chooseButton").addEventListener("click", () => fileInput.click());
$("replaceButton").addEventListener("click", () => { fileInput.value = ""; fileInput.click(); });
fileInput.addEventListener("change", () => upload(fileInput.files[0]));
["dragenter", "dragover"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.add("is-dragging"); }));
["dragleave", "drop"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.remove("is-dragging"); }));
dropZone.addEventListener("drop", (event) => upload(event.dataTransfer.files[0]));

function renderBatches() {
  if (!state.data) return;
  const query = $("batchSearch").value.trim().toLowerCase();
  const outcome = $("batchOutcome").value;
  const rows = [...state.data.batches].reverse().filter(row => String(row.batch_key).toLowerCase().includes(query) &&
    (outcome === "all" || (outcome === "win" && row.net_pnl > 0) || (outcome === "loss" && row.net_pnl < 0) || (outcome === "flat" && row.net_pnl === 0)));
  $("batchResults").textContent = `${rows.length} of ${state.data.batches.length} loaded batches`;
  $("tradeRows").innerHTML = rows.map(row => `<tr><td>${escapeHtml(row.batch_key)}</td><td>${shortDate.format(new Date(row.exit_time))}</td><td class="numeric">${row.legs}</td><td class="numeric">${money.format(row.gross_pnl)}</td><td class="numeric">${money.format(row.fees)}</td><td class="numeric ${row.net_pnl > 0 ? "positive" : row.net_pnl < 0 ? "negative" : ""}">${money.format(row.net_pnl)}</td></tr>`).join("") || '<tr><td colspan="6" class="empty-table">No batches match. Try another ID or choose all outcomes.</td></tr>';
}
$("batchSearch").addEventListener("input", renderBatches);
$("batchOutcome").addEventListener("change", renderBatches);
const sectionObserver = new IntersectionObserver(entries => {
  for (const entry of entries) if (entry.isIntersecting) {
    document.querySelectorAll(".report-nav a").forEach(link => {
      if (link.hash === `#${entry.target.id}`) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
  }
}, {rootMargin: "-15% 0px -60% 0px"});
["performance", "intraday", "consistency", "yearly", "monthly", "batches"].forEach(id => sectionObserver.observe($(id)));

$("equityButton").addEventListener("click", () => $("equityInput").click());
$("equityInput").addEventListener("change", async () => {
  const file = $("equityInput").files[0];
  if (!file || state.uploading || !state.tradeFile) return;
  const status = $("equityStatus");
  if (!file.name.toLowerCase().endsWith(".csv") || file.size === 0) {
    status.textContent = "Choose a nonempty equity.csv file.";
    $("equityInput").value = "";
    return;
  }
  state.uploading = true;
  $("equityButton").disabled = $("replaceButton").disabled = true;
  status.textContent = "Validating snapshots and matching the trade run…";
  try {
    if (file.size + state.tradeFile.size > 25 * 1024 * 1024) throw new Error("Combined files exceed the 25 MB limit.");
    const body = JSON.stringify({trades_csv: await state.tradeFile.text(), equity_csv: await file.text()});
    if (new Blob([body]).size > 25 * 1024 * 1024) throw new Error("Combined encoded upload exceeds the 25 MB limit.");
    const response = await fetch("/api/analyze", {method:"POST", headers:{"Content-Type":"application/json"}, body});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Equity CSV could not be analyzed.");
    renderIntraday(result.intraday, file.name);
  } catch (error) { status.textContent = error.message; }
  finally {
    state.uploading = false;
    $("equityButton").disabled = $("replaceButton").disabled = false;
    $("equityInput").value = "";
  }
});

function renderIntraday(risk, fileName) {
    state.data.intraday = risk;
    setMoney("intradayDrawdown", risk.max_drawdown);
    setMoney("worstUnrealized", risk.worst_unrealized_pnl);
    $("intradayCoverage").textContent = `${number.format(risk.snapshot_count)} snapshots · Largest gap ${number.format(risk.max_gap_seconds / 60)} minutes` + (risk.trough_time ? ` · Worst drawdown: ${risk.peak_time} to ${risk.trough_time}` : " · No observed drawdown");
    const rows = risk.series;
    const width = 920, height = 280, pad = 32;
    let low = 0, high = 0;
    for (const row of rows) { low = Math.min(low, row.equity); high = Math.max(high, row.equity); }
    const first = Date.parse(rows[0].timestamp), last = Date.parse(rows.at(-1).timestamp);
    const x = row => pad + (Date.parse(row.timestamp) - first) / (last - first) * (width - 2 * pad);
    const y = value => pad + (high - value) / (high - low || 1) * (height - 2 * pad);
    const points = rows.map(row => `${x(row)},${y(row.equity)}`).join(" ");
    $("intradayChart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true"><line class="zero-line" x1="${pad}" x2="${width-pad}" y1="${y(0)}" y2="${y(0)}"/><polyline class="chart-line" points="${points}"/><text class="axis-label" x="${pad}" y="18">${escapeHtml(money.format(high))}</text><text class="axis-label" x="${pad}" y="${height-12}">${escapeHtml(money.format(low))}</text></svg>`;
    $("intradayChart").setAttribute("aria-label", `Observed mark-to-market equity. Maximum drawdown ${money.format(risk.max_drawdown)}. Worst unrealised P&L ${money.format(risk.worst_unrealized_pnl)}.`);
    $("intradayReport").hidden = false;
    $("equityStatus").textContent = `${fileName}: run and final P&L matched. SHA-256 ${risk.sha256.slice(0,12)}…`;
}
