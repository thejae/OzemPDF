'use strict';

/**
 * Electron main process.
 *
 * The window is a thin shell: it spawns the Python engine as a local server,
 * waits for its ready line, then loads the HTML/CSS/JS interface from it. The
 * Python process is the only backend, so the browser build and the desktop
 * build share one API and one state model.
 */

const { app, BrowserWindow, dialog, ipcMain, Menu, shell } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const READY_PREFIX = 'OZEMPDF_READY ';
const START_TIMEOUT_MS = 30000;

/**
 * Portable data root.
 *
 * On Windows a packaged app is a portable folder: every file it owns lives in
 * `data/` beside `OzemPDF.exe` (settings, caches, logs, temp, staging), so the
 * folder can be moved or deleted as a unit and nothing is left in the user
 * profile. macOS/Linux app bundles are not reliably writable, so packaged
 * builds there keep Electron's normal userData location. A source checkout
 * uses `electron/.dev-data/`, which is git-ignored.
 */
const PORTABLE = app.isPackaged && process.platform === 'win32';
const DATA_DIR = PORTABLE
  ? path.join(path.dirname(process.execPath), 'data')
  : app.isPackaged
    ? app.getPath('userData')
    : path.join(__dirname, '.dev-data', 'data');
const TEMP_DIR = path.join(DATA_DIR, 'temp');
try {
  fs.mkdirSync(TEMP_DIR, { recursive: true });
} catch (error) {
  /* a read-only folder is reported by the missing log and engine errors */
}
if (PORTABLE || !app.isPackaged) {
  for (const variable of ['TEMP', 'TMP', 'TMPDIR']) {
    process.env[variable] = TEMP_DIR;
  }
  try {
    app.setPath('userData', DATA_DIR);
    app.setPath('sessionData', DATA_DIR);
    app.setPath('crashDumps', path.join(DATA_DIR, 'crashes'));
  } catch (error) {
    /* Electron keeps its default paths; the log below records the failure */
  }
}

const LOG_PATH = path.join(DATA_DIR, 'main.log');

function log(message) {
  try {
    fs.appendFileSync(LOG_PATH, `${new Date().toISOString()} ${message}\n`);
  } catch (error) {
    /* logging must never prevent startup */
  }
}

log(`--- start argv=${JSON.stringify(process.argv.slice(1))} packaged=${app.isPackaged} data=${DATA_DIR} resources=${process.resourcesPath}`);
process.on('uncaughtException', (error) => log(`uncaughtException ${error.stack || error}`));
process.on('unhandledRejection', (reason) => log(`unhandledRejection ${reason}`));

let mainWindow = null;
let engineProcess = null;
let serverUrl = null;
let quitting = false;
let forceClose = false;

function repoRoot() {
  return path.resolve(__dirname, '..');
}

function engineCommand() {
  const exeName = process.platform === 'win32' ? 'ozempdf-engine.exe' : 'ozempdf-engine';
  const root = app.isPackaged
    ? path.join(process.resourcesPath, 'engine')
    : path.join(repoRoot(), 'artifacts', 'dist', 'engine');
  // Directory builds are preferred (no temp extraction); a one-file build is
  // still accepted for anyone who packages that way.
  const candidates = [
    path.join(root, 'ozempdf-engine', exeName),
    path.join(root, exeName),
  ];
  const bundled = candidates.find((candidate) => fs.existsSync(candidate));
  const useBundled = app.isPackaged || process.env.OZEMPDF_USE_BUNDLED === '1';
  if (useBundled && bundled) {
    return { command: bundled, args: ['--serve', '--port', '0', '--no-browser'], cwd: path.dirname(bundled), python: false };
  }
  // A source checkout runs the live Python package so edits take effect; the
  // frozen sidecar is what ships.
  const python = process.env.OZEMPDF_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  return {
    command: python,
    args: ['-m', 'ozempdf', '--serve', '--port', '0', '--no-browser'],
    cwd: repoRoot(),
    python: true,
  };
}

function startEngine() {
  const spec = engineCommand();
  const env = {
    ...process.env,
    OZEMPDF_DATA_DIR: DATA_DIR,
    TEMP: TEMP_DIR,
    TMP: TEMP_DIR,
    PYTHONUNBUFFERED: '1',
    PYTHONIOENCODING: 'utf-8',
  };
  if (spec.python) {
    const srcDir = app.isPackaged
      ? path.join(process.resourcesPath, 'engine', 'src')
      : path.join(repoRoot(), 'python', 'src');
    env.PYTHONPATH = srcDir + (env.PYTHONPATH ? path.delimiter + env.PYTHONPATH : '');
  }
  log(`engine command=${spec.command} args=${JSON.stringify(spec.args)} cwd=${spec.cwd}`);

  return new Promise((resolve, reject) => {
    let settled = false;
    let child;
    try {
      child = spawn(spec.command, spec.args, { cwd: spec.cwd, env, windowsHide: true });
    } catch (error) {
      log(`engine spawn threw ${error.stack || error}`);
      reject(error);
      return;
    }
    engineProcess = child;
    log(`engine spawned pid=${child.pid}`);
    let buffer = '';

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        log('engine startup timeout');
        reject(new Error('The OzemPDF engine did not start in time.'));
      }
    }, START_TIMEOUT_MS);

    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      buffer += chunk;
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 1);
        if (line.startsWith(READY_PREFIX)) {
          try {
            const info = JSON.parse(line.slice(READY_PREFIX.length));
            if (!settled) {
              settled = true;
              clearTimeout(timer);
              log(`engine ready url=${info.url}`);
              resolve(info.url);
            }
          } catch (error) {
            log(`engine ready line unreadable: ${line}`);
          }
        } else if (line) {
          log(`engine stdout: ${line.slice(0, 400)}`);
        }
      }
    });

    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => {
      log(`engine stderr: ${String(chunk).trim().slice(0, 800)}`);
      process.stderr.write(chunk);
    });

    child.on('error', (error) => {
      log(`engine error event ${error.stack || error}`);
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(error);
      }
    });

    child.on('exit', (code, signal) => {
      engineProcess = null;
      log(`engine exited code=${code} signal=${signal}`);
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(new Error(`The OzemPDF engine exited with code ${code}.`));
      } else if (!quitting) {
        dialog.showErrorBox(
          'OzemPDF engine stopped',
          'The local engine process ended unexpectedly. Reopen OzemPDF to continue.\n\n' +
            'If this keeps happening, run `python -m ozempdf --serve` in a terminal to see the error.'
        );
        app.quit();
      }
    });
  });
}

