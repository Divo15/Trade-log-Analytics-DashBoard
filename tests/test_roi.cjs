const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/trade_log_dashboard/static/app.js'), 'utf8');
const start = source.indexOf('function yearlyFromDaily(');
const end = source.indexOf('function renderYearlyPerformance(', start);
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context);

const rows = [
  {day: '2025-01-01', net_pnl: 40000},
  {day: '2025-01-02', net_pnl: -10000},
  {day: '2026-01-01', net_pnl: -15000},
];

const result = context.yearlyFromDaily(rows, 300000);
assert.equal(result[0].roi, 10);
assert.equal(result[1].roi, -5);
assert.equal(context.yearlyFromDaily(rows, 600000)[0].roi, 5);
for (const capital of [undefined, 0, -1, NaN, Infinity]) {
  assert.equal(context.yearlyFromDaily(rows, capital)[0].roi, null);
}
assert.equal(context.yearlyFromDaily([{day: '2026-01-01', net_pnl: 0}], 300000)[0].roi, 0);
console.log('ROI checks passed');
