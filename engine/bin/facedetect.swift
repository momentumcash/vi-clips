// ClipForge face detector — Apple Vision (on-device, no model download).
// Usage: facedetect <frame.jpg> [<frame.jpg> ...]   → one JSON line per frame on stdout:
//   {"file": "...", "faces": [[x, y, w, h, confidence], ...]}   (normalized 0-1, origin TOP-left)
import Foundation
import Vision
import CoreGraphics
import ImageIO

func detect(_ path: String) -> [[Double]] {
    let url = URL(fileURLWithPath: path) as CFURL
    guard let src = CGImageSourceCreateWithURL(url, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return [] }
    let req = VNDetectFaceRectanglesRequest()
    let handler = VNImageRequestHandler(cgImage: img, options: [:])
    do { try handler.perform([req]) } catch { return [] }
    var out: [[Double]] = []
    for o in req.results ?? [] {
        let b = o.boundingBox
        out.append([Double(b.origin.x), Double(1.0 - b.origin.y - b.size.height), Double(b.size.width), Double(b.size.height), Double(o.confidence)])
    }
    return out
}

for path in CommandLine.arguments.dropFirst() {
    let faces = detect(path)
    let obj: [String: Any] = ["file": path, "faces": faces]
    if let d = try? JSONSerialization.data(withJSONObject: obj), let s = String(data: d, encoding: .utf8) {
        print(s)
        fflush(stdout)
    }
}
