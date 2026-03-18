const { app, BrowserWindow } = require("electron");
const path = require("path");
const net = require("net");

const PORT = parseInt(process.env.CODEFISSION_PORT || "19440", 10);

function waitForServer(port, timeout = 15000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    function tryConnect() {
      const sock = new net.Socket();
      sock
        .once("connect", () => {
          sock.destroy();
          resolve();
        })
        .once("error", () => {
          sock.destroy();
          if (Date.now() - start > timeout) {
            reject(new Error("Server did not start in time"));
          } else {
            setTimeout(tryConnect, 200);
          }
        })
        .connect(port, "127.0.0.1");
    }
    tryConnect();
  });
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    icon: path.join(__dirname, "icon.icns"),
    titleBarStyle: "hiddenInset",
    trafficLightPosition: { x: 14, y: 12 },
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  await waitForServer(PORT);
  win.loadURL(`http://localhost:${PORT}`);
}

app.whenReady().then(() => {
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  app.quit();
});