function createWindow() {
  log('createWindow');
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 1000,
    minHeight: 680,
    show: false,
    backgroundColor: '#111210',
    title: 'OzemPDF',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
    },
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.webContents.on('did-finish-load', () => log('window loaded'));
  mainWindow.webContents.on('did-fail-load', (_event, code, description, url) => {
    log(`window load failed code=${code} description=${description} url=${url}`);
  });
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    log(`renderer gone reason=${details.reason} exitCode=${details.exitCode}`);
  });
  mainWindow.on('unresponsive', () => log('window unresponsive'));
  mainWindow.on('closed', () => log('window closed'));
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!serverUrl || !url.startsWith(serverUrl)) event.preventDefault();
  });

  // Guard against closing mid-run (US-053).
  mainWindow.on('close', async (event) => {
    if (forceClose || !mainWindow) return;
    event.preventDefault();
    let info = null;
    try {
      info = await mainWindow.webContents.executeJavaScript(
        'window.__ozemCloseGuard ? window.__ozemCloseGuard() : null'
      );
    } catch (error) {
      info = null;
    }
    if (info && info.running) {
      const choice = dialog.showMessageBoxSync(mainWindow, {
        type: 'warning',
        buttons: [info.cancel || 'Cancel', info.quit || 'Quit anyway'],
        defaultId: 0,
        cancelId: 0,
        title: info.title || 'Slimming is still running',
        message: info.body || 'Files are still being processed.',
        detail: info.detail || '',
      });
      if (choice !== 1) return;
      try {
        await mainWindow.webContents.executeJavaScript(
          'window.__ozemStopAndWait ? window.__ozemStopAndWait() : true'
        );
      } catch (error) {
        /* the engine is killed below regardless */
      }
    }
    forceClose = true;
    mainWindow.destroy();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  if (serverUrl) mainWindow.loadURL(serverUrl);
}

ipcMain.handle('dialog:pick-paths', async (_event, kind) => {
  const window = mainWindow;
  const options = { properties: ['openFile'], filters: [] };
  if (kind === 'folder' || kind === 'output') {
    options.properties = kind === 'output' ? ['openDirectory', 'createDirectory'] : ['openDirectory'];
  } else if (kind === 'files') {
    options.properties = ['openFile', 'multiSelections'];
    options.filters = [{ name: 'PDF documents', extensions: ['pdf'] }];
  } else if (kind === 'cert') {
    options.filters = [{ name: 'PKCS#12 certificate', extensions: ['p12', 'pfx'] }];
  }
  const result = await dialog.showOpenDialog(window, options);
  return result.canceled ? [] : result.filePaths;
});

ipcMain.handle('shell:open-path', async (_event, target) => {
  if (typeof target !== 'string' || !target) return { ok: false };
  return { ok: (await shell.openPath(target)) === '' };
});

ipcMain.handle('shell:show-item', (_event, target) => {
  if (typeof target === 'string' && target) shell.showItemInFolder(target);
  return { ok: true };
});

function stopEngine() {
  if (!engineProcess) return;
  const child = engineProcess;
  engineProcess = null;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true });
    } else {
      child.kill('SIGTERM');
    }
  } catch (error) {
    /* best effort */
  }
}

app.whenReady().then(async () => {
  log('app ready');
  if (process.platform === 'darwin') {
    Menu.setApplicationMenu(
      Menu.buildFromTemplate([{ role: 'appMenu' }, { role: 'editMenu' }, { role: 'windowMenu' }])
    );
  } else {
    Menu.setApplicationMenu(null);
  }

  try {
    serverUrl = await startEngine();
  } catch (error) {
    log(`startup failed: ${error.stack || error}`);
    dialog.showErrorBox(
      'OzemPDF could not start',
      `${error.message}\n\n` +
        'The desktop app needs its Python engine. Install the package with\n' +
        '  pip install -e .\n' +
        'or point OZEMPDF_PYTHON at a Python executable that has OzemPDF installed.'
    );
    app.quit();
    return;
  }
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  log('window-all-closed');
  quitting = true;
  stopEngine();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  log('before-quit');
  quitting = true;
  stopEngine();
});

app.on('child-process-gone', (_event, details) => {
  log(`child process gone type=${details.type} reason=${details.reason} exitCode=${details.exitCode}`);
});
