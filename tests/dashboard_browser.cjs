const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.HOTPATH_PLAYWRIGHT_MODULE || 'playwright');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.HOTPATH_BROWSER_EXECUTABLE ? {executablePath: process.env.HOTPATH_BROWSER_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let runsCalls = 0, treeCalls = 0, delayOld = false;
    const originalId = fixture.data.run.id;
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'hotpath.test') return route.abort();
      const json = obj => route.fulfill({contentType: 'application/json', body: JSON.stringify(obj)});
      if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: fixture.html});
      if (url.pathname === '/api/runs') {
        if (++runsCalls === 1) return route.fulfill({status: 503, body: 'temporary'});
        return json([fixture.data.run, {...fixture.data.run, id: 'run_second', config_name: 'second'}]);
      }
      if (url.pathname === '/api/state') {
        const second = url.searchParams.get('run_id') === 'run_second';
        if (delayOld && !second) await new Promise(resolve => setTimeout(resolve, 400));
        return json(second ? {...fixture.data, run: {...fixture.data.run, id: 'run_second', config_name: 'second'}} : fixture.data);
      }
      if (url.pathname.endsWith('/tree')) { treeCalls++; return json(fixture.tree); }
      if (url.pathname.endsWith('/chart')) return json(fixture.chart);
      if (url.pathname.endsWith('/funnel')) return json(fixture.funnel);
      if (url.pathname.endsWith('/profile_diff')) return json(fixture.diff);
      return route.fulfill({status: 404});
    });
    await page.goto('http://hotpath.test/');
    await page.locator('#connectionError').waitFor({state: 'visible'});
    await page.locator('#expTable tbody tr').waitFor();
    assert.equal(await page.locator('#connectionError').isVisible(), false);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.match(await page.locator('#profileTitle').innerText(), /Call-stack flame graph\s+torch\.profiler \(cpu-time fallback\)\s+· abc/);
    assert.equal(await page.locator('.flamegraph svg[aria-label="Observed call-stack flame graph"]').count(), 1);
    assert.match(await page.locator('#profile').innerText(), /torch\.profiler CPU event tree/);
    assert.equal(await page.locator('#profile unsafe').count(), 0);
    const flameWidths = await page.locator('.flamegraph rect.frame.outer').evaluateAll(rects => rects.map(r => Number(r.getAttribute('width'))));
    assert.ok(Math.abs(flameWidths[1] / flameWidths[0] - .8) < .01, 'a child occupies its share of parent total time');
    await page.locator('#tree .node.rejected').click();
    assert.match(await page.locator('#detail').innerText(), /Exact reason: output 42 differs from expected 41 <unsafe>/);
    assert.match(await page.locator('#detail').innerText(), /Verification stage:\s*correctness failed; benchmark did not run/);
    assert.doesNotMatch(await page.locator('#detail').innerText(), /BENCHMARK \(/);
    assert.equal(await page.locator('#detail unsafe').count(), 0);
    assert.equal(await page.locator('#chartMode button[data-mode=raw]').getAttribute('aria-pressed'), 'true');
    const before = treeCalls;
    fixture.data.experiments[0].updated_at = '2030-01-01T00:00:00Z';
    fixture.data.experiments[0].reject_reason = 'Updated experiment, unchanged run';
    await page.waitForFunction(() => document.querySelector('#detail').textContent.includes('Updated experiment'));
    assert.ok(treeCalls > before, 'experiment updates must invalidate derived views');
    delayOld = true;
    await page.evaluate(id => { selectRun(id); selectRun('run_second'); }, originalId);
    await page.waitForFunction(() => state.data?.run.id === 'run_second');
    await page.waitForTimeout(700);
    assert.equal(await page.evaluate(() => state.data.run.id), 'run_second');
    await page.evaluate(() => {
      state.data = {run: null, experiments: [], config: {target: 'offline-demo'}, live: false};
      state.selected = null; state.tree = state.chart = state.funnel = state.diff = null;
      render();
    });
    assert.match(await page.locator('#detail').innerText(), /Start a run or select a stored run/);
    assert.match(await page.locator('#bottleneck').innerText(), /No run selected/);
    assert.match(await page.locator('#profile').innerText(), /No run selected/);
    assert.deepEqual(errors, []);
    console.log('browser checks passed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
