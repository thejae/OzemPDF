'use strict';

/**
 * Preload bridge. Only the narrow surface the interface needs: native pickers,
 * real filesystem paths for dropped files, and handing paths to the OS.
 */

const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('ozempdf', {
  isElectron: true,
  platform: process.platform,
  pickPaths: (kind) => ipcRenderer.invoke('dialog:pick-paths', kind),
  openPath: (target) => ipcRenderer.invoke('shell:open-path', target),
  showItemInFolder: (target) => ipcRenderer.invoke('shell:show-item', target),
  getPathForFile: (file) => {
    try {
      return webUtils.getPathForFile(file);
    } catch (error) {
      return '';
    }
  },
});
