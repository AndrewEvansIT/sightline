// power: prints how much power the Mac is drawing right now, as JSON:
//   {"cpu_w": 4.1, "gpu_w": 12.3, "ane_w": 0.0, "dram_w": 1.2, "total_w": 17.6, "system_w": 41.2, "sample_ms": 500}
// Read-only; no admin rights needed. Used by Sightline's Power widget and the speed test.
//
// Chip parts come from the "Energy Model" channels of IOReport, the private interface Activity Monitor and
// powermetrics use, read the way the open-source tool macmon does: two energy readings ~500 ms apart, the
// energy used in between divided by the time. The whole-Mac figure (system_w) is the SMC's own power sensor
// (key PSTR), also read without admin rights. Neither is a public Apple API, so a macOS update can change them.
//
// A part whose counter didn't move at all during the sample prints null, not 0: some macOS versions stop
// updating the CPU, Neural Engine and memory counters (seen on macOS 27 with an M3 Ultra, where only the
// GPU counter moves). A busy CPU never uses exactly zero energy, so a still counter means "can't tell".
// total_w is the sum of the four parts, or null when any of them is unknown.
//
//   power            one sample (500 ms)
//   power 250        one sample over 250 ms (100 to 5000)
//   power --channels every Energy Model channel with its energy over the sample (for checking a new chip)
//
// Build: helpers/build.sh (links -lIOReport)

import Foundation
import IOKit

// IOReport, declared by hand: there is no public header. Same signatures as macmon and others use.
@_silgen_name("IOReportCopyChannelsInGroup")
func IOReportCopyChannelsInGroup(_ group: CFString?, _ subgroup: CFString?, _ a: UInt64, _ b: UInt64, _ c: UInt64) -> Unmanaged<CFDictionary>?

@_silgen_name("IOReportCreateSubscription")
func IOReportCreateSubscription(_ a: UnsafeMutableRawPointer?, _ channels: CFMutableDictionary,
                                _ subscribed: UnsafeMutablePointer<Unmanaged<CFMutableDictionary>?>,
                                _ channelID: UInt64, _ b: CFTypeRef?) -> OpaquePointer?

@_silgen_name("IOReportCreateSamples")
func IOReportCreateSamples(_ subscription: OpaquePointer, _ channels: CFMutableDictionary, _ b: CFTypeRef?) -> Unmanaged<CFDictionary>?

@_silgen_name("IOReportChannelGetGroup")
func IOReportChannelGetGroup(_ item: CFDictionary) -> Unmanaged<CFString>?

@_silgen_name("IOReportChannelGetChannelName")
func IOReportChannelGetChannelName(_ item: CFDictionary) -> Unmanaged<CFString>?

@_silgen_name("IOReportChannelGetUnitLabel")
func IOReportChannelGetUnitLabel(_ item: CFDictionary) -> Unmanaged<CFString>?

@_silgen_name("IOReportSimpleGetIntegerValue")
func IOReportSimpleGetIntegerValue(_ item: CFDictionary, _ index: Int32) -> Int64

let args = CommandLine.arguments.dropFirst()
let listChannels = args.contains("--channels")
let ms = max(100, min(5000, args.compactMap { Int($0) }.first ?? 500))

// SMC: the whole Mac's power draw ---------------------------------------------------------

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

/// System power in watts from the SMC key PSTR (a float), or nil.
func smcSystemWatts() -> Double? {
    var conn: io_connect_t = 0
    let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("AppleSMC"))
    guard service != 0, IOServiceOpen(service, mach_task_self_, 0, &conn) == kIOReturnSuccess else { return nil }
    defer { IOServiceClose(conn); IOObjectRelease(service) }
    func call(_ input: inout KeyData) -> KeyData? {
        var output = KeyData()
        var size = MemoryLayout<KeyData>.stride
        let r = IOConnectCallStructMethod(conn, 2, &input, MemoryLayout<KeyData>.stride, &output, &size)
        return r == kIOReturnSuccess && output.result == 0 ? output : nil
    }
    var q = KeyData(); q.key = fourCC("PSTR"); q.command = 9  // key info
    guard let info = call(&q), info.info.1 == fourCC("flt "), info.info.0 >= 4 else { return nil }
    var r = KeyData(); r.key = fourCC("PSTR"); r.info.0 = info.info.0; r.command = 5  // read bytes
    guard let out = call(&r) else { return nil }
    let bytes = withUnsafeBytes(of: out.bytes) { Array($0.prefix(4)) }
    let w = Double(bytes.withUnsafeBytes { $0.load(as: Float.self) })
    return w.isFinite && w > 0 && w < 5000 ? w : nil
}

