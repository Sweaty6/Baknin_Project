import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {buildWebsite, normalizeBase, ROOT} from '../../scripts/build_website.mjs';

test('Node alone builds every public page, local analysis and project-path resources', async t => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'baknin-website-test-'));
  t.after(() => fs.rm(temporary, {recursive: true, force: true}));
  const output = path.join(temporary, 'site');
  const inputCatalog = JSON.parse(await fs.readFile(path.join(ROOT, 'website/data/catalog.json'), 'utf8'));
  const inputReviews = JSON.parse(await fs.readFile(path.join(ROOT, 'website/data/reviews.json'), 'utf8'));
  const result = await buildWebsite({output, exportedAt: '2026-10-14T00:00:00Z'});
  assert.equal(result.pages, inputCatalog.universities.length + 6);
  assert.equal(result.reviews, inputReviews.length);
  const html = await fs.readFile(path.join(output, 'index.html'), 'utf8');
  assert.match(html, /src="\/Baknin_Project\/static\/pages-api.js"/);
  assert.doesNotMatch(html, /\{%|\{\{/);
  const guide = await fs.readFile(path.join(output, 'launch/index.html'), 'utf8');
  assert.match(guide, /GitHub Actions/);
  assert.doesNotMatch(guide, /py -3\.11|venv|app\.py/);
  const analysis = await fs.readFile(path.join(output, 'analysis/index.html'), 'utf8');
  assert.match(analysis, /Анализ выполняется в вашем браузере/);
  const universityId = inputCatalog.universities[0].id;
  const detail = await fs.readFile(path.join(output, 'universities', universityId, 'index.html'), 'utf8');
  assert.ok(detail.includes(`data-university-id="${universityId}"`));
  assert.match(detail, /href="\/Baknin_Project\/" aria-current="page"/);
  const snapshot = JSON.parse(await fs.readFile(path.join(output, 'data/site.json'), 'utf8'));
  assert.equal(snapshot.reviews.length, inputReviews.length);
  assert.equal(Object.keys(snapshot.details).length, inputCatalog.universities.length);
  if (!inputReviews.length) assert.equal(snapshot.details[universityId].university.rank, null);
  assert.equal(snapshot.methodology.engine, 'browser_rules');
  assert.equal(snapshot.health.ml_trained, false);
  assert.deepEqual((await fs.readdir(output)).sort(), ['.baknin-pages-build', '.nojekyll', '404.html', 'analysis', 'data', 'index.html', 'launch', 'methodology', 'reviews', 'static', 'universities'].sort());
  assert.equal((await buildWebsite({output})).pages, inputCatalog.universities.length + 6);
});

test('Custom-domain root uses correct URLs', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'baknin-domain-test-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  await buildWebsite({output: path.join(directory, 'site'), basePath: '/'});
  const html = await fs.readFile(path.join(directory, 'site/index.html'), 'utf8');
  assert.match(html, /src="\/static\/pages-api.js"/);
  assert.doesNotMatch(html, /src="\/\/static/);
});

test('Invalid input cannot replace a working generated site', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'baknin-invalid-build-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const output = path.join(directory, 'site');
  await buildWebsite({output});
  const previous = await fs.readFile(path.join(output, 'index.html'));
  const reviews = path.join(directory, 'invalid.json');
  await fs.writeFile(reviews, JSON.stringify([{text: 'Technical fixture with no required provenance'}]));
  await assert.rejects(buildWebsite({output, reviews}));
  assert.deepEqual(await fs.readFile(path.join(output, 'index.html')), previous);
});

test('Unknown output files and source directories are preserved', async t => {
  const output = await fs.mkdtemp(path.join(os.tmpdir(), 'baknin-user-files-'));
  t.after(() => fs.rm(output, {recursive: true, force: true}));
  await fs.writeFile(path.join(output, 'keep.txt'), 'User file');
  await assert.rejects(buildWebsite({output}), /пользовательские файлы/);
  assert.equal(await fs.readFile(path.join(output, 'keep.txt'), 'utf8'), 'User file');
  for (const name of ['static', 'templates', 'data', 'docs', 'website', 'scripts', 'tests', 'baknin', '.git']) {
    await assert.rejects(buildWebsite({output: path.join(ROOT, name, 'generated-website')}), /исходников/);
  }
  await assert.rejects(buildWebsite({output: path.dirname(ROOT)}), /родительского/);
});

test('Inputs inside the generated directory cannot be deleted during replacement', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'baknin-input-preserve-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const output = path.join(directory, 'site');
  await buildWebsite({output});
  const input = path.join(output, 'reviews-source.json');
  await fs.writeFile(input, '[]');
  await assert.rejects(buildWebsite({output, reviews: input}), /Входные данные/);
  assert.equal(await fs.readFile(input, 'utf8'), '[]');
});

test('Base paths are validated', () => {
  assert.equal(normalizeBase('/Baknin_Project'), '/Baknin_Project/');
  assert.equal(normalizeBase('/'), '/');
  for (const value of ['https://example.org', '/../site/', '/site/./', '/?script=']) assert.throws(() => normalizeBase(value));
});

test('Input symlinks cannot hide files located inside the output directory', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'baknin-input-link-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const output = path.join(directory, 'site');
  await buildWebsite({output});
  const original = path.join(output, 'input-catalog.json');
  await fs.copyFile(path.join(ROOT, 'website/data/catalog.json'), original);
  const contents = await fs.readFile(original);
  const alias = path.join(directory, 'catalog-alias.json');
  await fs.symlink(original, alias);
  await assert.rejects(buildWebsite({output, catalog: alias}), /Входные данные/);
  assert.deepEqual(await fs.readFile(original), contents);
});

test('Failed replacement and rollback retain the previous website backup', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'baknin-rollback-preserve-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const output = path.join(directory, 'site');
  await buildWebsite({output});
  const previous = await fs.readFile(path.join(output, 'index.html'));
  const rename = fs.rename;
  fs.rename = async (from, to) => {
    if (path.basename(from).startsWith('.baknin-website-stage-') || path.basename(from) === 'previous') {
      const error = new Error('Injected file-system failure'); error.code = 'EACCES'; throw error;
    }
    return rename(from, to);
  };
  try { await assert.rejects(buildWebsite({output}), /предыдущая сборка сохранена/); }
  finally { fs.rename = rename; }
  const backup = (await fs.readdir(directory)).find(name => name.startsWith('.baknin-website-backup-'));
  assert.ok(backup);
  assert.deepEqual(await fs.readFile(path.join(directory, backup, 'previous/index.html')), previous);
});
