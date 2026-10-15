import AppKit
import CoreGraphics
import Darwin

setvbuf(stdout, nil, _IOLBF, 0)
func emit(_ kind: String, _ fields: [String:Any] = [:]) {
    var row = fields
    row["kind"] = kind
    row["time"] = ISO8601DateFormatter().string(from: Date())
    if let data = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]), let text = String(data: data, encoding: .utf8) { print(text) }
}
var observers: [NSObjectProtocol] = []
for name in ["com.apple.screenIsLocked", "com.apple.screenIsUnlocked", "com.apple.screensaver.didstart", "com.apple.screensaver.didstop"] {
    observers.append(DistributedNotificationCenter.default().addObserver(forName: Notification.Name(name), object: nil, queue: .main) { note in emit("notification", ["name":note.name.rawValue]) })
}
for name in [NSWorkspace.willSleepNotification, NSWorkspace.didWakeNotification, NSWorkspace.screensDidSleepNotification, NSWorkspace.screensDidWakeNotification, NSWorkspace.sessionDidBecomeActiveNotification, NSWorkspace.sessionDidResignActiveNotification] {
    observers.append(NSWorkspace.shared.notificationCenter.addObserver(forName: name, object: nil, queue: .main) { note in emit("notification", ["name":note.name.rawValue]) })
}
var eventTap: CFMachPort?
var last: [String:Double] = [:]
let callback: CGEventTapCallBack = { _, type, event, _ in
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let tap = eventTap { CGEvent.tapEnable(tap: tap, enable: true) }
        return Unmanaged.passUnretained(event)
    }
    let pid = event.getIntegerValueField(.eventSourceUnixProcessID)
    let state = event.getIntegerValueField(.eventSourceStateID)
    // shortcut: empirically verified with this UU version; revalidate after input-driver updates.
    let kind = pid == 0 && state == 1 ? "local" : "remote"
    let now = ProcessInfo.processInfo.systemUptime
    if now - (last[kind] ?? -10) >= 0.2 {
        last[kind] = now
        emit(kind)
    }
    return Unmanaged.passUnretained(event)
}
let mask = (CGEventMask(1) << CGEventType.flagsChanged.rawValue) | (CGEventMask(1) << CGEventType.keyDown.rawValue) | (CGEventMask(1) << CGEventType.mouseMoved.rawValue) | (CGEventMask(1) << CGEventType.leftMouseDown.rawValue)
let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly, eventsOfInterest: mask, callback: callback, userInfo: nil)
eventTap = tap
if let tap = tap {
    let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
    CGEvent.tapEnable(tap: tap, enable: true)
    emit("ready", ["inputTap":true])
} else { emit("ready", ["inputTap":false]) }
Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
    emit("heartbeat", ["inputTap": eventTap.map { CGEvent.tapIsEnabled(tap: $0) } ?? false])
}
RunLoop.main.run()
