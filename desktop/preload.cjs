const { contextBridge, ipcRenderer } = require('electron');

// Only the bundled startup page uses this bridge. No file paths or commands
// from a web page are accepted by the main process.
contextBridge.exposeInMainWorld('frameweaver', {
  action: (name) => ipcRenderer.invoke('startup-action', name),
  onStatus: (callback) => ipcRenderer.on('startup-status', (_event, value) => callback(value)),
});
