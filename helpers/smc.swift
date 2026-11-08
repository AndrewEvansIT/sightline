// smc: prints fan speeds and chip temperatures from the Mac's System Management Controller as JSON.
// Read-only; no admin rights needed. Used by Sightline's System widget.
// Build: helpers/build.sh

import Foundation
import IOKit

struct KeyData {
    var key: UInt32 = 0
    var vers = (UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt16(0))
    var limits = (UInt16(0), UInt16(0), UInt32(0), UInt32(0), UInt32(0))
    var info = (UInt32(0), UInt32(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0))  // size, type, attributes + C padding
    var result: UInt8 = 0
    var status: UInt8 = 0
    var command: UInt8 = 0
    var data32: UInt32 = 0
    var bytes = (UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0),
                 UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0),
                 UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0),
                 UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0), UInt8(0))
}

func fourCC(_ s: String) -> UInt32 { s.utf8.reduce(0) { $0 << 8 | UInt32($1) } }
func string(_ v: UInt32) -> String { String(bytes: [24, 16, 8, 0].map { UInt8(v >> $0 & 0xff) }, encoding: .ascii) ?? "" }

var conn: io_connect_t = 0
let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("AppleSMC"))
guard service != 0, IOServiceOpen(service, mach_task_self_, 0, &conn) == kIOReturnSuccess else {
    print("{\"error\":\"no SMC\"}"); exit(1)
}

func call(_ input: inout KeyData) -> KeyData? {
    var output = KeyData()
    var size = MemoryLayout<KeyData>.stride
    let r = IOConnectCallStructMethod(conn, 2, &input, MemoryLayout<KeyData>.stride, &output, &size)
    return r == kIOReturnSuccess && output.result == 0 ? output : nil
}

func read(_ key: UInt32) -> (type: String, bytes: [UInt8])? {
    var q = KeyData(); q.key = key; q.command = 9  // key info
    guard let info = call(&q) else { return nil }
    var r = KeyData(); r.key = key; r.info.0 = info.info.0; r.command = 5  // read bytes
    guard let out = call(&r) else { return nil }
    let all = withUnsafeBytes(of: out.bytes) { Array($0) }
    return (string(info.info.1), Array(all.prefix(Int(info.info.0))))
}

func number(_ v: (type: String, bytes: [UInt8])) -> Double? {
    switch v.type {
    case "flt ": return v.bytes.count >= 4 ? Double(v.bytes.withUnsafeBytes { $0.load(as: Float.self) }) : nil
    case "fpe2": return v.bytes.count >= 2 ? Double(UInt16(v.bytes[0]) << 8 | UInt16(v.bytes[1])) / 4 : nil
    case "ui8 ": return v.bytes.first.map(Double.init)
    case "ui16": return v.bytes.count >= 2 ? Double(UInt16(v.bytes[0]) << 8 | UInt16(v.bytes[1])) : nil
    case "sp78": return v.bytes.count >= 2 ? Double(Int16(bitPattern: UInt16(v.bytes[0]) << 8 | UInt16(v.bytes[1]))) / 256 : nil
    default: return nil
    }
}

// Fans
var fans: [[String: Double]] = []
let fanCount = read(fourCC("FNum")).flatMap(number).map(Int.init) ?? 0
for i in 0..<fanCount {
    let speed = read(fourCC("F\(i)Ac")).flatMap(number) ?? 0
    let maxSpeed = read(fourCC("F\(i)Mx")).flatMap(number) ?? 0
    fans.append(["rpm": speed.rounded(), "max": maxSpeed.rounded()])
}

// Temperatures: walk every key; CPU cores are "Tp…"/"Te…", GPU "Tg…" on Apple Silicon.
var cpu: [Double] = [], gpu: [Double] = []
let total = read(fourCC("#KEY")).map { $0.bytes.reduce(0) { $0 << 8 | Int($1) } } ?? 0
for i in 0..<total {
    var q = KeyData(); q.command = 8; q.data32 = UInt32(i)  // key at index
    guard let k = call(&q) else { continue }
    let name = string(k.key)
    guard name.hasPrefix("Tp") || name.hasPrefix("Te") || name.hasPrefix("Tg"), let v = read(k.key).flatMap(number),
          v > 10, v < 120 else { continue }
    (name.hasPrefix("Tg") ? { gpu.append(v) } : { cpu.append(v) })()
}
func avg(_ a: [Double]) -> Double? { a.isEmpty ? nil : (a.reduce(0, +) / Double(a.count) * 10).rounded() / 10 }

let out: [String: Any] = ["fans": fans, "cpu_c": avg(cpu) as Any, "cpu_max_c": cpu.max() as Any,
                          "gpu_c": avg(gpu) as Any, "sensors": cpu.count + gpu.count]
print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
