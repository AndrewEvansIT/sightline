// Sightline.app: the window around the panel page, a menu bar readout, notifications, and open-at-login.
//
// The panel itself is a small Python server (`python3 -m sightline`, stdlib only) that this app runs
// as a child process, from the copy of the package inside the app's Resources folder. The page is
// shown in a WKWebView. Layouts:
//   strip  — borderless, along the bottom of a screen, on the desktop layer (under your windows)
//   column — borderless, along the right edge of a screen, on the desktop layer
//   window — an ordinary window
// Build: app/build.sh

import Cocoa
import ServiceManagement
import UserNotifications
import WebKit

struct Settings {
    var port = 47800
    var layout = "window"
    var screen = "auto"
    var stripHeight: CGFloat = 520
    var columnWidth: CGFloat = 440

    static var configURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Sightline/config.json")
    }

    static func load() -> Settings {
        var s = Settings()
        guard let data = try? Data(contentsOf: configURL),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return s }
        s.apply(obj)
        return s
    }

    mutating func apply(_ obj: [String: Any]) {
        if let v = obj["port"] as? Int { port = v }
        if let v = obj["layout"] as? String { layout = v }
        if let v = obj["screen"] as? String { screen = v }
        if let v = obj["strip_height"] as? Double { stripHeight = CGFloat(v) }
        if let v = obj["strip_height"] as? Int { stripHeight = CGFloat(v) }
        if let v = obj["column_width"] as? Double { columnWidth = CGFloat(v) }
        if let v = obj["column_width"] as? Int { columnWidth = CGFloat(v) }
    }
}

