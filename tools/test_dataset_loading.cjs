const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

(async () => {
  const source = fs.readFileSync('src/trade_log_dashboard/static/runner.js', 'utf8');
  const prefix = source.slice(0, source.indexOf('function showRunner()'));
  const select = {replaceChildren(...options) { this.options = options; }, addEventListener() {}};
  const coverage = {querySelector() { return {}; }};
  let fail = false;
  let retry;
  const context = vm.createContext({
    $: id => ({datasetSelect: select, datasetCoverage: coverage}[id] || null),
    Intl, Date, console: {error() {}},
    Option: function(text, value) { this.text = text; this.value = value; },
    clearTimeout() {}, setTimeout(fn) { retry = fn; },
    fetch: async () => {
      if (fail) throw new Error('Network unavailable');
      return {ok: true, json: async () => ({datasets: [{id: 'weekly', label: 'Weekly', available: true}]})};
    }
  });
  vm.runInContext(prefix, context);
  await new Promise(setImmediate);
  assert.equal(select.options[1].value, 'weekly', 'Missing optional hint must not remove datasets');
  fail = true;
  await vm.runInContext('loadDatasets()', context);
  assert.match(select.options[0].text, /Network unavailable/);
  assert.equal(typeof retry, 'function');
  fail = false;
  await retry();
  assert.equal(select.options[1].value, 'weekly');
  console.log('PASS: missing hint, network failure, and retry recovery');
})();
