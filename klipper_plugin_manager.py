#!/usr/bin/env python3
"""Klipper Plugin Manager - веб-интерфейс для установки плагинов (extras).

Только стандартная библиотека Python 3.7+, зависимости не нужны.

Что делает при установке плагина:
  1. проверяет файл (имя, размер, синтаксис, наличие load_config);
  2. кладёт его в <config>/extras/;
  3. создаёт симлинк в ~/klipper/klippy/extras/;
  4. добавляет его в .git/info/exclude (Moonraker не ругается на "dirty");
  5. берёт пример секции из комментария в шапке плагина (блок вида
     "#   [имя_секции]" + строки "#   опция: значение") и сохраняет его в
     <config>/extras/<имя>.cfg; в printer.cfg один раз добавляется строка
     "[include extras/*.cfg]" (перед этим делается резервная копия);
  6. по кнопке перезапускает Klipper через Moonraker.

Запуск:  python3 klipper_plugin_manager.py [--port 8088]
Токен доступа создаётся при первом запуске: ~/.klipper_plugin_manager_token

ВНИМАНИЕ: плагин - это код, который Klipper выполняет на вашей машине.
Доступ к этому сервису эквивалентен удалённому выполнению кода, поэтому
держите его только в доверенной локальной сети и не публикуйте в интернет.
"""
import argparse
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}\.py$')
LOAD_RE = re.compile(r'^def\s+load_config(_prefix)?\s*\(', re.M)
MAX_SIZE = 256 * 1024


class Err(Exception):
    pass


def extract_config(src, name):
    """Пример секции из комментария в шапке плагина; иначе пустая [имя]."""
    lines = src.splitlines()[:120]
    for i, l in enumerate(lines):
        m = re.match(r'^#(\s{2,})\[([^\]]+)\]\s*$', l)
        if not m:
            continue
        indent, out = len(m.group(1)), []
        for l2 in lines[i:]:
            if not re.match(r'^#\s{2,}\S', l2):
                break
            body = l2[1:]
            t = body[indent:] if not body[:indent].strip() else body.lstrip()
            out.append(t.lstrip() if t.lstrip().startswith('#') else t)
        return '\n'.join(out) + '\n'
    if re.search(r'^def\s+load_config\s*\(', src, re.M):
        return '[%s]\n' % name[:-3]
    return None