// A borderless panel that can still take keyboard focus (for text boxes in Settings).
final class PanelWindow: NSPanel {
    override var canBecomeKey: Bool { true }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, NSWindowDelegate {
    var settings = Settings.load()
    var server: Process?
    var stopping = false
    var window: NSWindow!
    var web: WKWebView!
    var statusItem: NSStatusItem?
    var menuBarOn = UserDefaults.standard.object(forKey: "menuBar") as? Bool ?? true
    var overlayOpen = false
    var timers: [Timer] = []

    var base: URL { URL(string: "http://127.0.0.1:\(settings.port)/")! }
    var resources: URL { Bundle.main.resourceURL! }
    let desktopLevel = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopIconWindow)) + 1)

    // MARK: Launch

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        guard pythonReady() else { return }
        startServer()
        makeWindow()
        place()
        updateMenuBar()
        NotificationCenter.default.addObserver(self, selector: #selector(screensChanged),
                                               name: NSApplication.didChangeScreenParametersNotification, object: nil)
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        timers.append(Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.refreshStatusItem() })
        timers.append(Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in self?.pollNotifications() })
    }

    // The Command Line Tools ship /usr/bin/python3. Without them, macOS offers to install them.
    func pythonReady() -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
        p.arguments = ["-p"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
        p.waitUntilExit()
        if p.terminationStatus == 0 { return true }
        let alert = NSAlert()
        alert.messageText = "Sightline needs Apple's Command Line Tools"
        alert.informativeText = "They include the Python that runs the panel. Click Install, wait for macOS to finish, then open Sightline again."
        alert.addButton(withTitle: "Install")
        alert.addButton(withTitle: "Quit")
        if alert.runModal() == .alertFirstButtonReturn {
            let i = Process()
            i.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
            i.arguments = ["--install"]
            try? i.run()
        }
        NSApp.terminate(nil)
        return false
    }

    // MARK: The Python server (a child process, restarted if it stops)

    func startServer() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["-m", "sightline", "--port", String(settings.port)]
        p.currentDirectoryURL = resources
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = resources.path
        env["PYTHONUTF8"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        p.environment = env
        let logs = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0].appendingPathComponent("Logs")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let logURL = logs.appendingPathComponent("Sightline.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        if let log = try? FileHandle(forWritingTo: logURL) {
            log.seekToEndOfFile()
            p.standardOutput = log
            p.standardError = log
        }
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                guard let self = self, !self.stopping else { return }
                self.startServer()
            }
        }
        do { try p.run(); server = p } catch { NSLog("Sightline: couldn't start python3: \(error)") }
    }

    func restartServer() {
        stopping = true
        server?.terminate()
        server?.waitUntilExit()
        stopping = false
        startServer()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.web.load(URLRequest(url: self.base)) }
    }

    func applicationWillTerminate(_ note: Notification) {
        stopping = true
        server?.terminate()
    }

    // MARK: Window

    func makeWindow() {
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "sightline")
        config.userContentController.addUserScript(WKUserScript(source: appInfoScript(), injectionTime: .atDocumentStart, forMainFrameOnly: true))
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        buildWindow()
        web.load(URLRequest(url: base))
    }

    func buildWindow() {
        let old = window
        let isWindow = settings.layout == "window"
        if isWindow {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 760),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable],
                             backing: .buffered, defer: false)
            w.title = "Sightline"
            w.minSize = NSSize(width: 360, height: 300)
            w.setFrameAutosaveName("SightlineWindow")
            if !w.setFrameUsingName("SightlineWindow") { w.center() }
            window = w
            NSApp.setActivationPolicy(.regular)
        } else {
            let w = PanelWindow(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            w.becomesKeyOnlyIfNeeded = true
            w.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
            w.level = desktopLevel
            window = w
            NSApp.setActivationPolicy(.accessory)
        }
        window.backgroundColor = NSColor(red: 0.043, green: 0.047, blue: 0.055, alpha: 1)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = web
        old?.contentView = nil
        old?.orderOut(nil)
        window.orderFront(nil)
    }

    func targetScreen() -> NSScreen {
        let screens = NSScreen.screens
        if settings.screen != "auto", let s = screens.first(where: { screenID($0) == settings.screen }) { return s }
        return screens.first { $0.frame.height > $0.frame.width } ?? NSScreen.main ?? screens[0]
    }

    func screenID(_ s: NSScreen) -> String {
        (s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.stringValue ?? s.localizedName
    }

    @objc func place() {
        guard settings.layout != "window" else { return }
        let area = targetScreen().visibleFrame
        let frame: NSRect
        if settings.layout == "column" {
            let w = min(settings.columnWidth, area.width)
            frame = NSRect(x: area.maxX - w, y: area.minY, width: w, height: area.height)
        } else {
            let h = min(settings.stripHeight, area.height)
            frame = NSRect(x: area.minX, y: area.minY, width: area.width, height: h)
        }
        window.setFrame(frame, display: true)
    }

    @objc func screensChanged() {
        place()
        web.evaluateJavaScript(appInfoScript(), completionHandler: nil)
    }

    func setLayout(_ layout: String) {
        guard layout != settings.layout || window == nil else { return }
        settings.layout = layout
        buildWindow()
        place()
        updateMenuBar()
    }

    // While Settings or a card is open, a strip or column floats above other windows; closed, it goes back down.
    func setOverlay(_ open: Bool) {
        overlayOpen = open
        guard settings.layout != "window" else { return }
        if open {
            window.level = .floating
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
        } else {
            window.level = desktopLevel
        }
    }

    func appInfoScript() -> String {
        let screens = NSScreen.screens.map { s -> [String: Any] in
            ["id": screenID(s), "name": s.localizedName, "w": Int(s.frame.width), "h": Int(s.frame.height)]
        }
        let info: [String: Any] = ["screens": screens, "loginItem": SMAppService.mainApp.status == .enabled, "menuBar": menuBarOn,
                                   "version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""]
        let json = String(data: try! JSONSerialization.data(withJSONObject: info), encoding: .utf8)!
        return "window.SightlineApp = \(json);"
    }

    // MARK: Messages from the page

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any] else { return }
        if let conf = body["config"] as? [String: Any] {
            let before = settings
            settings.apply(conf)
            if settings.layout != before.layout { settings.layout = before.layout; setLayout(conf["layout"] as? String ?? before.layout) }
            else { place() }
        }
        if let open = body["overlay"] as? Bool { setOverlay(open) }
        if let on = body["loginItem"] as? Bool {
            do {
                if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            } catch {
                toast("macOS didn't allow that: \(error.localizedDescription)")
            }
            web.evaluateJavaScript(appInfoScript(), completionHandler: nil)
        }
        if let on = body["menuBar"] as? Bool {
            menuBarOn = on
            UserDefaults.standard.set(on, forKey: "menuBar")
            updateMenuBar()
        }
        if let save = body["save"] as? [String: Any], let name = save["name"] as? String, let text = save["text"] as? String {
            let panel = NSSavePanel()
            panel.nameFieldStringValue = name
            NSApp.activate(ignoringOtherApps: true)
            panel.begin { r in
                if r == .OK, let url = panel.url { try? text.write(to: url, atomically: true, encoding: .utf8) }
            }
        }
        if body["restart"] as? Bool == true { restartServer() }
        if body["quit"] as? Bool == true { NSApp.terminate(nil) }
    }

    func toast(_ text: String) {
        let js = "window.Sightline && Sightline.toast(\(String(data: try! JSONSerialization.data(withJSONObject: [text]), encoding: .utf8)!)[0])"
        web.evaluateJavaScript(js, completionHandler: nil)
    }

    // MARK: Web view

    // The server may still be starting (login): keep retrying.
    func webView(_ webView: WKWebView, didFailProvisionalNavigation nav: WKNavigation!, withError error: Error) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { webView.load(URLRequest(url: self.base)) }
    }

    // Links to anything but the panel itself open in the default browser.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, url.host != "127.0.0.1", url.host != "localhost", url.scheme?.hasPrefix("http") == true {
            NSWorkspace.shared.open(url)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let picker = NSOpenPanel()
        picker.allowsMultipleSelection = parameters.allowsMultipleSelection
        picker.canChooseDirectories = false
        NSApp.activate(ignoringOtherApps: true)
        picker.begin { completionHandler($0 == .OK ? picker.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        completionHandler(alert.runModal() == .alertFirstButtonReturn ? field.stringValue : nil)
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    // Closing the window (window layout) keeps Sightline running in the menu bar.
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { !menuBarOn }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showPanel()
        return true
    }

    // MARK: Menu bar readout

    func updateMenuBar() {
        if menuBarOn && statusItem == nil {
            statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            statusItem?.button?.title = "◐"
            statusItem?.button?.font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .regular)
        } else if !menuBarOn, let item = statusItem {
            NSStatusBar.system.removeStatusItem(item)
            statusItem = nil
        }
        statusItem?.menu = statusMenu()
        refreshStatusItem()
    }

    func statusMenu() -> NSMenu {
        let m = NSMenu()
        m.addItem(withTitle: "Show Sightline", action: #selector(showPanel), keyEquivalent: "").target = self
        let layouts = NSMenuItem(title: "Layout", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        for (key, title) in [("strip", "Bottom strip"), ("column", "Side column"), ("window", "Window")] {
            let item = NSMenuItem(title: title, action: #selector(pickLayout(_:)), keyEquivalent: "")
            item.representedObject = key
            item.target = self
            item.state = settings.layout == key ? .on : .off
            sub.addItem(item)
        }
        layouts.submenu = sub
        m.addItem(layouts)
        m.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: ",").target = self
        m.addItem(withTitle: "Make a rig card", action: #selector(openCard), keyEquivalent: "").target = self
        m.addItem(.separator())
        m.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r").target = self
        m.addItem(withTitle: "Quit Sightline", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        return m
    }

    func refreshStatusItem() {
        guard let button = statusItem?.button else { return }
        URLSession.shared.dataTask(with: base.appendingPathComponent("api/status")) { data, _, _ in
            guard let data = data, let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let widgets = obj["widgets"] as? [String: Any] else { return }
            func d(_ id: String) -> [String: Any] { ((widgets[id] as? [String: Any])?["data"] as? [String: Any]) ?? [:] }
            var parts: [String] = []
            if let cpu = d("system")["cpu"] as? Int { parts.append("\(cpu)%") }
            if let used = d("memory")["used_gb"] as? Double { parts.append("\(Int(used.rounded())) GB") }
            if let models = d("ai_runtimes")["models"] as? [Any], !models.isEmpty { parts.append("\(models.count)◆") }
            let alerts = (obj["alerts"] as? [Any])?.count ?? 0
            DispatchQueue.main.async {
                button.title = (alerts > 0 ? "● " : "") + (parts.isEmpty ? "◐" : parts.joined(separator: " · "))
                button.toolTip = "Sightline: processor · memory · models loaded" + (alerts > 0 ? " · \(alerts) need a look" : "")
            }
        }.resume()
    }

    @objc func showPanel() {
        if settings.layout == "window" {
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
        } else {
            window.orderFront(nil)
        }
    }

    @objc func pickLayout(_ sender: NSMenuItem) {
        guard let key = sender.representedObject as? String else { return }
        let js = "Sightline.saveConfig({layout: '\(key)'})"
        web.evaluateJavaScript(js, completionHandler: nil)
        setLayout(key)
    }

    @objc func openSettings() {
        showPanel()
        web.evaluateJavaScript("SightlineSettings.open()", completionHandler: nil)
    }

    @objc func openCard() {
        showPanel()
        web.evaluateJavaScript("SightlineCards.open('rig')", completionHandler: nil)
    }

    @objc func reload() { web.reload() }

    // MARK: Notifications (alerts the server raises)

    func pollNotifications() {
        URLSession.shared.dataTask(with: base.appendingPathComponent("api/alerts/next")) { data, _, _ in
            guard let data = data, let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let notes = obj["notes"] as? [[String: Any]] else { return }
            for n in notes {
                let content = UNMutableNotificationContent()
                content.title = n["title"] as? String ?? "Sightline"
                content.body = n["text"] as? String ?? ""
                if n["level"] as? String == "bad" { content.sound = .default }
                UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
            }
        }.resume()
    }

    // MARK: Main menu (copy, paste and undo need it)

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(), editItem = NSMenuItem(), windowItem = NSMenuItem()
        main.addItem(appItem); main.addItem(editItem); main.addItem(windowItem)
        let app = NSMenu()
        app.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: ",").target = self
        app.addItem(.separator())
        app.addItem(withTitle: "Hide Sightline", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(withTitle: "Quit Sightline", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = app
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r").target = self
        windowItem.submenu = win
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
