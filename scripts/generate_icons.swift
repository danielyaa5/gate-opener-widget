// Resize and mask the selected artwork into Apple app icon asset catalogs.
// Run from the repository root: swift scripts/generate_icons.swift
import AppKit
import ImageIO
import UniformTypeIdentifiers

let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
guard let source = CGImageSourceCreateWithURL(root.appendingPathComponent("assets/gate-icon-master.png") as CFURL, nil),
      let artwork = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fatalError("Missing assets/gate-icon-master.png")
}
// iPhone icons must be opaque, so they use an RGB context without alpha. AppKit cannot draw
// into a 3-channel bitmap (the result stays black), hence Core Graphics for both platforms.
func render(size: Int, mac: Bool, to url: URL) throws {
    let alpha: CGImageAlphaInfo = mac ? .premultipliedLast : .noneSkipLast
    guard let context = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: alpha.rawValue) else {
        throw CocoaError(.fileWriteUnknown)
    }
    context.interpolationQuality = .high
    context.scaleBy(x: CGFloat(size) / 1024, y: CGFloat(size) / 1024)
    let inset: CGFloat = mac ? 72 : 0
    let rect = CGRect(x: inset, y: inset, width: 1024 - inset * 2, height: 1024 - inset * 2)
    if mac {
        context.addPath(CGPath(roundedRect: rect, cornerWidth: 190, cornerHeight: 190, transform: nil))
        context.clip()
    }
    context.draw(artwork, in: rect)
    guard let image = context.makeImage(),
          let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
        throw CocoaError(.fileWriteUnknown)
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { throw CocoaError(.fileWriteUnknown) }
}
for platform in ["macOS", "iOS"] {
    let catalog = root.appendingPathComponent("assets/\(platform).xcassets")
    let folder = catalog.appendingPathComponent("AppIcon.appiconset")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    let info: [String: Any] = ["version": 1, "author": "xcode"]
    try JSONSerialization.data(withJSONObject: ["info": info], options: [.prettyPrinted, .sortedKeys])
        .write(to: catalog.appendingPathComponent("Contents.json"))
    var images: [[String: Any]] = []
    if platform == "macOS" {
        for size in [16, 32, 128, 256, 512] {
            for scale in [1, 2] {
                let filename = "icon_\(size)x\(size)@\(scale)x.png"
                try render(size: size * scale, mac: true, to: folder.appendingPathComponent(filename))
                images.append(["idiom": "mac", "size": "\(size)x\(size)", "scale": "\(scale)x", "filename": filename])
            }
        }
    } else {
        try render(size: 1024, mac: false, to: folder.appendingPathComponent("icon.png"))
        images.append(["idiom": "universal", "platform": "ios", "size": "1024x1024", "filename": "icon.png"])
    }
    try JSONSerialization.data(withJSONObject: ["images": images, "info": info], options: [.prettyPrinted, .sortedKeys])
        .write(to: folder.appendingPathComponent("Contents.json"))
}
print("Generated Mac and iPhone app icons.")
