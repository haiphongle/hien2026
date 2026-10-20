const { app, BrowserWindow, Menu, dialog, shell, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { pathToFileURL } = require('node:url');

const resource = path.join(process.resourcesPath, 'runtime');
const windows = process.platform === 'win32';
// Overrides are for isolated build smoke tests only; Finder uses these defaults.
const data = process.env.FRAMEWEAVER_DESKTOP_TEST_DATA || path.join(app.getPath('appData'), 'FrameWeaver');
const port = Number(process.env.FRAMEWEAVER_DESKTOP_TEST_PORT || 8100);
const wsPort = Number(process.env.FRAMEWEAVER_DESKTOP_TEST_WS_PORT || 9222);
const origin = `http://127.0.0.1:${port}`;
const startup = path.join(__dirname, 'startup.html');
let window, backend, log, starting = false, quitting = false, retryState;

app.setName('FrameWeaver');
fs.mkdirSync(path.join(data, 'desktop'), { recursive: true, mode: 0o700 });
// Isolated Windows installer tests retain early failures before Python logging exists.
const trace = (message) => {
  if (windows && process.env.FRAMEWEAVER_DESKTOP_TEST_DATA) fs.appendFileSync(path.join(data, 'desktop-startup.log'), `${new Date().toISOString()} ${message}\n`);
};
trace('Desktop main loaded');
process.on('uncaughtExceptionMonitor', (error) => trace(error.stack || error.message));
app.setPath('userData', path.join(data, 'desktop'));
const primary = app.requestSingleInstanceLock();
if (!primary) app.quit();

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function health() {
  try {
    const res = await fetch(`${origin}/health`, { signal: AbortSignal.timeout(1500) });
    const body = await res.json();
    return res.ok && body.status === 'ok' && typeof body.extension_connected === 'boolean' ? body : null;
  } catch { return null; }
}
function busy(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: '127.0.0.1', port });
    socket.setTimeout(1000);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', () => resolve(false));
    socket.once('timeout', () => { socket.destroy(); resolve(true); });
  });
}
function status(message, retry = false, reuse = false) {
  retryState = { message, retry, reuse };
  if (window && !window.isDestroyed()) window.webContents.send('startup-status', retryState);
}
async function openExternal(url) {
  try {
    const parsed = new URL(url);
    if (['https:', 'http:', 'mailto:'].includes(parsed.protocol)) await shell.openExternal(url);
  } catch { /* Ignore non-web links and unsupported schemes. */ }
}
function internal(url) {
  try { return new URL(url).origin === origin; } catch { return false; }
}
async function dashboard() { await window.loadURL(origin); }
async function start() {
  trace('Backend startup requested');
  if (starting || backend) return;
  starting = true;
  try {
    status('Đang kiểm tra môi trường…');
    if (await busy(port)) {
      const current = await health();
      status(current ? 'FrameWeaver đang chạy ở một phiên khác. Bạn có thể mở phiên đó; dữ liệu và tác vụ sẽ thuộc phiên đang chạy.' : `Cổng ${port} đang được ứng dụng khác sử dụng. Đóng ứng dụng đó rồi thử lại.`, true, Boolean(current));
      return;
    }
    if (await busy(wsPort)) {
      status(`Cổng extension ${wsPort} đang được sử dụng. Đóng phiên FrameWeaver cũ rồi thử lại.`, true);
      return;
    }
    fs.mkdirSync(data, { recursive: true, mode: 0o700 });
    const logs = path.join(data, 'logs');
    fs.mkdirSync(logs, { recursive: true, mode: 0o700 });
    const logPath = path.join(logs, 'backend.log');
    if (fs.existsSync(logPath) && fs.statSync(logPath).size > 5 * 1024 * 1024) fs.renameSync(logPath, `${logPath}.previous`);
    log = fs.openSync(logPath, 'a', 0o600);
    const python = windows ? path.join(resource, 'python', 'python.exe') : path.join(resource, 'python', 'bin', 'python3');
    const code = path.join(resource, 'app');
    const home = app.getPath('home');
    // Finder does not inherit a shell PATH. Bundled media always takes priority.
    const search = [path.join(resource, 'media', 'bin'), path.dirname(python), path.join(home, '.local/bin'),
      path.join(home, '.npm-global/bin'), ...(windows ? (process.env.PATH || '').split(path.delimiter) : ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin'])];
    const env = { ...process.env, PATH: search.join(path.delimiter), PYTHONPATH: code,
      PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1', PYTHONNOUSERSITE: '1',
      FRAMEWEAVER_DESKTOP: '1', FRAMEWEAVER_PARENT_PID: String(process.pid), FRAMEWEAVER_DATA_DIR: data, FRAMEWEAVER_WEB_DIR: path.join(resource, 'web'),
      ...(windows ? { FRAMEWEAVER_DESKTOP_CONTROL: 'stdin', PYTHONUTF8: '1' } : {}),
      API_HOST: '127.0.0.1', API_PORT: String(port), WS_HOST: '127.0.0.1', WS_PORT: String(wsPort), GLA_RELOAD: '0' };
    delete env.PYTHONHOME;
    backend = spawn(python, ['-m', 'agent.main'], { cwd: code, env, stdio: [windows ? 'pipe' : 'ignore', log, log], detached: true, windowsHide: true });
    backend.stdin?.on('error', () => {}); // A stopped backend can close its control pipe first.
    const owned = backend;
    let startupError;
    backend.once('error', (error) => { startupError = `Không khởi động được Python: ${error.message}`; });
    backend.once('close', () => {
      if (backend === owned) backend = null;
      if (log !== undefined) { fs.closeSync(log); log = undefined; }
      if (!quitting) {
        window.loadFile(startup).then(() => status(startupError || 'Backend đã dừng. Mở nhật ký để xem chi tiết rồi thử lại.', true));
      }
    });
    status('Đang mở cơ sở dữ liệu và các dịch vụ…');
    for (let attempt = 0; attempt < 120; attempt++) {
      if (!backend) return;
      if (await health()) { await dashboard(); return; }
      await wait(500);
    }
    status('Backend khởi động lâu hơn dự kiến. Mở nhật ký để kiểm tra.', false);
  } catch (error) { status(error.message, true); }
  finally { starting = false; }
}
async function extension() {
  const destination = path.join(data, 'extension');
  fs.mkdirSync(data, { recursive: true, mode: 0o700 });
  fs.rmSync(path.join(destination, '_metadata'), { recursive: true, force: true });
  fs.cpSync(path.join(resource, 'extension'), destination, { recursive: true });
  await shell.openPath(destination);
  await dialog.showMessageBox(window, { type: 'info', message: 'Cài extension cho Google Flow',
    detail: 'Trong Chrome: Mở Extensions → Manage Extensions → Developer mode → Load unpacked, rồi chọn thư mục extension vừa mở. Đăng nhập Google Flow trong Chrome. Extension kết nối tự động khi FrameWeaver đang chạy.' });
}
function menu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'FrameWeaver', submenu: [{ role: 'about' }, { type: 'separator' }, ...(!windows ? [{ role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' }, { type: 'separator' }] : []), { role: 'quit' }] },
    { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
    { label: 'Ứng dụng', submenu: [
      { label: 'Cài đặt', click: () => window.loadURL(`${origin}/settings`) },
      { label: 'Mở trong trình duyệt', click: () => openExternal(origin) },
      { label: 'Thư mục dữ liệu', click: () => shell.openPath(data) },
      { label: 'Thư mục bản xuất', click: () => { const dir = path.join(data, 'output'); fs.mkdirSync(dir, { recursive: true }); shell.openPath(dir); } },
      { label: 'Nhật ký', click: () => shell.openPath(path.join(data, 'logs')) },
      { type: 'separator' }, { label: 'Cài extension Google Flow…', click: extension },
    ] },
    { role: 'help', submenu: [
      { label: windows ? 'Hướng dẫn Windows' : 'Hướng dẫn macOS', click: () => shell.openPath(path.join(resource, windows ? 'HUONG-DAN-WINDOWS.html' : 'HUONG-DAN-MACOS.html')) },
      { label: 'GitHub FrameWeaver', click: () => openExternal('https://github.com/phucnguyentvp1/frameweaver') },
    ] },
  ]));
}
ipcMain.handle('startup-action', async (event, action) => {
  trace(`Startup IPC ${action}: ${event.senderFrame?.url} expected ${pathToFileURL(startup).href}`);
  if (event.senderFrame?.url !== pathToFileURL(startup).href) return;
  if (action === 'ready' && retryState) status(retryState.message, retryState.retry, retryState.reuse);
  if (action === 'retry') return start();
  if (action === 'reuse' && await health()) return dashboard();
  if (action === 'logs') return shell.openPath(path.join(data, 'logs'));
  if (action === 'data') return shell.openPath(data);
});
app.on('second-instance', () => { if (window?.isMinimized()) window.restore(); window?.show(); window?.focus(); });
app.on('before-quit', async (event) => {
  if (quitting) return;
  if (!backend) { quitting = true; return; }
  event.preventDefault();
  const answer = await dialog.showMessageBox(window, { type: 'question', buttons: ['Tiếp tục sử dụng', 'Thoát ứng dụng'], defaultId: 0, cancelId: 0,
    message: 'Thoát FrameWeaver?', detail: 'Các tác vụ đang chạy sẽ dừng. Hãy đợi tạo hoặc xuất video hoàn tất trước khi thoát.' });
  if (answer.response !== 1) return;
  quitting = true;
  const child = backend;
  if (windows) child.stdin.end();
  else child.kill('SIGTERM');
  for (let attempt = 0; attempt < 40 && backend; attempt++) await wait(250);
  if (backend) {
    if (windows) await new Promise(resolve => {
      const killer = spawn(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe'), ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
      killer.once('error', resolve); killer.once('close', resolve);
    });
    else { try { process.kill(-child.pid, 'SIGKILL'); } catch { /* Already exited. */ } }
  }
  app.quit();
});
app.whenReady().then(async () => {
  trace('Electron ready');
  if (!primary) return;
  window = new BrowserWindow({ width: 1440, height: 940, minWidth: 1024, minHeight: 700,
    title: 'FrameWeaver', backgroundColor: '#101018',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true } });
  if (windows && process.env.FRAMEWEAVER_DESKTOP_TEST_DATA) {
    window.webContents.on('did-finish-load', () => trace(`Page loaded: ${window.webContents.getURL()}`));
    window.webContents.on('preload-error', (_event, _file, error) => trace(`Preload: ${error.stack || error.message}`));
    window.webContents.on('console-message', (_event, details, message) => trace(`Renderer: ${typeof details === 'object' ? details.message : message}`));
    window.webContents.on('render-process-gone', (_event, details) => trace(`Renderer stopped: ${details.reason}`));
  }
  window.webContents.setWindowOpenHandler(({ url }) => { openExternal(url); return { action: 'deny' }; });
  window.webContents.on('will-navigate', (event, url) => {
    if (!internal(url)) { event.preventDefault(); openExternal(url); }
  });
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  window.on('close', (event) => { if (!quitting) { event.preventDefault(); if (windows) app.quit(); else window.hide(); } });
  menu();
  await window.loadFile(startup);
  await start();
});
app.on('activate', () => window?.show());
