"""Kotlin runtime shared by every Android export that carries a 2D FX kind: identical text for every asset, so a pack
merge keeps one copy. Raw string: Kotlin's `$` and backslashes pass through untouched."""
from __future__ import annotations

FX_FILE = "kotlin/AssetStudioFx.kt"

FX_KOTLIN = r"""package @PKG@

import android.content.Context
import android.graphics.BitmapFactory
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.BoxWithConstraintsScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.FloatState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random

object StudioAssets {
    suspend fun bitmap(context: Context, path: String): ImageBitmap = withContext(Dispatchers.IO) {
        context.assets.open(path).use { BitmapFactory.decodeStream(it).asImageBitmap() }
    }

    suspend fun json(context: Context, path: String): JSONObject = withContext(Dispatchers.IO) {
        JSONObject(context.assets.open(path).bufferedReader().use { it.readText() })
    }
}

@Composable
fun rememberStudioImage(path: String): ImageBitmap? {
    val context = LocalContext.current
    return produceState<ImageBitmap?>(null, path) { value = StudioAssets.bitmap(context, path) }.value
}

private fun JSONArray.doubles() = DoubleArray(length()) { getDouble(it) }

private fun JSONObject.pair(key: String) = getJSONArray(key).doubles()

private fun JSONObject.pairOrNull(key: String) = optJSONArray(key)?.doubles()

private fun filterFor(pixel: Boolean) = if (pixel) FilterQuality.None else FilterQuality.Low

@Composable
private fun rememberSeconds(playing: Boolean): FloatState {
    val seconds = remember { mutableFloatStateOf(0f) }
    LaunchedEffect(playing) {
        if (!playing) return@LaunchedEffect
        val start = withFrameNanos { it } - (seconds.floatValue * 1e9f).toLong()
        while (true) withFrameNanos { seconds.floatValue = (it - start) / 1e9f }
    }
    return seconds
}

class TileAnimation(
    val image: ImageBitmap,
    val tile: Int,
    val frames: Int,
    val fps: Float,
    val offsetX: Int,
    val offsetY: Int,
    val filter: FilterQuality,
) {
    fun frameAt(seconds: Float) = (seconds * fps).toInt().mod(max(frames, 1))

    fun src(col: Int, row: Int, frame: Int) = IntOffset((col + frame * offsetX) * tile, (row + frame * offsetY) * tile)
}

suspend fun loadTileAnimation(context: Context, name: String): TileAnimation {
    val doc = StudioAssets.json(context, "tiles/${name}_tile_anim.json")
    val offset = doc.getJSONObject("layout").getJSONArray("frame_offset")
    return TileAnimation(
        StudioAssets.bitmap(context, "tiles/${doc.getString("sheet")}"),
        doc.getInt("tile_size"),
        doc.getInt("frames"),
        doc.getDouble("fps").toFloat(),
        offset.getInt(0),
        offset.getInt(1),
        filterFor(doc.optBoolean("pixel")),
    )
}

@Composable
fun TileAnimationImage(
    anim: TileAnimation,
    modifier: Modifier = Modifier,
    col: Int = 0,
    row: Int = 0,
    playing: Boolean = true,
) {
    val seconds = rememberSeconds(playing)
    Canvas(modifier) {
        drawImage(
            anim.image,
            srcOffset = anim.src(col, row, anim.frameAt(seconds.floatValue)),
            srcSize = IntSize(anim.tile, anim.tile),
            dstSize = IntSize(size.width.roundToInt(), size.height.roundToInt()),
            filterQuality = anim.filter,
        )
    }
}

class AutotileSet(
    val image: ImageBitmap,
    val tile: Int,
    private val byMask: Map<Int, IntOffset>,
    private val fill: IntOffset,
    val filter: FilterQuality,
) {
    fun srcFor(mask: Int) = byMask[mask] ?: fill

    fun srcAt(cells: List<List<Boolean>>, x: Int, y: Int) = if (cells[y][x]) srcFor(maskAt(cells, x, y)) else fill

    companion object {
        private val DX = intArrayOf(0, 1, 1, 1, 0, -1, -1, -1)
        private val DY = intArrayOf(-1, -1, 0, 1, 1, 1, 0, -1)
        private val CORNERS = arrayOf(intArrayOf(1, 0, 2), intArrayOf(3, 4, 2), intArrayOf(5, 4, 6), intArrayOf(7, 0, 6))

        suspend fun load(context: Context, name: String): AutotileSet {
            val doc = StudioAssets.json(context, "tiles/${name}_autotile.json")
            val size = doc.getInt("tile_size")
            val byMask = HashMap<Int, IntOffset>()
            var fill: IntOffset? = null
            val tiles = doc.getJSONArray("tiles")
            for (i in 0 until tiles.length()) {
                val t = tiles.getJSONObject(i)
                val src = IntOffset(t.getInt("col") * size, t.getInt("row") * size)
                if (t.isNull("mask")) fill = src else byMask[t.getInt("mask")] = src
            }
            return AutotileSet(
                StudioAssets.bitmap(context, "tiles/${doc.getString("atlas")}"),
                size,
                byMask,
                fill ?: byMask.values.first(),
                filterFor(doc.optBoolean("pixel")),
            )
        }

        fun maskAt(cells: List<List<Boolean>>, x: Int, y: Int): Int {
            var mask = 0
            for (i in 0 until 8) {
                val nx = x + DX[i]
                val ny = y + DY[i]
                if (ny in cells.indices && nx in cells[ny].indices && cells[ny][nx]) mask = mask or (1 shl i)
            }
            for (c in CORNERS) {
                if (mask and (1 shl c[1]) == 0 || mask and (1 shl c[2]) == 0) mask = mask and (1 shl c[0]).inv()
            }
            return mask
        }
    }
}

@Composable
fun AutotileMap(tiles: AutotileSet, cells: List<List<Boolean>>, modifier: Modifier = Modifier) {
    Canvas(modifier) {
        val t = tiles.tile
        val sc = min(size.width / (cells.maxOfOrNull { it.size } ?: 1) / t, size.height / max(cells.size, 1) / t)
        withTransform({ scale(sc, sc, Offset.Zero) }) {
            for (y in cells.indices) for (x in cells[y].indices) {
                drawImage(
                    tiles.image,
                    srcOffset = tiles.srcAt(cells, x, y),
                    srcSize = IntSize(t, t),
                    dstOffset = IntOffset(x * t, y * t),
                    dstSize = IntSize(t, t),
                    filterQuality = tiles.filter,
                )
            }
        }
    }
}

class TileMap(
    val image: ImageBitmap,
    val tile: Int,
    val cols: Int,
    val tiles: List<IntArray>,
    val frames: Int,
    val fps: Float,
    val step: Int,
    val filter: FilterQuality,
)

suspend fun loadTileMap(context: Context, name: String): TileMap {
    val doc = StudioAssets.json(context, "tiles/${name}_map.json")
    val grid = doc.getJSONArray("tiles")
    val anim = doc.optJSONObject("animation")
    return TileMap(
        StudioAssets.bitmap(context, "tiles/${doc.getString("image")}"),
        doc.getInt("tile"),
        doc.getInt("cols"),
        List(grid.length()) { y -> grid.getJSONArray(y).let { row -> IntArray(row.length()) { row.getInt(it) } } },
        anim?.getInt("frames") ?: 1,
        (anim?.getDouble("fps") ?: 1.0).toFloat(),
        anim?.getInt("step") ?: 0,
        filterFor(doc.optBoolean("pixel")),
    )
}

@Composable
fun TileMapView(map: TileMap, modifier: Modifier = Modifier, playing: Boolean = true) {
    val seconds = rememberSeconds(playing && map.frames > 1)
    Canvas(modifier) {
        val t = map.tile
        val frame = (seconds.floatValue * map.fps).toInt().mod(max(map.frames, 1))
        val sc = min(
            size.width / (map.tiles.maxOfOrNull { it.size } ?: 1) / t,
            size.height / max(map.tiles.size, 1) / t,
        )
        withTransform({ scale(sc, sc, Offset.Zero) }) {
            for (y in map.tiles.indices) for (x in map.tiles[y].indices) {
                val id = map.tiles[y][x] + frame * map.step
                drawImage(
                    map.image,
                    srcOffset = IntOffset(id % map.cols * t, id / map.cols * t),
                    srcSize = IntSize(t, t),
                    dstOffset = IntOffset(x * t, y * t),
                    dstSize = IntSize(t, t),
                    filterQuality = map.filter,
                )
            }
        }
    }
}

class VfxTexture(val image: ImageBitmap, val cols: Int, val rows: Int) {
    val cellWidth get() = image.width / cols
    val cellHeight get() = image.height / rows
}

class VfxEmitter(j: JSONObject) {
    val texture: String = j.getString("texture")
    val additive = j.getString("blend") == "add"
    val delay = j.getDouble("delay")
    val burst = j.getInt("burst")
    val rate = j.getDouble("rate")
    val life = j.pair("lifetime")
    val speed = j.pair("speed")
    val shape: JSONObject = j.getJSONObject("shape")
    val gravity = j.getDouble("gravity")
    val drag = j.getDouble("drag")
    val size = j.pair("size")
    val sizeCurve = j.getJSONArray("size_curve").let { a -> List(a.length()) { a.getJSONArray(it).doubles() } }
    val rotation = j.pair("rotation")
    val spin = j.pair("spin")
    val velocityAligned = j.getString("align") == "velocity"
    val colors = j.getJSONArray("colors").let { a -> List(a.length()) { stop(a.getJSONArray(it)) } }
    val box = j.pairOrNull("box")
    val direction = j.pairOrNull("direction")
    val spread = j.optDouble("spread", 0.0)

    private fun stop(p: JSONArray): DoubleArray {
        val h = p.getString(1).removePrefix("#")
        fun part(i: Int) = h.substring(i, i + 2).toInt(16).toDouble()
        return doubleArrayOf(p.getDouble(0), part(0), part(2), part(4), if (h.length > 6) part(6) else 255.0)
    }
}

class VfxPreset(j: JSONObject) {
    val name: String = j.getString("name")
    val loop = j.getBoolean("loop")
    val duration = j.getDouble("duration")
    val emitters = j.getJSONArray("emitters").let { a -> List(a.length()) { VfxEmitter(a.getJSONObject(it)) } }

    val span get() = emitters.fold(0.0) { m, e -> max(m, e.delay + (if (e.burst > 0) 0.0 else duration) + e.life[1]) }
}

private class Particle(
    var x: Double,
    var y: Double,
    var vx: Double,
    var vy: Double,
    val life: Double,
    var rot: Double,
    val spin: Double,
    val size: Double,
    val frame: Int,
) {
    var age = 0.0
}

private class EmitterState {
    val particles = ArrayList<Particle>()
    var carry = 0.0
    var lastBurst = -1e9
    var burstDone = false
}

class VfxSimulation(
    private val preset: VfxPreset,
    private val textures: Map<String, VfxTexture>,
    private val unit: Double,
    seed: Int? = null,
) {
    private val random = if (seed == null) Random.Default else Random(seed)
    private val states = preset.emitters.map { EmitterState() }
    private var time = 0.0
    var finished = false
        private set

    fun prewarm(seconds: Double = 0.0) {
        val total = if (seconds > 0) seconds else preset.emitters.fold(0.0) { m, e -> max(m, e.life[1]) }
        var t = 0.0
        while (t < total) {
            update(1 / 30.0)
            t += 1 / 30.0
        }
    }

    fun update(dt: Double) {
        if (finished) return
        time += dt
        for (i in states.indices) step(preset.emitters[i], states[i], dt)
        if (!preset.loop && time > preset.span && states.all { it.particles.isEmpty() }) finished = true
    }

    private fun between(r: DoubleArray) = r[0] + (r[1] - r[0]) * random.nextDouble()

    private fun step(e: VfxEmitter, s: EmitterState, dt: Double) {
        val local = time - e.delay
        if (local >= 0) {
            if (e.burst > 0) {
                if (!s.burstDone || (preset.loop && local - s.lastBurst >= e.life[1])) {
                    repeat(e.burst) { spawn(e, s) }
                    s.burstDone = true
                    s.lastBurst = local
                }
            } else if (e.rate > 0 && (preset.loop || local - dt < preset.duration)) {
                s.carry += e.rate * dt
                while (s.carry >= 1) {
                    s.carry -= 1
                    spawn(e, s)
                }
            }
        }
        val keep = exp(-e.drag * dt)
        val list = s.particles
        for (i in list.indices.reversed()) {
            val p = list[i]
            p.age += dt
            if (p.age >= p.life) {
                list[i] = list.last()
                list.removeAt(list.lastIndex)
                continue
            }
            p.vy += e.gravity * unit * dt
            p.vx *= keep
            p.vy *= keep
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.rot += p.spin * dt
        }
    }

    private fun spawn(e: VfxEmitter, s: EmitterState) {
        val tex = textures[e.texture] ?: return
        var x = 0.0
        var y = 0.0
        e.box?.let { b ->
            x = b[0] + (random.nextDouble() * 2 - 1) * b[2]
            y = b[1] + (random.nextDouble() * 2 - 1) * b[3]
        }
        val radius = e.shape.optDouble("radius", 0.0) * unit
        var angle = random.nextDouble() * 2 * PI
        when (e.shape.optString("type")) {
            "sphere" -> {
                val a = random.nextDouble() * 2 * PI
                val d = radius * sqrt(random.nextDouble())
                x += cos(a) * d
                y += sin(a) * d
            }
            "circle" -> {
                val inner = radius * (1 - e.shape.optDouble("thickness", 1.0))
                val a = random.nextDouble() * 2 * PI
                val d = inner + (radius - inner) * sqrt(random.nextDouble())
                x += cos(a) * d
                y += sin(a) * d
            }
            "cone" -> {
                x += (random.nextDouble() * 2 - 1) * radius
                angle = -PI / 2 + (random.nextDouble() * 2 - 1) * e.shape.optDouble("angle", 0.0) * PI / 180
            }
        }
        e.direction?.let { angle = atan2(it[1], it[0]) + (random.nextDouble() * 2 - 1) * e.spread * PI / 180 }
        val speed = between(e.speed) * unit
        s.particles.add(
            Particle(
                x, y, cos(angle) * speed, sin(angle) * speed, between(e.life), between(e.rotation), between(e.spin),
                between(e.size) * unit, random.nextInt(tex.cols * tex.rows),
            ),
        )
    }

    fun draw(scope: DrawScope, origin: Offset, filter: FilterQuality = FilterQuality.Low) {
        for (i in states.indices) {
            val e = preset.emitters[i]
            val tex = textures[e.texture] ?: continue
            val cw = tex.cellWidth
            val ch = tex.cellHeight
            for (p in states[i].particles) {
                val t = p.age / p.life
                val sc = (p.size * curve(e.sizeCurve, t) / cw).toFloat()
                if (sc <= 0f) continue
                val rot = if (e.velocityAligned) atan2(p.vx, -p.vy) * 180 / PI else p.rot
                scope.withTransform({
                    translate(origin.x + p.x.toFloat(), origin.y + p.y.toFloat())
                    rotate(rot.toFloat(), Offset.Zero)
                    scale(sc, sc, Offset.Zero)
                    translate(-cw / 2f, -ch / 2f)
                }) {
                    drawImage(
                        tex.image,
                        srcOffset = IntOffset(p.frame % tex.cols * cw, p.frame / tex.cols * ch),
                        srcSize = IntSize(cw, ch),
                        colorFilter = ColorFilter.tint(color(e.colors, t), BlendMode.Modulate),
                        blendMode = if (e.additive) BlendMode.Plus else BlendMode.SrcOver,
                        filterQuality = filter,
                    )
                }
            }
        }
    }

    private fun curve(pts: List<DoubleArray>, t: Double): Double {
        if (t <= pts.first()[0]) return pts.first()[1]
        for (i in 1 until pts.size) {
            if (t <= pts[i][0]) {
                val a = pts[i - 1]
                val b = pts[i]
                val span = b[0] - a[0]
                return if (span <= 0) b[1] else a[1] + (b[1] - a[1]) * (t - a[0]) / span
            }
        }
        return pts.last()[1]
    }

    private fun color(stops: List<DoubleArray>, t: Double): Color {
        var at = stops.last()
        if (t <= stops.first()[0]) {
            at = stops.first()
        } else {
            for (i in 1 until stops.size) {
                if (t <= stops[i][0]) {
                    val a = stops[i - 1]
                    val b = stops[i]
                    val k = (t - a[0]) / max(b[0] - a[0], 1e-6)
                    at = DoubleArray(5) { a[it] + (b[it] - a[it]) * k }
                    break
                }
            }
        }
        fun byte(v: Double) = v.roundToInt().coerceIn(0, 255)
        return Color(byte(at[1]), byte(at[2]), byte(at[3]), byte(at[4]))
    }
}

class VfxLibrary private constructor(
    val presets: Map<String, VfxPreset>,
    val textures: Map<String, VfxTexture>,
    val unit: Double,
    val area: DoubleArray?,
    val mists: Map<String, JSONObject>,
) {
    fun preset(name: String) = presets[name] ?: throw IllegalArgumentException("unknown preset $name")

    fun simulate(name: String, seed: Int? = null, prewarm: Boolean = false) =
        VfxSimulation(preset(name), textures, unit, seed).also { if (prewarm) it.prewarm() }

    companion object {
        suspend fun load(context: Context, name: String) = read(context, "vfx", name, "vfx")

        suspend fun loadWeather(context: Context, name: String) = read(context, "weather", name, "weather")

        private suspend fun read(context: Context, dir: String, name: String, stem: String): VfxLibrary {
            val base = "$dir/$name"
            val doc = StudioAssets.json(context, "$base/${name}_$stem.json")
            val textures = HashMap<String, VfxTexture>()
            val tex = doc.getJSONObject("textures")
            for (key in tex.keys()) {
                val t = tex.getJSONObject(key)
                textures[key] = VfxTexture(
                    StudioAssets.bitmap(context, "$base/${t.getString("file")}"),
                    t.optInt("cols", 1),
                    t.optInt("rows", 1),
                )
            }
            val presets = doc.getJSONArray("presets").let { a -> List(a.length()) { VfxPreset(a.getJSONObject(it)) } }
            val mistList = doc.optJSONArray("mists")
            val mists = HashMap<String, JSONObject>()
            if (mistList != null) for (i in 0 until mistList.length()) {
                mistList.getJSONObject(i).let { mists[it.getString("name")] = it }
            }
            return VfxLibrary(
                presets.associateBy { it.name },
                textures,
                doc.optDouble("ppu", 1.0),
                doc.optJSONArray("area")?.doubles(),
                mists,
            )
        }
    }
}

@Composable
fun VfxView(
    library: VfxLibrary,
    preset: String,
    modifier: Modifier = Modifier,
    origin: Offset? = null,
    seed: Int? = null,
    prewarm: Boolean = false,
    onFinished: (() -> Unit)? = null,
) {
    val sim = remember(library, preset, seed) { library.simulate(preset, seed, prewarm) }
    val tick = remember { mutableLongStateOf(0L) }
    LaunchedEffect(sim) {
        var last = 0L
        while (!sim.finished) {
            val now = withFrameNanos { it }
            if (last != 0L) sim.update(min((now - last) / 1e9, 0.1))
            last = now
            tick.longValue = now
        }
        onFinished?.invoke()
    }
    Canvas(modifier) {
        tick.longValue
        sim.draw(this, origin ?: center)
    }
}

@Composable
fun WeatherView(library: VfxLibrary, preset: String, modifier: Modifier = Modifier, seed: Int? = null) {
    val sim = remember(library, preset, seed) { library.simulate(preset, seed, prewarm = true) }
    val tick = remember { mutableLongStateOf(0L) }
    LaunchedEffect(sim) {
        var last = 0L
        while (true) {
            val now = withFrameNanos { it }
            if (last != 0L) sim.update(min((now - last) / 1e9, 0.1))
            last = now
            tick.longValue = now
        }
    }
    Canvas(modifier) {
        tick.longValue
        val area = library.area
        val sc = if (area == null) 1f else (size.width / area[0]).toFloat()
        withTransform({ scale(sc, sc, Offset.Zero) }) { sim.draw(this, Offset.Zero) }
    }
}

@Composable
fun MistView(library: VfxLibrary, layer: String, modifier: Modifier = Modifier, playing: Boolean = true) {
    val spec = library.mists[layer] ?: throw IllegalArgumentException("unknown mist $layer")
    val tex = library.textures.getValue(spec.getString("texture"))
    val tiles = spec.pair("tiles")
    val scroll = spec.pair("scroll")
    val tint = Color(0xFF000000 or spec.getString("tint").removePrefix("#").take(6).toLong(16))
        .copy(alpha = min(1.0, spec.getDouble("density")).toFloat())
    val seconds = rememberSeconds(playing)
    Canvas(modifier) {
        val tw = size.width / tiles[0].toFloat()
        val th = size.height / tiles[1].toFloat()
        val ox = (seconds.floatValue * scroll[0].toFloat()).mod(1f) * tw
        val oy = (seconds.floatValue * scroll[1].toFloat()).mod(1f) * th
        clipRect {
            for (y in -1..kotlin.math.ceil(tiles[1]).toInt()) for (x in -1..kotlin.math.ceil(tiles[0]).toInt()) {
                withTransform({
                    translate(x * tw + ox, y * th + oy)
                    scale(tw / tex.image.width, th / tex.image.height, Offset.Zero)
                }) {
                    drawImage(tex.image, colorFilter = ColorFilter.tint(tint, BlendMode.Modulate))
                }
            }
        }
    }
}

class MinimapPoi(val kind: String, val x: Float, val y: Float)

@Composable
fun MinimapView(
    asset: String,
    pois: List<MinimapPoi>,
    modifier: Modifier = Modifier,
    markerSize: Float = 12f,
    marker: @Composable (MinimapPoi) -> Unit,
) {
    val image = rememberStudioImage(asset)
    BoxWithConstraints(modifier) {
        if (image != null) Image(image, null, Modifier.fillMaxSize(), contentScale = ContentScale.FillBounds)
        for (poi in pois) {
            Box(
                Modifier
                    .offset(maxWidth * poi.x - markerSize.dp / 2, maxHeight * poi.y - markerSize.dp / 2)
                    .size(markerSize.dp),
            ) { marker(poi) }
        }
    }
}

class CardEntry(val id: String, val name: String, val front: String, val art: String?)

@Composable
fun CardFace(asset: String, modifier: Modifier = Modifier) {
    val image = rememberStudioImage(asset)
    if (image != null) Image(image, null, modifier, contentScale = ContentScale.Fit)
}

class NineSliceSpec(
    val asset: String,
    val left: Int,
    val top: Int,
    val right: Int,
    val bottom: Int,
    val width: Int,
    val height: Int,
)

private fun DrawScope.drawNineSlice(image: ImageBitmap, spec: NineSliceSpec, scale: Float) {
    val sx = intArrayOf(0, spec.left, spec.width - spec.right, spec.width)
    val sy = intArrayOf(0, spec.top, spec.height - spec.bottom, spec.height)
    val w = size.width.roundToInt()
    val h = size.height.roundToInt()
    val l = (spec.left * scale).roundToInt().coerceAtMost(w / 2)
    val r = (spec.right * scale).roundToInt().coerceAtMost(w / 2)
    val t = (spec.top * scale).roundToInt().coerceAtMost(h / 2)
    val b = (spec.bottom * scale).roundToInt().coerceAtMost(h / 2)
    val dx = intArrayOf(0, l, w - r, w)
    val dy = intArrayOf(0, t, h - b, h)
    for (j in 0 until 3) for (i in 0 until 3) {
        val sw = sx[i + 1] - sx[i]
        val sh = sy[j + 1] - sy[j]
        val dw = dx[i + 1] - dx[i]
        val dh = dy[j + 1] - dy[j]
        if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0) continue
        drawImage(
            image,
            srcOffset = IntOffset(sx[i], sy[j]),
            srcSize = IntSize(sw, sh),
            dstOffset = IntOffset(dx[i], dy[j]),
            dstSize = IntSize(dw, dh),
            filterQuality = FilterQuality.None,
        )
    }
}

@Composable
fun StudioScreen(
    refWidth: Float,
    modifier: Modifier = Modifier,
    background: String? = null,
    content: @Composable BoxWithConstraintsScope.(scale: Float) -> Unit,
) {
    BoxWithConstraints(modifier.fillMaxSize()) {
        background?.let { rememberStudioImage(it) }?.let {
            Image(it, null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        }
        BoxWithConstraints(Modifier.fillMaxSize().safeDrawingPadding()) {
            content(constraints.maxWidth / refWidth)
        }
    }
}

@Composable
fun StudioPlace(
    left: Float,
    top: Float,
    width: Float,
    height: Float,
    content: @Composable BoxWithConstraintsScope.() -> Unit,
) {
    BoxWithConstraints(Modifier.fillMaxSize()) {
        BoxWithConstraints(Modifier.offset(maxWidth * left, maxHeight * top).size(maxWidth * width, maxHeight * height)) {
            content()
        }
    }
}

@Composable
fun StudioSkin(spec: NineSliceSpec?, scale: Float, content: @Composable BoxScope.() -> Unit) {
    val image = spec?.let { rememberStudioImage(it.asset) }
    val draw = if (spec != null && image != null) Modifier.drawBehind { drawNineSlice(image, spec, scale) } else Modifier
    Box(Modifier.fillMaxSize().then(draw), content = content)
}

@Composable
fun StudioButton(
    normal: NineSliceSpec?,
    pressed: NineSliceSpec?,
    scale: Float,
    onTap: (() -> Unit)?,
    content: @Composable BoxScope.() -> Unit,
) {
    var down by remember { androidx.compose.runtime.mutableStateOf(false) }
    Box(
        Modifier.fillMaxSize().pointerInput(onTap) {
            detectTapGestures(
                onPress = {
                    down = true
                    tryAwaitRelease()
                    down = false
                },
                onTap = { onTap?.invoke() },
            )
        },
    ) {
        StudioSkin(if (down) pressed ?: normal else normal, scale, content)
    }
}

@Composable
fun StudioText(text: String, color: Color, fontSize: Float, align: String, scale: Float) {
    val sp = with(LocalDensity.current) { (fontSize * scale).toSp() }
    val textAlign = when (align) {
        "left" -> TextAlign.Start
        "right" -> TextAlign.End
        else -> TextAlign.Center
    }
    val box = when (align) {
        "left" -> Alignment.CenterStart
        "right" -> Alignment.CenterEnd
        else -> Alignment.Center
    }
    Box(Modifier.fillMaxSize(), contentAlignment = box) {
        BasicText(
            text,
            style = TextStyle(color = color, fontSize = sp, fontWeight = FontWeight.Bold, textAlign = textAlign),
            maxLines = 1,
            softWrap = false,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
fun StudioImage(asset: String) {
    val image = rememberStudioImage(asset)
    if (image != null) Image(image, null, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
}
"""
