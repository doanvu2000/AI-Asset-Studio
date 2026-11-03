"""Swift runtime shared by every iOS export that carries a 2D FX kind (SwiftUI Canvas + TimelineView, iOS 15+): identical
text for every asset, so a pack merge keeps one copy. Raw string: Swift's `\\(` and backslashes pass through untouched."""
from __future__ import annotations

from studio_core.export.ios_swift import GEN_HEAD

FX_FILE = "Sources/AssetStudioFx.swift"
FX_DIR = "Resources/AssetStudioFx"

_BODY = r"""import ImageIO
import SwiftUI

// Resources/AssetStudioFx is added to Xcode as a folder reference, so sub-paths survive (groups flatten the bundle).
enum StudioAssets {
    static let folder = "AssetStudioFx"
    private static let cache = NSCache<NSString, CGImage>()

    static func url(_ path: String, bundle: Bundle = .main) -> URL? {
        bundle.resourceURL?.appendingPathComponent(folder).appendingPathComponent(path)
    }

    static func data(_ path: String, bundle: Bundle = .main) -> Data? {
        url(path, bundle: bundle).flatMap { try? Data(contentsOf: $0) }
    }

    static func decode<T: Decodable>(_ type: T.Type, _ path: String, bundle: Bundle = .main) -> T? {
        data(path, bundle: bundle).flatMap { try? JSONDecoder().decode(T.self, from: $0) }
    }

    static func cgImage(_ path: String, bundle: Bundle = .main) -> CGImage? {
        if let hit = cache.object(forKey: path as NSString) { return hit }
        guard let url = url(path, bundle: bundle),
              let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return nil }
        cache.setObject(image, forKey: path as NSString)
        return image
    }
}

extension Color {
    init(studioHex hex: UInt32) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255, opacity: 1)
    }
}

private func fxImage(_ image: CGImage, pixel: Bool) -> Image {
    Image(decorative: image, scale: 1).interpolation(pixel ? .none : .medium)
}

final class StudioSheet {
    let image: CGImage
    let size: Int
    let pixel: Bool
    private var cache: [Int: Image] = [:]

    init(_ image: CGImage, tile: Int, pixel: Bool) {
        self.image = image
        self.size = max(tile, 1)
        self.pixel = pixel
    }

    func tile(col: Int, row: Int) -> Image? {
        let key = (row << 20) | col
        if let hit = cache[key] { return hit }
        guard col >= 0, row >= 0,
              let part = image.cropping(to: CGRect(x: col * size, y: row * size, width: size, height: size)) else { return nil }
        let tile = fxImage(part, pixel: pixel)
        cache[key] = tile
        return tile
    }
}

private func drawTiles(_ context: inout GraphicsContext, _ canvas: CGSize, columns: Int, rows: Int,
                       tile: (Int, Int) -> Image?) {
    let side = min(canvas.width / CGFloat(max(columns, 1)), canvas.height / CGFloat(max(rows, 1)))
    for y in 0..<rows {
        for x in 0..<columns {
            guard let image = tile(x, y) else { continue }
            let left = (CGFloat(x) * side).rounded(.down)
            let top = (CGFloat(y) * side).rounded(.down)
            let rect = CGRect(x: left, y: top, width: (CGFloat(x + 1) * side).rounded(.up) - left,
                              height: (CGFloat(y + 1) * side).rounded(.up) - top)
            context.draw(image, in: rect)
        }
    }
}

private struct TileAnimationDoc: Decodable {
    struct Layout: Decodable { let frame_offset: [Int] }
    let sheet: String
    let tile_size: Int
    let frames: Int
    let fps: Double
    let layout: Layout
    let pixel: Bool?
}

final class TileAnimation {
    let sheet: StudioSheet
    let frames: Int
    let fps: Double
    let offsetX: Int
    let offsetY: Int

    init(sheet: StudioSheet, frames: Int, fps: Double, offsetX: Int, offsetY: Int) {
        self.sheet = sheet
        self.frames = frames
        self.fps = fps
        self.offsetX = offsetX
        self.offsetY = offsetY
    }

    static func load(_ name: String, bundle: Bundle = .main) -> TileAnimation? {
        guard let doc = StudioAssets.decode(TileAnimationDoc.self, "tiles/\(name)_tile_anim.json", bundle: bundle),
              doc.layout.frame_offset.count >= 2,
              let image = StudioAssets.cgImage("tiles/\(doc.sheet)", bundle: bundle) else { return nil }
        return TileAnimation(sheet: StudioSheet(image, tile: doc.tile_size, pixel: doc.pixel ?? false), frames: doc.frames,
                             fps: doc.fps, offsetX: doc.layout.frame_offset[0], offsetY: doc.layout.frame_offset[1])
    }

    func frame(at seconds: Double) -> Int {
        guard frames > 0 else { return 0 }
        return Int(max(seconds, 0) * fps) % frames
    }
}

struct TileAnimationImage: View {
    let anim: TileAnimation
    let col: Int
    let row: Int
    let playing: Bool

    init(_ anim: TileAnimation, col: Int = 0, row: Int = 0, playing: Bool = true) {
        self.anim = anim
        self.col = col
        self.row = row
        self.playing = playing
    }

    var body: some View {
        TimelineView(.animation(paused: !playing)) { timeline in
            Canvas { context, size in
                let frame = anim.frame(at: timeline.date.timeIntervalSinceReferenceDate)
                if let tile = anim.sheet.tile(col: col + frame * anim.offsetX, row: row + frame * anim.offsetY) {
                    context.draw(tile, in: CGRect(origin: .zero, size: size))
                }
            }
        }
    }
}

private struct AutotileDoc: Decodable {
    struct Entry: Decodable {
        let mask: Int?
        let col: Int
        let row: Int
    }
    let tile_size: Int
    let atlas: String
    let tiles: [Entry]
    let pixel: Bool?
}

final class AutotileSet {
    let sheet: StudioSheet
    private let byMask: [Int: (col: Int, row: Int)]
    private let fill: (col: Int, row: Int)

    private static let dx = [0, 1, 1, 1, 0, -1, -1, -1]
    private static let dy = [-1, -1, 0, 1, 1, 1, 0, -1]
    private static let corners = [[1, 0, 2], [3, 4, 2], [5, 4, 6], [7, 0, 6]]

    private init(sheet: StudioSheet, byMask: [Int: (col: Int, row: Int)], fill: (col: Int, row: Int)) {
        self.sheet = sheet
        self.byMask = byMask
        self.fill = fill
    }

    static func load(_ name: String, bundle: Bundle = .main) -> AutotileSet? {
        guard let doc = StudioAssets.decode(AutotileDoc.self, "tiles/\(name)_autotile.json", bundle: bundle),
              let image = StudioAssets.cgImage("tiles/\(doc.atlas)", bundle: bundle),
              let first = doc.tiles.first else { return nil }
        var byMask: [Int: (col: Int, row: Int)] = [:]
        var fill = (col: first.col, row: first.row)
        for entry in doc.tiles {
            if let mask = entry.mask { byMask[mask] = (entry.col, entry.row) } else { fill = (entry.col, entry.row) }
        }
        return AutotileSet(sheet: StudioSheet(image, tile: doc.tile_size, pixel: doc.pixel ?? false), byMask: byMask,
                           fill: fill)
    }

    func image(mask: Int) -> Image? {
        let at = byMask[mask] ?? fill
        return sheet.tile(col: at.col, row: at.row)
    }

    func image(cells: [[Bool]], x: Int, y: Int) -> Image? {
        guard cells[y][x] else { return sheet.tile(col: fill.col, row: fill.row) }
        return image(mask: Self.maskAt(cells, x, y))
    }

    static func maskAt(_ cells: [[Bool]], _ x: Int, _ y: Int) -> Int {
        var mask = 0
        for i in 0..<8 {
            let nx = x + dx[i]
            let ny = y + dy[i]
            if ny >= 0, ny < cells.count, nx >= 0, nx < cells[ny].count, cells[ny][nx] { mask |= 1 << i }
        }
        for c in corners where mask & (1 << c[1]) == 0 || mask & (1 << c[2]) == 0 {
            mask &= ~(1 << c[0])
        }
        return mask
    }
}

struct AutotileMap: View {
    let tiles: AutotileSet
    let cells: [[Bool]]

    init(_ tiles: AutotileSet, cells: [[Bool]]) {
        self.tiles = tiles
        self.cells = cells
    }

    var body: some View {
        Canvas { context, size in
            let columns = cells.map { $0.count }.max() ?? 1
            drawTiles(&context, size, columns: columns, rows: cells.count) { x, y in
                y < cells.count && x < cells[y].count ? tiles.image(cells: cells, x: x, y: y) : nil
            }
        }
    }
}

private struct TileMapDoc: Decodable {
    struct Animation: Decodable {
        let frames: Int
        let fps: Double
        let step: Int
    }
    let tile: Int
    let image: String
    let cols: Int
    let pixel: Bool?
    let animation: Animation?
    let tiles: [[Int]]
}

final class TileMap {
    let sheet: StudioSheet
    let cols: Int
    let tiles: [[Int]]
    let frames: Int
    let fps: Double
    let step: Int

    private init(sheet: StudioSheet, doc: TileMapDoc) {
        self.sheet = sheet
        self.cols = max(doc.cols, 1)
        self.tiles = doc.tiles
        self.frames = max(doc.animation?.frames ?? 1, 1)
        self.fps = doc.animation?.fps ?? 1
        self.step = doc.animation?.step ?? 0
    }

    static func load(_ name: String, bundle: Bundle = .main) -> TileMap? {
        guard let doc = StudioAssets.decode(TileMapDoc.self, "tiles/\(name)_map.json", bundle: bundle),
              let image = StudioAssets.cgImage("tiles/\(doc.image)", bundle: bundle) else { return nil }
        return TileMap(sheet: StudioSheet(image, tile: doc.tile, pixel: doc.pixel ?? false), doc: doc)
    }
}

struct TileMapView: View {
    let map: TileMap
    let playing: Bool

    init(_ map: TileMap, playing: Bool = true) {
        self.map = map
        self.playing = playing
    }

    var body: some View {
        TimelineView(.animation(paused: !(playing && map.frames > 1))) { timeline in
            Canvas { context, size in
                let frame = Int(max(timeline.date.timeIntervalSinceReferenceDate, 0) * map.fps) % map.frames
                let columns = map.tiles.map { $0.count }.max() ?? 1
                drawTiles(&context, size, columns: columns, rows: map.tiles.count) { x, y in
                    guard x < map.tiles[y].count else { return nil }
                    let id = map.tiles[y][x] + frame * map.step
                    return map.sheet.tile(col: id % map.cols, row: id / map.cols)
                }
            }
        }
    }
}

struct VfxColorStop: Decodable {
    let t: Double
    let r: Double
    let g: Double
    let b: Double
    let a: Double

    init(from decoder: Decoder) throws {
        var list = try decoder.unkeyedContainer()
        t = try list.decode(Double.self)
        let hex = try list.decode(String.self).replacingOccurrences(of: "#", with: "")
        guard hex.count >= 6, let value = UInt64(hex.prefix(8), radix: 16) else {
            throw DecodingError.dataCorruptedError(in: list, debugDescription: "bad colour \(hex)")
        }
        let long = hex.count >= 8
        let packed = long ? value : (value << 8) | 0xFF
        r = Double((packed >> 24) & 0xFF)
        g = Double((packed >> 16) & 0xFF)
        b = Double((packed >> 8) & 0xFF)
        a = Double(packed & 0xFF)
    }
}

struct VfxShape: Decodable {
    let type: String?
    let radius: Double?
    let thickness: Double?
    let angle: Double?
}

struct VfxEmitter: Decodable {
    let texture: String
    let blend: String
    let delay: Double
    let burst: Double
    let rate: Double
    let lifetime: [Double]
    let speed: [Double]
    let shape: VfxShape
    let gravity: Double
    let drag: Double
    let size: [Double]
    let size_curve: [[Double]]
    let rotation: [Double]
    let spin: [Double]
    let align: String
    let colors: [VfxColorStop]
    let box: [Double]?
    let direction: [Double]?
    let spread: Double?
}

struct VfxPreset: Decodable {
    let name: String
    let loop: Bool
    let duration: Double
    let emitters: [VfxEmitter]

    var span: Double {
        emitters.reduce(0) { max($0, $1.delay + ($1.burst > 0 ? 0 : duration) + $1.lifetime[1]) }
    }
}

struct VfxMist: Decodable {
    let name: String
    let texture: String
    let tint: String
    let density: Double
    let tiles: [Double]
    let scroll: [Double]
}

private struct VfxTextureSpec: Decodable {
    let file: String
    let cols: Int?
    let rows: Int?
}

private struct VfxDoc: Decodable {
    let textures: [String: VfxTextureSpec]
    let presets: [VfxPreset]
    let mists: [VfxMist]?
    let ppu: Double?
    let area: [Double]?
}

final class VfxTexture {
    let image: CGImage
    let cols: Int
    let rows: Int
    private var cache: [Int: Image] = [:]

    init(_ image: CGImage, cols: Int, rows: Int) {
        self.image = image
        self.cols = max(cols, 1)
        self.rows = max(rows, 1)
    }

    var cellWidth: Int { max(image.width / cols, 1) }
    var cellHeight: Int { max(image.height / rows, 1) }

    func cell(_ index: Int) -> Image? {
        if let hit = cache[index] { return hit }
        let rect = CGRect(x: index % cols * cellWidth, y: index / cols * cellHeight, width: cellWidth, height: cellHeight)
        guard let part = image.cropping(to: rect) else { return nil }
        let cell = Image(decorative: part, scale: 1).interpolation(.medium)
        cache[index] = cell
        return cell
    }
}

struct StudioRandom {
    private var state: UInt64

    init(seed: Int?) {
        state = seed.map { UInt64(bitPattern: Int64($0)) &+ 0x9E37_79B9_7F4A_7C15 } ?? UInt64.random(in: 1...UInt64.max)
    }

    mutating func next() -> Double {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        z ^= z >> 31
        return Double(z >> 11) / 9_007_199_254_740_992.0
    }
}

private struct FxParticle {
    var x: Double
    var y: Double
    var vx: Double
    var vy: Double
    let life: Double
    var rot: Double
    let spin: Double
    let size: Double
    let frame: Int
    var age = 0.0
}

private final class EmitterState {
    var particles: [FxParticle] = []
    var carry = 0.0
    var lastBurst = -1e9
    var burstDone = false
}

final class VfxSimulation {
    private let preset: VfxPreset
    private let textures: [String: VfxTexture]
    private let unit: Double
    private var random: StudioRandom
    private let states: [EmitterState]
    private var time = 0.0
    private var lastDate: Date?
    private(set) var finished = false

    init(preset: VfxPreset, textures: [String: VfxTexture], unit: Double, seed: Int? = nil) {
        self.preset = preset
        self.textures = textures
        self.unit = unit
        self.random = StudioRandom(seed: seed)
        self.states = preset.emitters.map { _ in EmitterState() }
    }

    func prewarm(seconds: Double = 0) {
        let total = seconds > 0 ? seconds : preset.emitters.reduce(0) { max($0, $1.lifetime[1]) }
        var t = 0.0
        while t < total {
            update(1.0 / 30)
            t += 1.0 / 30
        }
    }

    func advance(to date: Date) {
        defer { lastDate = date }
        guard let previous = lastDate else { return }
        update(min(max(date.timeIntervalSince(previous), 0), 0.1))
    }

    func update(_ dt: Double) {
        if finished || dt <= 0 { return }
        time += dt
        for i in states.indices { step(preset.emitters[i], states[i], dt) }
        if !preset.loop && time > preset.span && states.allSatisfy({ $0.particles.isEmpty }) { finished = true }
    }

    private func between(_ range: [Double]) -> Double { range[0] + (range[1] - range[0]) * random.next() }

    private func step(_ e: VfxEmitter, _ s: EmitterState, _ dt: Double) {
        let local = time - e.delay
        if local >= 0 {
            if e.burst > 0 {
                if !s.burstDone || (preset.loop && local - s.lastBurst >= e.lifetime[1]) {
                    for _ in 0..<Int(e.burst) { spawn(e, s) }
                    s.burstDone = true
                    s.lastBurst = local
                }
            } else if e.rate > 0 && (preset.loop || local - dt < preset.duration) {
                s.carry += e.rate * dt
                while s.carry >= 1 {
                    s.carry -= 1
                    spawn(e, s)
                }
            }
        }
        let keep = exp(-e.drag * dt)
        var i = s.particles.count - 1
        while i >= 0 {
            s.particles[i].age += dt
            if s.particles[i].age >= s.particles[i].life {
                s.particles.swapAt(i, s.particles.count - 1)
                s.particles.removeLast()
                i -= 1
                continue
            }
            s.particles[i].vy += e.gravity * unit * dt
            s.particles[i].vx *= keep
            s.particles[i].vy *= keep
            s.particles[i].x += s.particles[i].vx * dt
            s.particles[i].y += s.particles[i].vy * dt
            s.particles[i].rot += s.particles[i].spin * dt
            i -= 1
        }
    }

    private func spawn(_ e: VfxEmitter, _ s: EmitterState) {
        guard let tex = textures[e.texture] else { return }
        var x = 0.0
        var y = 0.0
        if let b = e.box, b.count >= 4 {
            x = b[0] + (random.next() * 2 - 1) * b[2]
            y = b[1] + (random.next() * 2 - 1) * b[3]
        }
        let radius = (e.shape.radius ?? 0) * unit
        var angle = random.next() * 2 * Double.pi
        switch e.shape.type ?? "" {
        case "sphere":
            let a = random.next() * 2 * Double.pi
            let d = radius * random.next().squareRoot()
            x += cos(a) * d
            y += sin(a) * d
        case "circle":
            let inner = radius * (1 - (e.shape.thickness ?? 1))
            let a = random.next() * 2 * Double.pi
            let d = inner + (radius - inner) * random.next().squareRoot()
            x += cos(a) * d
            y += sin(a) * d
        case "cone":
            x += (random.next() * 2 - 1) * radius
            angle = -Double.pi / 2 + (random.next() * 2 - 1) * (e.shape.angle ?? 0) * Double.pi / 180
        default:
            break
        }
        if let d = e.direction, d.count >= 2 {
            angle = atan2(d[1], d[0]) + (random.next() * 2 - 1) * (e.spread ?? 0) * Double.pi / 180
        }
        let speed = between(e.speed) * unit
        let cells = tex.cols * tex.rows
        s.particles.append(FxParticle(
            x: x, y: y, vx: cos(angle) * speed, vy: sin(angle) * speed, life: max(between(e.lifetime), 0.001),
            rot: between(e.rotation), spin: between(e.spin), size: between(e.size) * unit,
            frame: min(Int(random.next() * Double(cells)), cells - 1)))
    }

    func draw(into context: inout GraphicsContext, origin: CGPoint) {
        for (i, e) in preset.emitters.enumerated() {
            guard let tex = textures[e.texture] else { continue }
            for p in states[i].particles {
                let t = p.age / p.life
                let w = p.size * Self.curve(e.size_curve, t)
                guard w > 0, let cell = tex.cell(p.frame) else { continue }
                let h = w * Double(tex.cellHeight) / Double(tex.cellWidth)
                let rotation = e.align == "velocity" ? atan2(p.vx, -p.vy) : p.rot * Double.pi / 180
                let tint = Self.color(e.colors, t)
                var layer = context
                layer.translateBy(x: origin.x + p.x, y: origin.y + p.y)
                layer.rotate(by: .radians(rotation))
                layer.opacity = tint.a / 255
                layer.blendMode = e.blend == "add" ? .plusLighter : .normal
                layer.addFilter(.colorMultiply(Color(.sRGB, red: tint.r / 255, green: tint.g / 255, blue: tint.b / 255,
                                                     opacity: 1)))
                layer.draw(cell, in: CGRect(x: -w / 2, y: -h / 2, width: w, height: h))
            }
        }
    }

    private static func curve(_ points: [[Double]], _ t: Double) -> Double {
        guard let first = points.first, let last = points.last else { return 1 }
        if t <= first[0] { return first[1] }
        for i in 1..<max(points.count, 1) where t <= points[i][0] {
            let a = points[i - 1]
            let b = points[i]
            let span = b[0] - a[0]
            return span <= 0 ? b[1] : a[1] + (b[1] - a[1]) * (t - a[0]) / span
        }
        return last[1]
    }

    private static func color(_ stops: [VfxColorStop], _ t: Double) -> (r: Double, g: Double, b: Double, a: Double) {
        func rgba(_ s: VfxColorStop) -> (r: Double, g: Double, b: Double, a: Double) { (s.r, s.g, s.b, s.a) }
        guard let first = stops.first, let last = stops.last else { return (255, 255, 255, 255) }
        if t <= first.t { return rgba(first) }
        for i in 1..<max(stops.count, 1) where t <= stops[i].t {
            let a = stops[i - 1]
            let b = stops[i]
            let k = (t - a.t) / max(b.t - a.t, 1e-6)
            return (a.r + (b.r - a.r) * k, a.g + (b.g - a.g) * k, a.b + (b.b - a.b) * k, a.a + (b.a - a.a) * k)
        }
        return rgba(last)
    }
}

final class VfxLibrary {
    let presets: [String: VfxPreset]
    let textures: [String: VfxTexture]
    let unit: Double
    let area: [Double]?
    let mists: [String: VfxMist]

    private init(presets: [String: VfxPreset], textures: [String: VfxTexture], unit: Double, area: [Double]?,
                 mists: [String: VfxMist]) {
        self.presets = presets
        self.textures = textures
        self.unit = unit
        self.area = area
        self.mists = mists
    }

    static func load(_ name: String, bundle: Bundle = .main) -> VfxLibrary? {
        read(dir: "vfx", name: name, stem: "vfx", bundle: bundle)
    }

    static func loadWeather(_ name: String, bundle: Bundle = .main) -> VfxLibrary? {
        read(dir: "weather", name: name, stem: "weather", bundle: bundle)
    }

    private static func read(dir: String, name: String, stem: String, bundle: Bundle) -> VfxLibrary? {
        let base = "\(dir)/\(name)"
        guard let doc = StudioAssets.decode(VfxDoc.self, "\(base)/\(name)_\(stem).json", bundle: bundle) else { return nil }
        var textures: [String: VfxTexture] = [:]
        for (key, spec) in doc.textures {
            guard let image = StudioAssets.cgImage("\(base)/\(spec.file)", bundle: bundle) else { return nil }
            textures[key] = VfxTexture(image, cols: spec.cols ?? 1, rows: spec.rows ?? 1)
        }
        return VfxLibrary(presets: Dictionary(doc.presets.map { ($0.name, $0) }, uniquingKeysWith: { a, _ in a }),
                          textures: textures, unit: doc.ppu ?? 1, area: doc.area,
                          mists: Dictionary((doc.mists ?? []).map { ($0.name, $0) }, uniquingKeysWith: { a, _ in a }))
    }

    func simulate(_ name: String, seed: Int? = nil, prewarm: Bool = false) -> VfxSimulation? {
        guard let preset = presets[name] else { return nil }
        let sim = VfxSimulation(preset: preset, textures: textures, unit: unit, seed: seed)
        if prewarm { sim.prewarm() }
        return sim
    }
}

private struct VfxKey: Hashable {
    let preset: String
    let seed: Int?
}

private final class SimBox: ObservableObject {
    let sim: VfxSimulation?

    init(_ make: () -> VfxSimulation?) { sim = make() }
}

private struct VfxRun: View {
    let origin: CGPoint?
    let fitArea: [Double]?
    let onFinished: (() -> Void)?
    @StateObject private var box: SimBox
    @State private var done = false

    init(make: @escaping () -> VfxSimulation?, origin: CGPoint?, fitArea: [Double]?, onFinished: (() -> Void)?) {
        _box = StateObject(wrappedValue: SimBox(make))
        self.origin = origin
        self.fitArea = fitArea
        self.onFinished = onFinished
    }

    var body: some View {
        TimelineView(.animation(paused: done)) { timeline in
            Canvas { context, size in
                guard let sim = box.sim else { return }
                sim.advance(to: timeline.date)
                var layer = context
                var at = origin ?? CGPoint(x: size.width / 2, y: size.height / 2)
                if let area = fitArea, area.count >= 2, area[0] > 0 {
                    let k = size.width / CGFloat(area[0])
                    layer.scaleBy(x: k, y: k)
                    at = .zero
                }
                sim.draw(into: &layer, origin: at)
            }
        }
        .task {
            guard let sim = box.sim, onFinished != nil else { return }
            while !sim.finished {
                if (try? await Task.sleep(nanoseconds: 50_000_000)) == nil { return }
            }
            done = true
            onFinished?()
        }
    }
}

struct VfxView: View {
    let library: VfxLibrary
    let preset: String
    var origin: CGPoint?
    var seed: Int?
    var prewarm: Bool
    var onFinished: (() -> Void)?

    init(_ library: VfxLibrary, _ preset: String, origin: CGPoint? = nil, seed: Int? = nil, prewarm: Bool = false,
         onFinished: (() -> Void)? = nil) {
        self.library = library
        self.preset = preset
        self.origin = origin
        self.seed = seed
        self.prewarm = prewarm
        self.onFinished = onFinished
    }

    var body: some View {
        VfxRun(make: { library.simulate(preset, seed: seed, prewarm: prewarm) }, origin: origin, fitArea: nil,
               onFinished: onFinished)
            .id(VfxKey(preset: preset, seed: seed))
    }
}

struct WeatherView: View {
    let library: VfxLibrary
    let preset: String
    var seed: Int?

    init(_ library: VfxLibrary, _ preset: String, seed: Int? = nil) {
        self.library = library
        self.preset = preset
        self.seed = seed
    }

    var body: some View {
        VfxRun(make: { library.simulate(preset, seed: seed, prewarm: true) }, origin: .zero, fitArea: library.area,
               onFinished: nil)
            .id(VfxKey(preset: preset, seed: seed))
    }
}

struct MistView: View {
    let library: VfxLibrary
    let layer: String
    let playing: Bool

    init(_ library: VfxLibrary, _ layer: String, playing: Bool = true) {
        self.library = library
        self.layer = layer
        self.playing = playing
    }

    var body: some View {
        if let spec = library.mists[layer], let tex = library.textures[spec.texture], let cell = tex.cell(0),
           spec.tiles.count >= 2, spec.scroll.count >= 2, spec.tiles[0] > 0, spec.tiles[1] > 0 {
            TimelineView(.animation(paused: !playing)) { timeline in
                Canvas { context, size in
                    let seconds = max(timeline.date.timeIntervalSinceReferenceDate, 0)
                    let tw = size.width / CGFloat(spec.tiles[0])
                    let th = size.height / CGFloat(spec.tiles[1])
                    let ox = CGFloat((seconds * spec.scroll[0]).truncatingRemainder(dividingBy: 1)) * tw
                    let oy = CGFloat((seconds * spec.scroll[1]).truncatingRemainder(dividingBy: 1)) * th
                    var tinted = context
                    tinted.opacity = min(1, spec.density)
                    let hex = spec.tint.replacingOccurrences(of: "#", with: "")
                    tinted.addFilter(.colorMultiply(Color(studioHex: UInt32(hex.prefix(6), radix: 16) ?? 0xFFFFFF)))
                    for y in -1...Int(spec.tiles[1].rounded(.up)) {
                        for x in -1...Int(spec.tiles[0].rounded(.up)) {
                            tinted.draw(cell, in: CGRect(x: CGFloat(x) * tw + ox, y: CGFloat(y) * th + oy, width: tw, height: th))
                        }
                    }
                }
            }
        }
    }
}

struct MinimapPoi {
    let kind: String
    let x: CGFloat
    let y: CGFloat

    init(_ kind: String, _ x: CGFloat, _ y: CGFloat) {
        self.kind = kind
        self.x = x
        self.y = y
    }
}

struct MinimapView<Marker: View>: View {
    let asset: String
    let pois: [MinimapPoi]
    let markerSize: CGFloat
    let marker: (MinimapPoi) -> Marker

    init(asset: String, pois: [MinimapPoi], markerSize: CGFloat = 12, @ViewBuilder marker: @escaping (MinimapPoi) -> Marker) {
        self.asset = asset
        self.pois = pois
        self.markerSize = markerSize
        self.marker = marker
    }

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                if let image = StudioAssets.cgImage(asset) {
                    Image(decorative: image, scale: 1).resizable().frame(width: geo.size.width, height: geo.size.height)
                }
                ForEach(Array(pois.enumerated()), id: \.offset) { _, poi in
                    marker(poi)
                        .frame(width: markerSize, height: markerSize)
                        .position(x: geo.size.width * poi.x, y: geo.size.height * poi.y)
                }
            }
        }
    }
}

struct CardEntry {
    let id: String
    let name: String
    let front: String
    let art: String?

    init(_ id: String, _ name: String, _ front: String, _ art: String?) {
        self.id = id
        self.name = name
        self.front = front
        self.art = art
    }
}

struct CardFace: View {
    let asset: String

    init(_ asset: String) { self.asset = asset }

    var body: some View {
        if let image = StudioAssets.cgImage(asset) {
            Image(decorative: image, scale: 1).resizable().scaledToFit()
        }
    }
}

struct NineSliceSpec {
    let asset: String
    let left: CGFloat
    let top: CGFloat
    let right: CGFloat
    let bottom: CGFloat

    init(_ asset: String, left: CGFloat, top: CGFloat, right: CGFloat, bottom: CGFloat) {
        self.asset = asset
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom
    }
}

struct StudioScreen<Content: View>: View {
    let refWidth: CGFloat
    let background: String?
    let content: (CGFloat) -> Content

    init(refWidth: CGFloat, background: String? = nil, @ViewBuilder content: @escaping (CGFloat) -> Content) {
        self.refWidth = refWidth
        self.background = background
        self.content = content
    }

    var body: some View {
        ZStack {
            if let path = background, let image = StudioAssets.cgImage(path) {
                Color.clear.overlay(Image(decorative: image, scale: 1).resizable().scaledToFill()).clipped().ignoresSafeArea()
            }
            GeometryReader { geo in
                ZStack(alignment: .topLeading) { content(geo.size.width / max(refWidth, 1)) }
                    .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
            }
        }
    }
}

struct StudioPlace<Content: View>: View {
    let left: CGFloat
    let top: CGFloat
    let width: CGFloat
    let height: CGFloat
    let content: Content

    init(_ left: CGFloat, _ top: CGFloat, _ width: CGFloat, _ height: CGFloat, @ViewBuilder content: () -> Content) {
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.content = content()
    }

    var body: some View {
        GeometryReader { geo in
            content
                .frame(width: geo.size.width * width, height: geo.size.height * height)
                .offset(x: geo.size.width * left, y: geo.size.height * top)
        }
    }
}

struct StudioSkin<Content: View>: View {
    let spec: NineSliceSpec?
    let scale: CGFloat
    let content: Content

    init(_ spec: NineSliceSpec?, _ scale: CGFloat, @ViewBuilder content: () -> Content) {
        self.spec = spec
        self.scale = scale
        self.content = content()
    }

    var body: some View {
        ZStack {
            if let spec = spec, let image = StudioAssets.cgImage(spec.asset) {
                let k = max(scale, 0.01)
                Image(decorative: image, scale: 1 / k)
                    .resizable(capInsets: EdgeInsets(top: spec.top * k, leading: spec.left * k, bottom: spec.bottom * k,
                                                     trailing: spec.right * k), resizingMode: .stretch)
                    .interpolation(.none)
            }
            content
        }
    }
}

private struct StudioPressStyle: ButtonStyle {
    let normal: NineSliceSpec?
    let pressed: NineSliceSpec?
    let scale: CGFloat

    func makeBody(configuration: Configuration) -> some View {
        StudioSkin(configuration.isPressed ? (pressed ?? normal) : normal, scale) { configuration.label }
    }
}

struct StudioButton<Content: View>: View {
    let normal: NineSliceSpec?
    let pressed: NineSliceSpec?
    let scale: CGFloat
    let onTap: () -> Void
    let content: Content

    init(normal: NineSliceSpec?, pressed: NineSliceSpec?, scale: CGFloat, onTap: @escaping () -> Void,
         @ViewBuilder content: () -> Content) {
        self.normal = normal
        self.pressed = pressed
        self.scale = scale
        self.onTap = onTap
        self.content = content()
    }

    var body: some View {
        Button(action: onTap) { content }
            .buttonStyle(StudioPressStyle(normal: normal, pressed: pressed, scale: scale))
    }
}

struct StudioText: View {
    let text: String
    let color: Color
    let size: CGFloat
    let align: String
    let scale: CGFloat

    init(_ text: String, color: Color, size: CGFloat, align: String, scale: CGFloat) {
        self.text = text
        self.color = color
        self.size = size
        self.align = align
        self.scale = scale
    }

    private var alignment: Alignment {
        switch align {
        case "left": return .leading
        case "right": return .trailing
        default: return .center
        }
    }

    var body: some View {
        Text(text)
            .font(.system(size: size * scale, weight: .bold))
            .foregroundColor(color)
            .lineLimit(1)
            .truncationMode(.tail)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: alignment)
    }
}

struct StudioImage: View {
    let asset: String

    init(_ asset: String) { self.asset = asset }

    var body: some View {
        if let image = StudioAssets.cgImage(asset) {
            Image(decorative: image, scale: 1).resizable().scaledToFit().frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}
"""

FX_SWIFT = GEN_HEAD + _BODY
