/** Build the complete public website using Node.js; no Python/server is needed. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import nunjucks from 'nunjucks';
import {prepareSnapshot} from './lib/public-data.mjs';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const MARKER = 'baknin-pages-v1\n';
const assets = ['app.js', 'style.css', 'pages-api.js', 'pages-nlp.js'];

export function normalizeBase(value = '/Baknin_Project/') {
  if (typeof value !== 'string' || !/^\/[A-Za-z0-9_./-]*$/.test(value)) throw new Error('base-path должен иметь вид /Baknin_Project/');
  const parts = value.split('/').filter(Boolean);
  if (parts.some(part => part === '.' || part === '..')) throw new Error('base-path не должен содержать . или ..');
  return '/' + parts.join('/') + (parts.length ? '/' : '');
}

function contains(parent, child) {
  const relative = path.relative(parent, child);
  return !relative || (relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative));
}

async function outputDirectory(value) {
  const output = path.resolve(value);
  const protectedPaths = ['templates', 'static', 'data', 'docs', 'website', 'baknin', 'scripts', 'tests', '.git', '.github', 'node_modules'];
  if (contains(output, ROOT) || protectedPaths.some(name => contains(path.join(ROOT, name), output))) {
    throw new Error('Нельзя записывать сборку поверх исходников или родительского каталога проекта');
  }
  const parent = path.dirname(output);
  await fs.mkdir(parent, {recursive: true});
  const realParent = await fs.realpath(parent);
  if (realParent !== parent) throw new Error('Папка сборки не должна находиться за символической ссылкой');
  try {
    const stat = await fs.lstat(output);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('output должен быть обычной папкой');
    if ((await fs.readdir(output)).length) {
      const marker = await fs.readFile(path.join(output, '.baknin-pages-build'), 'utf8').catch(() => '');
      if (marker !== MARKER) throw new Error('Папка результата содержит пользовательские файлы; выберите пустую папку');
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  return output;
}

function prefixLinks(html, base) {
  return html.replace(/(\b(?:href|src|action)\s*=\s*["'])(\/[^"']*)(["'])/gi,
    (_, before, href, after) => before + (href.startsWith('//') ? href : base + href.slice(1)) + after);
}

async function completeSnapshot(snapshot) {
  const context = vm.createContext({URL, console,
    document: {querySelector: () => ({content: snapshot.base_path})},
    fetch: async () => ({ok: true, json: async () => snapshot})});
  vm.runInContext(await fs.readFile(path.join(ROOT, 'static/pages-api.js'), 'utf8'), context,
    {filename: 'pages-api.js', timeout: 1000});
  const adapter = context.BakninPagesAPI;
  snapshot.universities = await adapter.request('/api/universities');
  for (const university of snapshot.universities.items) {
    snapshot.details[university.id] = await adapter.request('/api/universities/' + university.id);
  }
  snapshot.methodology = await adapter.request('/api/methodology');
  return snapshot;
}

export async function buildWebsite({output = '_site', basePath = '/Baknin_Project/',
  catalog = path.join(ROOT, 'website/data/catalog.json'), reviews = path.join(ROOT, 'website/data/reviews.json'),
  rules = path.join(ROOT, 'website/data/rules.json'), exportedAt} = {}) {
  const base = normalizeBase(basePath);
  const target = await outputDirectory(output);
  const inputPaths = await Promise.all([catalog, reviews, rules].map(file => fs.realpath(path.resolve(file))));
  if (inputPaths.some(file => contains(target, file))) throw new Error('Входные данные не должны находиться внутри папки результата');
  const input = await Promise.all(inputPaths.map(async file => JSON.parse(await fs.readFile(file, 'utf8'))));
  const snapshot = await completeSnapshot(prepareSnapshot(...input, {basePath: base, exportedAt}));
  const engine = new nunjucks.Environment(new nunjucks.FileSystemLoader(path.join(ROOT, 'templates'), {noCache: true}),
    {autoescape: true, throwOnUndefined: true});
  const settings = {PAGE_MODE: 'pages', PAGE_BASE_PATH: base};
  const config = {get: (key, fallback = undefined) => settings[key] ?? fallback};
  function urlFor(endpoint, options = {}) {
    if (endpoint === 'static' && assets.includes(options.filename)) return '/static/' + options.filename;
    if (endpoint === 'launch_page') return '/launch';
    throw new Error('Неподдерживаемая ссылка в шаблоне: ' + endpoint);
  }
  const stage = await fs.mkdtemp(path.join(path.dirname(target), '.baknin-website-stage-'));
  let backup;
  let preserveBackup = false;
  try {
    const pages = [
      {route: '/', template: 'index.html', file: 'index.html'},
      ...['reviews', 'analysis', 'methodology', 'launch'].map(name => ({route: '/' + name, template: name + '.html', file: name + '/index.html'})),
      ...snapshot.universities.items.map(university => ({route: '/universities/' + university.id,
        template: 'university.html', file: 'universities/' + university.id + '/index.html', university_id: university.id})),
      {route: '/404', template: 'error.html', file: '404.html', status: 404, message: 'Страница не найдена'},
    ];
    for (const page of pages) {
      const context = {config, url_for: urlFor, request: {path: page.route}};
      for (const key of ['university_id', 'status', 'message']) if (page[key] !== undefined) context[key] = page[key];
      const html = prefixLinks(engine.render(page.template, context), base);
      if (/\{%|\{\{/.test(html)) throw new Error('В опубликованной странице остались инструкции шаблонизатора');
      const filename = path.join(stage, page.file);
      await fs.mkdir(path.dirname(filename), {recursive: true});
      await fs.writeFile(filename, html);
    }
    await fs.mkdir(path.join(stage, 'data'));
    await fs.writeFile(path.join(stage, 'data/site.json'), JSON.stringify(snapshot, null, 2) + '\n');
    await fs.mkdir(path.join(stage, 'static'));
    for (const file of assets) await fs.copyFile(path.join(ROOT, 'static', file), path.join(stage, 'static', file));
    await fs.writeFile(path.join(stage, '.nojekyll'), '');
    await fs.writeFile(path.join(stage, '.baknin-pages-build'), MARKER);
    try {
      await fs.lstat(target);
      backup = await fs.mkdtemp(path.join(path.dirname(target), '.baknin-website-backup-'));
      await fs.rename(target, path.join(backup, 'previous'));
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    try { await fs.rename(stage, target); }
    catch (error) {
      if (backup) {
        try { await fs.rename(path.join(backup, 'previous'), target); }
        catch (restoreError) {
          preserveBackup = true;
          throw new Error('Не удалось заменить сайт; предыдущая сборка сохранена в ' + path.join(backup, 'previous'), {cause: restoreError});
        }
      }
      throw error;
    }
    return {output: target, base_path: base, pages: pages.length,
      universities: snapshot.universities.items.length, reviews: snapshot.reviews.length};
  } finally {
    await fs.rm(stage, {recursive: true, force: true});
    if (backup && !preserveBackup) await fs.rm(backup, {recursive: true, force: true});
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const options = {};
    const names = {'--output': 'output', '--base-path': 'basePath', '--catalog': 'catalog', '--reviews': 'reviews', '--rules': 'rules'};
    for (let i = 2; i < process.argv.length; i += 2) {
      if (!names[process.argv[i]] || !process.argv[i + 1] || process.argv[i + 1].startsWith('--')) {
        throw new Error('Используйте --output ПАПКА, --base-path /Baknin_Project/ и необязательные --catalog, --reviews, --rules');
      }
      options[names[process.argv[i]]] = process.argv[i + 1];
    }
    console.log(JSON.stringify(await buildWebsite(options)));
  } catch (error) {
    console.error('Ошибка сборки сайта: ' + error.message);
    process.exitCode = 1;
  }
}
