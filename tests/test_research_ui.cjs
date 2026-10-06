// Browser checks use explicit fixtures: no model call or real market claim.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const id = 'a'.repeat(32);
    const dataset = {id: 'fixture', label: 'UI test dataset', available: true, symbol: 'NIFTY', folder: 'fixture', start_date: '2025-01-01', end_date: '2026-01-01', trading_days: 250};
    let created = false;
    let connectionReady = true;
    const run = {id, objective: 'UI test: complete option-selling research journey', status: 'candidate_ready', updated_at: '2026-10-06T10:00:00Z', dataset_label: dataset.label,
      calls_used: 5, max_calls: 12, current_role: null, error: null, messages: [{at: '2026-10-06T10:00:00Z', sender: 'lead', recipient: 'team', text: 'UI fixture — inspect evidence before a handoff. <script>invalid()</script>', limitations: ['Fixture only; not market research.']}],
      tasks: [{sequence: 3, role: 'strategy', status: 'complete', artifacts: [], reply: {journey: 'Entry → exit → remain flat'}}], candidate: {task: 3, file: 'candidate.py'}};
    await page.route('**/api/datasets', route => route.fulfill({json: {datasets: [dataset]}}));
    await page.route('**/api/research/**', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname.endsWith('/connection')) return route.fulfill({json: {ready: connectionReady, message: connectionReady ? 'Test fixture: ChatGPT sign-in detected.' : "Sign in using 'codex login' with ChatGPT."}});
      if (url.pathname === '/api/research/runs' && request.method() === 'POST') {
        const payload = request.postDataJSON();
        assert.equal(payload.dataset_id, 'fixture');
        assert.equal(payload.max_calls, 12);
        assert.ok(payload.config.execution);
        created = true; return route.fulfill({json: {id}});
      }
      if (url.pathname === '/api/research/runs') return route.fulfill({json: {runs: created ? [run] : []}});
      if (url.pathname.endsWith('/handoff')) return route.fulfill({json: {filename: 'research_candidate.py', source: '# UI fixture', dataset_id: 'fixture', config: {parameters: {}, execution: {fees_per_leg: 0}}, note: 'Review the candidate before execution.'}});
      return route.fulfill({json: run});
    });
    await page.goto(process.env.DASHBOARD_URL || 'http://127.0.0.1:8797/');
    await page.locator('#researchButton').click();
    await page.waitForFunction(() => !document.getElementById('researchStart').disabled);
    await page.locator('#researchDataset').selectOption('fixture');
    const output = path.resolve('.qa-screenshots'); fs.mkdirSync(output, {recursive: true});
    await page.screenshot({path: path.join(output, 'research-desktop.png'), fullPage: true});
    for (const width of [768, 390]) {
      await page.setViewportSize({width, height: 900});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Horizontal overflow at ${width}`);
      await page.screenshot({path: path.join(output, `research-${width}.png`), fullPage: true});
    }
    await page.setViewportSize({width: 1440, height: 1050});
    await page.locator('#researchStart').click();
    await page.locator('#researchHandoff').waitFor({state: 'visible'});
    assert.ok((await page.locator('#researchJournal').innerText()).includes('<script>invalid()</script>'));
    await page.screenshot({path: path.join(output, 'research-candidate.png'), fullPage: true});
    await page.locator('#researchHandoff').click();
    await page.locator('#runnerPanel').waitFor({state: 'visible'});
    assert.ok(await page.locator('#runnerPanel').isVisible());
    assert.ok(await page.locator('#researchPanel').isHidden());
    assert.equal(await page.locator('#entrypoint').inputValue(), 'research_candidate.py');
    assert.equal(await page.locator('#datasetSelect').inputValue(), 'fixture');
    assert.equal(await page.locator('#runStatus').innerText(), 'Review the candidate before execution.');
    await page.locator('#researchButton').click();
    await page.locator('#researchNew').click();
    connectionReady = false;
    await page.locator('#researchReconnect').click();
    await page.waitForFunction(() => document.getElementById('researchConnection').textContent.includes('codex login'));
    assert.ok(await page.locator('#researchStart').isDisabled());
    assert.deepEqual(errors, []);
    console.log('Browser checks passed: desktop/tablet/mobile, start, escaped text, handoff, navigation and disconnected state.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
