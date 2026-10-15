import AppKit
import ApplicationServices

func attr(_ element: AXUIElement, _ key: CFString) -> AnyObject? {
    var value: AnyObject?
    return AXUIElementCopyAttributeValue(element, key, &value) == .success ? value : nil
}
if CommandLine.arguments.contains("--request-permission") {
    _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
    exit(0)
}
guard AXIsProcessTrusted() else { print("permission-required"); exit(0) }
guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.netease.uuremote").first else {
    print("disconnected"); exit(0)
}
let root = AXUIElementCreateApplication(app.processIdentifier)
AXUIElementSetMessagingTimeout(root, 0.5)
guard let windows = attr(root, kAXWindowsAttribute as CFString) as? [AXUIElement], !windows.isEmpty else {
    print("unknown"); exit(0)
}
var texts: [String] = []
var visited = 0
func walk(_ element: AXUIElement, _ depth: Int) {
    guard depth < 25, visited < 3000 else { return }
    visited += 1
    if let value = attr(element, kAXValueAttribute as CFString) as? String { texts.append(value) }
    if let title = attr(element, kAXTitleAttribute as CFString) as? String { texts.append(title) }
    for child in (attr(element, kAXChildrenAttribute as CFString) as? [AXUIElement]) ?? [] { walk(child, depth + 1) }
}
for window in windows { walk(window, 0) }
// shortcut: matches the installed Chinese UU interface; revalidate after UU UI/language changes.
let active = texts.contains { $0.range(of: "当前有\\s*[1-9][0-9]*\\s*台设备正在控制此电脑", options: .regularExpression) != nil }
let known = texts.contains { $0.contains("全部设备") || $0.contains("控制此电脑") }
print(active ? "connected" : (known ? "disconnected" : "unknown"))
