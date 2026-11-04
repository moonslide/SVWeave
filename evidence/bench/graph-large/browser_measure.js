const { chromium } = require('playwright');
(async () => {
  const [single, atlas, lazy] = process.argv.slice(2);
  const b = await chromium.launch();
  async function open(label, url, extra) {
    const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
    const errs = [];
    p.on('pageerror', e => errs.push(String(e).slice(0, 120)));
    const t0 = Date.now();
    try {
      await p.goto(url, { timeout: 180000 });
      await p.waitForSelector('.node', { timeout: 180000 });
      const ms = Date.now() - t0;
      const mem = await p.evaluate(() => (performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1e6) : null));
      console.log(label, '| open+draw ms:', ms, '| JS heap MB:', mem, '|', await p.textContent('#count'));
      if (extra) await extra(p);
    } catch (e) {
      console.log(label, '| FAILED after', Date.now() - t0, 'ms:', String(e).slice(0, 160));
    }
    console.log(label, '| errors:', JSON.stringify(errs));
    await p.close();
  }
  await open('single complete page', 'file://' + single);
  await open('split index', 'file://' + atlas + '/index.html', async p => {
    const t = Date.now();
    await p.fill('#q', 'g_cl[123].u_cl.g_core[77].u_core.u_wb');
    await p.waitForTimeout(100);
    const links = await p.$$('#side a');
    console.log('  search across 115,562 instances ms:', Date.now() - t, '| results:', links.length);
    if (links.length) {
      const t2 = Date.now();
      await Promise.all([p.waitForNavigation(), links[0].click()]);
      await p.waitForSelector('.node');
      console.log('  open the result page ms:', Date.now() - t2, '|', (await p.textContent('#side')).slice(0, 80));
    }
  });
  await open('lazy index', 'file://' + lazy + '/index.html', async p => {
    await p.fill('#q', 'g_cl[123].u_cl.g_core[77].u_core.u_wb');
    await p.waitForTimeout(100);
    const links = await p.$$('#side a');
    const t = Date.now();
    if (links.length) {
      await links[0].click();
      await p.waitForFunction(() => document.getElementById('side').textContent.indexOf('u_wb') === 0 || document.getElementById('side').textContent.indexOf('u_wbpath') >= 0, null, { timeout: 60000 });
      console.log('  load the result part + select ms:', Date.now() - t, '|', await p.textContent('#count'));
    }
  });
  await b.close();
})();
