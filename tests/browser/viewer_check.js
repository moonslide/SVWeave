// Drives the exported graph pages in Chromium (Playwright) and prints one JSON object of what it
// saw. Used by tests/browser/test_viewer.py; input: the exports directory and two instance paths.
// Every page interaction a user relies on: the banner, expand, search, portals, the atlas links
// and crumbs, lazy loading, the #f= focus, the commit filter, and that no foreign script runs.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const [dir, deepPath, deepPath2] = process.argv.slice(2);
  const out = {};
  const b = await chromium.launch();
  async function page(url) {
    const p = await b.newPage({ viewport: { width: 1400, height: 900 } });
    p.errors = [];
    p.on('console', m => { if (m.type() === 'error') p.errors.push(m.text().slice(0, 200)); });
    p.on('pageerror', e => p.errors.push(String(e).slice(0, 200)));
    await p.goto(url);
    await p.waitForSelector('.node', { timeout: 30000 });
    return p;
  }
  const count = async p => (await p.$$('.node')).length;
  const side = async p => (await p.textContent('#side')) || '';
  try {
    // ---- single complete page
    let p = await page('file://' + path.join(dir, 'whole.html'));
    const r = { banner: await p.textContent('#banner'), before: await count(p) };
    await p.click('#expand');
    r.expanded = await count(p);
    await p.fill('#q', deepPath);
    await p.waitForTimeout(200);
    r.hits = (await p.$$('.node.hit')).length;
    r.errors = p.errors;
    out.whole = r;
    await p.close();

    // ---- --split atlas: index, a portal link, crumbs back, search across pages + #f= focus
    p = await page('file://' + path.join(dir, 'atlas', 'index.html'));
    const a = { banner: await p.textContent('#banner') };
    const portal = await p.$('.node.portal');
    a.portal = !!portal;
    if (portal) {
      await portal.click();
      const link = await p.$('#side a.portal-link');
      a.portal_link = !!link;
      if (link) {
        await Promise.all([p.waitForNavigation(), link.click()]);
        await p.waitForSelector('.node');
        a.sub_url = path.basename(p.url());
        a.sub_banner = await p.textContent('#banner');
        const back = await p.$('#crumbs a');
        a.crumb_back = back ? await back.getAttribute('href') : null;
        if (back) {
          await Promise.all([p.waitForNavigation(), back.click()]);
          await p.waitForSelector('.node');
          a.back_url = path.basename(p.url());
        }
      }
    }
    await p.fill('#q', deepPath2);
    await p.waitForTimeout(200);
    const res = await p.$$('#side a');
    a.search_links = res.length;
    if (res.length) {
      await Promise.all([p.waitForNavigation(), res[0].click()]);
      await p.waitForSelector('.node.sel', { timeout: 30000 });
      a.found_url = path.basename(p.url()).split('#')[0];
      a.found_side = (await side(p)).slice(0, 300);
    }
    a.errors = p.errors;
    out.atlas = a;
    await p.close();

    // ---- --lazy: double-click a portal loads its part; a search result in an unloaded part
    p = await page('file://' + path.join(dir, 'lazy', 'index.html'));
    const l = { banner: await p.textContent('#banner'), before: await p.textContent('#count') };
    const lp = await p.$('.node.portal');
    if (lp) {
      await lp.dblclick();
      await p.waitForFunction(b => document.getElementById('count').textContent !== b, l.before,
                              { timeout: 30000 });
    }
    l.after = await p.textContent('#count');
    await p.fill('#q', deepPath2);
    await p.waitForTimeout(200);
    const lr = await p.$$('#side a');
    l.search_links = lr.length;
    if (lr.length) {
      await lr[0].click();
      await p.waitForFunction(w => (document.getElementById('side').textContent || '').indexOf(w) >= 0,
                              deepPath2, { timeout: 30000 });
      l.found_side = (await side(p)).slice(0, 300);
      l.selected = (await p.$$('.node.sel')).length;
    }
    l.errors = p.errors;
    out.lazy = l;
    await p.close();

    // ---- per-commit diff page: the commit filter highlights and dims
    p = await page('file://' + path.join(dir, 'diff.html'));
    const d = { banner: await p.textContent('#banner'), rows: (await p.$$('.crow')).length };
    const row = await p.$('.crow');
    if (row) {
      await row.click();
      await p.waitForTimeout(200);
      d.hit = (await p.$$('.node.hit')).length;
      d.dim = (await p.$$('.node.dim')).length;
      await (await p.$('.crow')).click();  // click again: cleared
      await p.waitForTimeout(200);
      d.hit_after_clear = (await p.$$('.node.hit')).length;
    }
    d.errors = p.errors;
    out.diff = d;
    await p.close();

    // ---- CSP: an injected inline script never runs, the viewer still does
    const html = fs.readFileSync(path.join(dir, 'whole.html'), 'utf8')
      .replace('</body>', '<script>document.title="INJECTED"</script></body>');
    fs.writeFileSync(path.join(dir, 'injected.html'), html);
    p = await page('file://' + path.join(dir, 'injected.html'));
    out.csp = { injected_ran: (await p.title()) === 'INJECTED', drawn: await count(p) };
    await p.close();
  } catch (e) {
    out.exception = String(e).slice(0, 500);
  }
  await b.close();
  process.stdout.write(JSON.stringify(out));
})();
