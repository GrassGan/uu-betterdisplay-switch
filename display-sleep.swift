import Foundation
import CoreGraphics

let expected = Set(CommandLine.arguments.dropFirst())
guard !expected.isEmpty else { exit(2) }
var ids = [CGDirectDisplayID](repeating: 0, count: 16)
var count: UInt32 = 0
guard CGGetOnlineDisplayList(16, &ids, &count) == .success else { exit(1) }
var found = Set<String>()
var allAsleep = true
var allAwake = true
for id in ids.prefix(Int(count)) {
    let key = "\(CGDisplayVendorNumber(id)):\(CGDisplayModelNumber(id)):\(CGDisplaySerialNumber(id))"
    if expected.contains(key) {
        found.insert(key)
        allAsleep = allAsleep && CGDisplayIsAsleep(id) != 0
        allAwake = allAwake && CGDisplayIsAsleep(id) == 0
    }
}
let complete = found == expected
let data = try! JSONSerialization.data(withJSONObject: ["allAsleep": complete && allAsleep, "allAwake": complete && allAwake])
print(String(data: data, encoding: .utf8)!)
