const state = { fileName: "trades.csv", data: null };

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
  const width = 920, height = 300, left = 68, right = 18, top = 16, bottom = 34;
  const values = rows.flatMap((row) => [Number(row.equity), Number(row.drawdown)]);
  let min = Math.min(0, ...values), max = Math.max(0, ...values);
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
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">${grid}<polygon class="drawdown-area" points="${drawdownArea}"/><polygon class="chart-area" points="${areaPoints}"/><polyline class="chart-line" points="${equityPoints}"/>${labels}</svg>`;
}

function barChart(rows) {
  const host = $("monthlyChart");
  if (!rows.length) return;
  const width = 1100, height = 225, left = 58, right = 16, top = 14, bottom = 40;
  const values = rows.map((row) => Number(row.net_pnl));
  const min = Math.min(0, ...values), max = Math.max(0, ...values);
  const span = max - min || 1;
  const y = (value) => top + (max - value) * (height - top - bottom) / span;
  const slot = (width - left - right) / rows.length;
  const barWidth = Math.min(46, slot * 0.58);
  const labelEvery = Math.max(1, Math.ceil(rows.length / 12));
  const bars = rows.map((row, index) => {
    const value = Number(row.net_pnl), py = y(value), zero = y(0), x = left + slot * index + (slot - barWidth) / 2;
    const rectY = Math.min(py, zero), rectHeight = Math.max(2, Math.abs(zero - py));
    const label = row.month.slice(5) + "/" + row.month.slice(2, 4);
    const axisLabel = (index % labelEvery === 0 || index === rows.length - 1)
      ? `<text class="axis-label" x="${x+barWidth/2}" y="${height-13}" text-anchor="middle">${label}</text>`
      : "";
    return `<rect class="${value >= 0 ? "bar-positive" : "bar-negative"}" x="${x}" y="${rectY}" width="${barWidth}" height="${rectHeight}" rx="3"><title>${row.month}: ${money.format(value)}</title></rect>${axisLabel}`;
  }).join("");
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><line class="zero-line" x1="${left}" y1="${y(0)}" x2="${width-right}" y2="${y(0)}"/>${bars}</svg>`;
}

function render(data) {
  state.data = data;
  const { overview, statistics: stats, validation, concentration } = data;
  $("strategyTitle").textContent = overview.strategy;
  $("dateRange").textContent = `${shortDate.format(new Date(overview.start_time))} – ${shortDate.format(new Date(overview.end_time))}`;
  $("runMeta").textContent = `${overview.batch_count} batches · ${overview.leg_count} legs · ${overview.symbols} symbols · Run ${overview.run_id}`;
  $("fileName").textContent = state.fileName;
  $("checksum").textContent = `SHA-256 ${validation.sha256.slice(0, 12)}…`;
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
  $("lossDays").textContent = `${stats.loss_days} · ${number.format(100 - stats.day_win_rate)}%`;
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
  barChart(data.monthly);
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
  $("uploadView").hidden = true;
  $("dashboard").hidden = false;
  $("replaceButton").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function upload(file) {
  if (!file || !file.name.toLowerCase().endsWith(".csv")) {
    $("uploadStatus").textContent = "Choose a .csv trade-log file.";
    return;
  }
  if (file.size > 25 * 1024 * 1024) {
    $("uploadStatus").textContent = "This file is larger than the 25 MB limit.";
    return;
  }
  state.fileName = file.name;
  $("uploadStatus").textContent = "Validating and calculating…";
  $("chooseButton").disabled = true;
  try {
    const response = await fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "text/csv", "X-Filename": file.name }, body: file });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The CSV could not be analyzed.");
    render(result);
  } catch (error) {
    $("uploadStatus").textContent = error.message;
  } finally {
    $("chooseButton").disabled = false;
  }
}

const fileInput = $("fileInput"), dropZone = $("dropZone");
$("chooseButton").addEventListener("click", () => fileInput.click());
$("replaceButton").addEventListener("click", () => { fileInput.value = ""; fileInput.click(); });
fileInput.addEventListener("change", () => upload(fileInput.files[0]));
["dragenter", "dragover"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.add("is-dragging"); }));
["dragleave", "drop"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.remove("is-dragging"); }));
dropZone.addEventListener("drop", (event) => upload(event.dataTransfer.files[0]));