// IOReport: energy per chip part --------------------------------------------------------

struct Reading { let name: String; let unit: String; let value: Int64 }

func readings(_ sub: OpaquePointer, _ channels: CFMutableDictionary) -> [Reading] {
    guard let s = IOReportCreateSamples(sub, channels, nil)?.takeRetainedValue(),
          let items = (s as NSDictionary)["IOReportChannels"] as? [NSDictionary] else { return [] }
    return items.compactMap { item in
        let cf = item as CFDictionary
        guard (IOReportChannelGetGroup(cf)?.takeUnretainedValue() as String?) == "Energy Model" else { return nil }
        return Reading(name: IOReportChannelGetChannelName(cf)?.takeUnretainedValue() as String? ?? "",
                       unit: IOReportChannelGetUnitLabel(cf)?.takeUnretainedValue() as String? ?? "",
                       value: IOReportSimpleGetIntegerValue(cf, 0))
    }
}

func joules(_ value: Int64, unit: String) -> Double? {
    switch unit.trimmingCharacters(in: .whitespaces) {
    case "mJ": return Double(value) / 1e3
    case "uJ", "µJ": return Double(value) / 1e6
    case "nJ": return Double(value) / 1e9
    case "J": return Double(value)
    default: return nil
    }
}

var first: [Reading] = [], second: [Reading] = []
var seconds = Double(ms) / 1000
var systemW: Double? = nil
if let chanRef = IOReportCopyChannelsInGroup("Energy Model" as CFString, nil, 0, 0, 0) {
    let chan = chanRef.takeRetainedValue()
    var subscribed: Unmanaged<CFMutableDictionary>?
    if let channels = CFDictionaryCreateMutableCopy(kCFAllocatorDefault, CFDictionaryGetCount(chan), chan),
       let sub = IOReportCreateSubscription(nil, channels, &subscribed, 0, nil) {
        first = readings(sub, channels)
        let started = Date()
        usleep(useconds_t(ms * 1000))
        second = readings(sub, channels)
        seconds = Date().timeIntervalSince(started)
    }
}
systemW = smcSystemWatts()

// IOReportCreateSamplesDelta returns all zeros on some macOS versions, so the difference is taken here,
// channel by channel (same order in both readings; names checked).
var cpu = 0.0, gpu = 0.0, ane = 0.0, dram = 0.0
var cpuMoved = false, gpuSeen = false, gpuMoved = false
var listed: [[String: Any]] = []
if first.count == second.count {
    for (a, b) in zip(first, second) where a.name == b.name {
        guard let j = joules(b.value &- a.value, unit: b.unit), j >= 0 else { continue }
        let w = j / seconds
        let name = b.name
        if listChannels {
            listed.append(["name": name, "unit": b.unit, "watts": (w * 1000).rounded() / 1000])
            continue
        }
        // Totals only, so per-cluster and per-core channels aren't counted twice. Ultra chips repeat the
        // totals per die ("DIE_0_CPU Energy", "ANE0_1", "DRAM0_1"), which are summed.
        if name.hasSuffix("CPU Energy") {
            cpu += w; cpuMoved = cpuMoved || w > 0
        } else if name == "GPU Energy" {
            gpu += w; gpuSeen = true; gpuMoved = gpuMoved || w > 0
        } else if name.hasPrefix("ANE") {
            ane += w
        } else if name.hasPrefix("DRAM") {
            dram += w
        }
    }
}

func r(_ x: Double) -> Double { (x * 100).rounded() / 100 }
var out: [String: Any] = ["sample_ms": Int(seconds * 1000)]
if listChannels {
    out["channels"] = listed
} else {
    // CPU, Neural Engine and memory counters are updated together; if the CPU's didn't move, none can be trusted.
    let parts: [String: Double?] = ["cpu_w": cpuMoved ? r(cpu) : nil, "gpu_w": gpuSeen ? r(gpu) : nil,
                                    "ane_w": cpuMoved ? r(ane) : nil, "dram_w": cpuMoved ? r(dram) : nil]
    for (k, v) in parts { out[k] = v ?? NSNull() }
    out["total_w"] = cpuMoved && gpuSeen ? r(cpu + gpu + ane + dram) : NSNull()
    out["system_w"] = systemW.map(r) ?? NSNull()
    out["gpu_counter_moving"] = gpuMoved
    if first.isEmpty && systemW == nil {
        out["error"] = "no power readings on this Mac"
    }
}
let data = try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)
exit(out["error"] == nil ? 0 : 1)
