/** Optional developer preview; public visitors use the published HTTPS URL. */
import fs from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const directory = path.resolve(process.env.WEBSITE_PREVIEW_DIRECTORY || '_site');
const port = Number(process.env.WEBSITE_PREVIEW_PORT || 8080);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Неверный WEBSITE_PREVIEW_PORT');
let snapshot;
try { snapshot = JSON.parse(await fs.readFile(path.join(directory, 'data/site.json'), 'utf8')); }
catch { throw new Error('Сначала соберите сайт: npm run build'); }
const base = snapshot.base_path;
const mime = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml'};
const server = http.createServer(async (request, response) => {
  if (!['GET', 'HEAD'].includes(request.method)) { response.writeHead(405); return response.end(); }
  try {
    const url = new URL(request.url, 'http://127.0.0.1');
    let pathname = decodeURIComponent(url.pathname);
    if (pathname === '/' && base !== '/') { response.writeHead(302, {Location: base}); return response.end(); }
    if (!pathname.startsWith(base) || pathname.includes('\0')) { response.writeHead(404); return response.end('Страница не найдена'); }
    const relative = pathname.slice(base.length);
    let file = path.resolve(directory, relative);
    if (file !== directory && !file.startsWith(directory + path.sep)) { response.writeHead(404); return response.end(); }
    const stat = await fs.stat(file);
    if (stat.isDirectory()) {
      if (!pathname.endsWith('/')) { response.writeHead(301, {Location: url.pathname + '/' + url.search}); return response.end(); }
      file = path.join(file, 'index.html');
      await fs.access(file);
    }
    response.writeHead(200, {'Content-Type': mime[path.extname(file)] || 'application/octet-stream',
      'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'"});
    if (request.method === 'HEAD') return response.end();
    createReadStream(file).on('error', () => response.destroy()).pipe(response);
  } catch { response.writeHead(404, {'Content-Type': 'text/plain; charset=utf-8'}); response.end('Страница не найдена'); }
});
server.listen(port, '127.0.0.1', () => console.log(`Просмотр сборки: http://127.0.0.1:${port}${base}`));