class Manager:
    def __init__(self, cfg_dir, klipper_dir, moonraker):
        self.store = Path(cfg_dir).expanduser() / 'extras'
        self.extras = Path(klipper_dir).expanduser() / 'klippy' / 'extras'
        self.exclude = Path(klipper_dir).expanduser() / '.git' / 'info' / 'exclude'
        self.moonraker = moonraker.rstrip('/')
        self.main_cfg = Path(cfg_dir).expanduser() / 'printer.cfg'
        self.store.mkdir(parents=True, exist_ok=True)
        if not self.extras.is_dir():
            raise SystemExit("Не найдена папка %s - проверьте --klipper-dir"
                             % self.extras)

    def _ours(self, link):
        return link.is_symlink() and link.resolve().parent == self.store.resolve()

    def _check_name(self, name):
        if not NAME_RE.match(name or ''):
            raise Err("Недопустимое имя файла (латиница, цифры, _ и .py)")

    def _exclude(self, name, add):
        line = 'klippy/extras/' + name
        try:
            lines = (self.exclude.read_text().splitlines()
                     if self.exclude.exists() else [])
            if add and line not in lines:
                lines.append(line)
            elif not add and line in lines:
                lines.remove(line)
            else:
                return
            self.exclude.parent.mkdir(parents=True, exist_ok=True)
            self.exclude.write_text('\n'.join(lines) + '\n')
        except OSError:
            pass

    def _cfg_path(self, name):
        return self.store / (name[:-3] + '.cfg')

    def _main_text(self):
        try:
            return self.main_cfg.read_text()
        except OSError:
            raise Err("Не найден %s" % self.main_cfg)

    def _ensure_include(self):
        """Один раз добавляет [include extras/*.cfg] в начало printer.cfg."""
        text = self._main_text()
        if re.search(r'^\[include\s+extras/\*\.cfg\]', text, re.M):
            return
        bak = self.main_cfg.with_name(
            'printer.cfg.bak-' + time.strftime('%Y%m%d-%H%M%S'))
        bak.write_text(text)
        tmp = self.main_cfg.with_name('printer.cfg.tmp')
        tmp.write_text('[include extras/*.cfg]\n\n' + text)
        os.replace(str(tmp), str(self.main_cfg))

    def get_config(self, name):
        self._check_name(name)
        p = self._cfg_path(name)
        return {'text': p.read_text() if p.exists() else ''}

    def set_config(self, name, data):
        self._check_name(name)
        if len(data) > MAX_SIZE:
            raise Err("Слишком большой конфиг")
        self._cfg_path(name).write_text(data.decode('utf-8'))
        self._ensure_include()
        return "Конфигурация сохранена. Перезапустите Klipper."

    def list(self):
        res = []
        for f in sorted(self.store.glob('*.py')):
            link = self.extras / f.name
            linked = self._ours(link)
            res.append({'name': f.name, 'size': f.stat().st_size,
                        'linked': linked,
                        'conflict': os.path.lexists(link) and not linked})
        return res

    def install(self, name, data):
        self._check_name(name)
        if len(data) > MAX_SIZE:
            raise Err("Файл больше %d КБ" % (MAX_SIZE // 1024))
        try:
            src = data.decode('utf-8')
            compile(src, name, 'exec')  # только проверка синтаксиса
        except UnicodeDecodeError:
            raise Err("Файл должен быть в кодировке UTF-8")
        except SyntaxError as e:
            raise Err("Синтаксическая ошибка, строка %s: %s" % (e.lineno, e.msg))
        if not LOAD_RE.search(src):
            raise Err("Это не модуль Klipper: нет функции load_config()")
        link = self.extras / name
        if os.path.lexists(link) and not self._ours(link):
            raise Err("Имя занято встроенным модулем Klipper: " + name)
        dest = self.store / name
        tmp = self.store / (name + '.tmp')
        tmp.write_bytes(data)
        os.replace(str(tmp), str(dest))
        if link.is_symlink():
            link.unlink()
        link.symlink_to(dest.resolve())
        self._exclude(name, True)
        note = ""
        cfg = self._cfg_path(name)
        snippet = extract_config(src, name)
        if snippet and not cfg.exists():
            head = snippet.splitlines()[0]
            if re.search('^' + re.escape(head) + r'\s*$',
                         self._main_text(), re.M):
                note = " Секция %s уже есть в printer.cfg - не добавлена." % head
            else:
                cfg.write_text(snippet)
                note = " Секция %s добавлена в extras/%s." % (head, cfg.name)
        if cfg.exists():
            self._ensure_include()
        return ("Установлен %s.%s Проверьте настройки и перезапустите Klipper."
                % (name, note))

    def install_url(self, url, name=None):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ('http', 'https'):
            raise Err("Нужна ссылка http(s)")
        if u.netloc == 'github.com' and '/blob/' in u.path:
            u = u._replace(netloc='raw.githubusercontent.com',
                           path=u.path.replace('/blob/', '/', 1))
        name = name or os.path.basename(u.path)
        req = urllib.request.Request(u.geturl(),
                                     headers={'User-Agent': 'klipper-plugin-manager'})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read(MAX_SIZE + 1)
        except Exception as e:
            raise Err("Не удалось скачать: %s" % e)
        return self.install(name, data)

    def remove(self, name):
        self._check_name(name)
        link = self.extras / name
        if self._ours(link):
            link.unlink()
        f = self.store / name
        if f.exists():
            f.unlink()
        self._exclude(name, False)
        cfg = self._cfg_path(name)
        if cfg.exists():
            cfg.unlink()
        return ("Удалён %s вместе с его конфигом. Если секция плагина была "
                "прописана в printer.cfg вручную - удалите её. "
                "Перезапустите Klipper." % name)

    def restart(self):
        req = urllib.request.Request(
            self.moonraker + '/machine/services/restart',
            data=json.dumps({'service': 'klipper'}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:
            raise Err("Moonraker недоступен: %s" % e)
        return "Klipper перезапускается..."


class Handler(BaseHTTPRequestHandler):
    mgr = None
    token = ''

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_SIZE * 2:
            raise Err("Слишком большой запрос")
        return self.rfile.read(n)

    def _handle(self, fn):
        if not hmac.compare_digest(self.headers.get('X-Token', ''), self.token):
            return self._send(401, {'error': 'Неверный токен'})
        try:
            res = fn()
            self._send(200, res if isinstance(res, (list, dict))
                       else {'message': res})
        except Err as e:
            self._send(400, {'error': str(e)})
        except Exception as e:
            self._send(500, {'error': 'Внутренняя ошибка: %s' % e})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/':
            self._send(200, PAGE.encode(), 'text/html; charset=utf-8')
        elif path == '/api/plugins':
            self._handle(self.mgr.list)
        elif path == '/api/config':
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self._handle(lambda: self.mgr.get_config(q.get('name', [''])[0]))
        else:
            self._send(404, {'error': 'not found'})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        m = self.mgr
        routes = {
            '/api/install': lambda: m.install(q.get('name'), self._body()),
            '/api/install_url': lambda: m.install_url(
                *(lambda j: (j.get('url', ''), j.get('name') or None))(
                    json.loads(self._body() or b'{}'))),
            '/api/remove': lambda: m.remove(q.get('name')),
            '/api/config': lambda: m.set_config(q.get('name'), self._body()),
            '/api/restart': m.restart,
        }
        if u.path in routes:
            self._handle(routes[u.path])
        else:
            self._send(404, {'error': 'not found'})


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Klipper Plugin Manager</title>
<style>
:root{color-scheme:light dark;font-family:system-ui,sans-serif}
body{max-width:760px;margin:0 auto;padding:16px}
section{border:1px solid #8884;border-radius:10px;padding:12px 16px;margin:14px 0}
h1{font-size:1.3rem}h2{font-size:1rem;margin:0 0 8px}
table{width:100%;border-collapse:collapse}td{padding:6px 4px;border-top:1px solid #8883}
input[type=text]{width:100%;box-sizing:border-box;padding:6px;margin:4px 0}
button{padding:6px 12px;border-radius:6px;border:1px solid #8886;cursor:pointer}
.ok{color:#2a9d4a}.bad{color:#d33}.mut{color:#888;font-size:.85rem}
#msg{position:sticky;top:0;padding:8px 12px;border-radius:8px;display:none}
</style></head><body>
<h1>Klipper Plugin Manager</h1><div id="msg"></div>
<section><h2>Установленные плагины</h2><table id="list"></table>
<p><button id="restart">Перезапустить Klipper</button></p></section>
<section><h2>Загрузить файл .py</h2>
<input type="file" id="file" accept=".py"> <button id="up">Установить</button></section>
<section><h2>Установить по ссылке</h2>
<input type="text" id="url" placeholder="https://github.com/.../blob/main/plugin.py">
<button id="inst">Установить</button>
<p class="mut">После установки добавьте секцию плагина в printer.cfg и перезапустите Klipper.</p></section>
<dialog id="dlg" style="width:min(560px,92vw)"><h2>Конфигурация: <span id="cfgname"></span></h2>
<textarea id="cfgtext" rows="14" style="width:100%;box-sizing:border-box;font-family:monospace"></textarea>
<p><button id="cfgsave">Сохранить</button> <button id="cfgcancel">Отмена</button></p></dialog>
<script>
const $=s=>document.querySelector(s);let token=localStorage.getItem('t')||'';
function toast(t,bad){const m=$('#msg');m.textContent=t;m.style.display='block';
m.style.background=bad?'#d334':'#2a9d4a44';}
async function api(p,o={}){o.headers={...(o.headers||{}),'X-Token':token};
const r=await fetch(p,o);const j=await r.json().catch(()=>({}));
if(r.status==401){const t=prompt('Токен доступа');if(!t)throw new Error('Нужен токен');
token=t;localStorage.setItem('t',t);return api(p,o);}
if(!r.ok)throw new Error(j.error||r.statusText);return j;}
async function act(fn){try{const j=await fn();if(j.message)toast(j.message);}
catch(e){toast(e.message,true);}load();}
async function load(){try{const l=await api('/api/plugins');
$('#list').innerHTML=l.length?l.map(p=>`<tr><td>${p.name}</td>
<td class="${p.linked?'ok':'bad'}">${p.linked?'подключён':p.conflict?'конфликт имён':'не подключён'}</td>
<td class="mut">${(p.size/1024).toFixed(1)} КБ</td>
<td><button data-c="${p.name}">Настроить</button> <button data-n="${p.name}">Удалить</button></td></tr>`).join('')
:'<tr><td class="mut">Пока ничего не установлено</td></tr>';
document.querySelectorAll('#list button[data-n]').forEach(b=>b.onclick=()=>{
if(confirm('Удалить '+b.dataset.n+'?'))act(()=>api('/api/remove?name='+encodeURIComponent(b.dataset.n),{method:'POST'}));});
}catch(e){toast(e.message,true);}}
$('#up').onclick=()=>{const f=$('#file').files[0];if(!f)return toast('Выберите файл',true);
act(()=>api('/api/install?name='+encodeURIComponent(f.name),{method:'POST',body:f}));};
$('#inst').onclick=()=>act(()=>api('/api/install_url',{method:'POST',
body:JSON.stringify({url:$('#url').value.trim()})}));
$('#restart').onclick=()=>confirm('Перезапустить Klipper? Печать будет прервана.')&&
act(()=>api('/api/restart',{method:'POST'}));
document.addEventListener('click',async e=>{const n=e.target.dataset&&e.target.dataset.c;
if(!n)return;try{const j=await api('/api/config?name='+encodeURIComponent(n));
$('#cfgname').textContent=n.replace('.py','.cfg');$('#cfgtext').value=j.text;
$('#dlg').dataset.n=n;$('#dlg').showModal();}catch(err){toast(err.message,true);}});
$('#cfgsave').onclick=()=>{const n=$('#dlg').dataset.n;$('#dlg').close();
act(()=>api('/api/config?name='+encodeURIComponent(n),{method:'POST',body:$('#cfgtext').value}));};
$('#cfgcancel').onclick=()=>$('#dlg').close();
load();
</script></body></html>"""


def get_token(path):
    path = Path(path).expanduser()
    if path.exists():
        return path.read_text().strip()
    token = secrets.token_urlsafe(16)
    path.write_text(token)
    path.chmod(0o600)
    return token


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=8088)
    ap.add_argument('--config-dir', default='~/printer_data/config')
    ap.add_argument('--klipper-dir', default='~/klipper')
    ap.add_argument('--moonraker', default='http://127.0.0.1:7125')
    ap.add_argument('--token-file', default='~/.klipper_plugin_manager_token')
    a = ap.parse_args()

    Handler.mgr = Manager(a.config_dir, a.klipper_dir, a.moonraker)
    Handler.token = get_token(a.token_file)
    print("Klipper Plugin Manager: http://%s:%d" % (a.host, a.port))
    print("Токен доступа: " + Handler.token, flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
