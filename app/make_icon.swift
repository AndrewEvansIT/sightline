// Draws Sightline's app icon and writes Sightline.icns into the folder given as the first argument.
// Run by app/build.sh: swift app/make_icon.swift <Resources folder>

import AppKit

let out = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ".")
let iconset = FileManager.default.temporaryDirectory.appendingPathComponent("Sightline-\(UUID().uuidString).iconset")
try! FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

func draw(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px)
    let inset = s * 0.09
    let body = NSRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)
    let shape = NSBezierPath(roundedRect: body, xRadius: body.width * 0.225, yRadius: body.width * 0.225)
    NSGradient(colors: [NSColor(red: 0.09, green: 0.10, blue: 0.13, alpha: 1), NSColor(red: 0.03, green: 0.035, blue: 0.045, alpha: 1)])!
        .draw(in: shape, angle: -90)
    // A ring: most of it teal, the rest violet, like a gauge.
    let c = NSPoint(x: s / 2, y: s / 2 + s * 0.03)
    let r = body.width * 0.28
    let lw = body.width * 0.085
    func arc(_ from: CGFloat, _ to: CGFloat, _ color: NSColor) {
        let p = NSBezierPath()
        p.appendArc(withCenter: c, radius: r, startAngle: from, endAngle: to, clockwise: true)
        p.lineWidth = lw
        p.lineCapStyle = .round
        color.setStroke()
        p.stroke()
    }
    NSColor(white: 1, alpha: 0.08).setStroke()
    let track = NSBezierPath(ovalIn: NSRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2))
    track.lineWidth = lw
    track.stroke()
    arc(90, -160, NSColor(red: 0.31, green: 0.71, blue: 0.79, alpha: 1))
    arc(-175, -250, NSColor(red: 0.77, green: 0.55, blue: 1.0, alpha: 1))
    // The sight line.
    let bar = NSBezierPath(roundedRect: NSRect(x: body.minX + body.width * 0.2, y: body.minY + body.height * 0.17,
                                               width: body.width * 0.6, height: body.height * 0.045), xRadius: s, yRadius: s)
    NSColor(red: 0.20, green: 0.84, blue: 0.29, alpha: 0.9).setFill()
    bar.fill()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for size in [16, 32, 128, 256, 512] {
    try! draw(size).write(to: iconset.appendingPathComponent("icon_\(size)x\(size).png"))
    try! draw(size * 2).write(to: iconset.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
let p = Process()
p.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
p.arguments = ["-c", "icns", iconset.path, "-o", out.appendingPathComponent("Sightline.icns").path]
try! p.run()
p.waitUntilExit()
try? FileManager.default.removeItem(at: iconset)
print(out.appendingPathComponent("Sightline.icns").path)
