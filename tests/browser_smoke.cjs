/* Optional browser QA. Requires Playwright and an installed Chrome.
   Start `python -m witnessforge serve` first. This is not a runtime dependency. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const output = path.resolve('.test-output');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  const checks = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(process.env.WITNESSFORGE_URL || 'http://127.0.0.1:8765');
    await page.getByText('Local lab connected', { exact: true }).waitFor();
    assert.equal(await page.locator('#trace-count').innerText(), '20 actions');
    await page.screenshot({ path: path.join(output, 'workbench-desktop.png'), fullPage: true });
    checks.push('Initial workbench loads with the 20-action demo.');

    await page.locator('#run-button').click();
    await page.locator('#result-title').filter({ hasText: 'A smaller witness, with the same failure' }).waitFor();
    assert.equal(await page.locator('#metric-minimized').innerText(), '4');
    assert.equal(await page.locator('#metric-minimality').innerText(), '1-minimal');
    assert.match(await page.locator('#findings').innerText(), /Maya/);
    assert.match(await page.locator('#findings').innerText(), /Noor/);
    assert.equal(await page.locator('#export-actions a').count(), 3);
    await page.screenshot({ path: path.join(output, 'reduced-witness-desktop.png'), fullPage: false });
    checks.push('Browser run reproduces the intended sender error and reduces 20 actions to 4.');

    await page.getByRole('tab', { name: /Repair validation/ }).click();
    await page.locator('#validate-repair').click();
    await page.locator('#repair-title').filter({ hasText: 'The bundled repair passed the suite' }).waitFor();
    assert.equal(await page.locator('.validation-case').count(), 27);
    assert.match(await page.locator('#repair-description').innerText(), /27 of 27 checks passed/);
    checks.push('Repair validation renders all 27 passing cases.');

    await page.getByRole('tab', { name: /Case archive/ }).click();
    await page.locator('.case-open').first().waitFor();
    await page.locator('.case-open').first().click();
    await page.locator('#view-experiment').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#metric-minimized').innerText(), '4');
    checks.push('Saved case reopens from the archive.');

    await page.locator('#parser').selectOption('fixed');
    await page.locator('#run-button').click();
    await page.locator('#result-title').filter({ hasText: 'No target failure to reduce' }).waitFor();
    assert.equal(await page.locator('#metric-minimality').innerText(), 'Not needed');
    checks.push('Passing parser run is not presented as a minimized failure.');

    await page.locator('#parser').selectOption('buggy');
    await page.locator('#max-evaluations').fill('2');
    await page.locator('#run-button').click();
    await page.locator('#result-title').filter({ hasText: 'Failure preserved; reduction incomplete' }).waitFor();
    assert.equal(await page.locator('#result-badge').innerText(), 'BUDGET REACHED');
    checks.push('An exhausted budget visibly prevents a minimality claim.');

    await page.locator('#trace-input').fill('{invalid');
    await page.locator('#run-button').click();
    await page.locator('#trace-error').waitFor({ state: 'visible' });
    assert.match(await page.locator('#trace-error').innerText(), /Invalid JSON/);
    checks.push('Malformed JSON displays a useful error.');

    await page.locator('#reset-demo').click();
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, 'Mobile viewport has horizontal overflow');
    await page.screenshot({ path: path.join(output, 'workbench-mobile.png'), fullPage: true });
    checks.push('The workbench fits a 390-pixel mobile viewport.');
    assert.deepEqual(errors, []);
    checks.push('No JavaScript page errors during the tested workflows.');
    fs.writeFileSync(path.join(output, 'browser-results.json'), JSON.stringify({ status: 'PASS', checks, errors }, null, 2));
    console.log(JSON.stringify({ status: 'PASS', checks: checks.length, errors }));
  } catch (error) {
    fs.writeFileSync(path.join(output, 'browser-results.json'), JSON.stringify({ status: 'FAIL', checks, errors, failure: String(error) }, null, 2));
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
