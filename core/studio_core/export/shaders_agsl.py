"""Compose Shader Pack: AGSL ports of the Flutter .frag presets (shader_templates/agsl/) embedded in one Kotlin file with
RuntimeShader + RenderEffect modifiers. Identical for every asset, so a pack merge keeps one copy."""
from __future__ import annotations

from studio_core.export.shaders import template_dir
from studio_core.export.source import Files

AGSL_IDS = ("hit_flash", "dissolve", "palette_swap", "outline_pulse", "water_ripple", "wind_sway")
AGSL_FILE = "kotlin/AssetStudioShaders.kt"

NOTE_AGSL = """- kotlin/AssetStudioShaders.kt: Shader Pack AGSL (RuntimeShader, Android 13+ / API 33; máy cũ hơn chạy bản không hiệu ứng,
  hitFlash dùng ColorFilter). Modifier cho mọi Composable (Image sprite, ô sheet...):
    Image(painterResource(...), null, Modifier.hitFlash(color = Color.White, amount = flash))
  Hiệu ứng: hitFlash, dissolve, paletteSwap (buildPaletteRamp), outlinePulse, waterRipple, windSway; uniform cùng tên/nghĩa
  như shaders/*.frag của Flutter (uRect/uSrc/uImage thay bằng kích thước layer). Hiệu ứng chỉ vẽ trong khung layer: chừa
  padding quanh sprite cho outline/ripple/sway. Chưa biên dịch thật trên thiết bị."""


def agsl_source(shader_id: str) -> str:
    root = template_dir() / "agsl"
    return (root / "common.agsl").read_text(encoding="utf-8") + "\n" + (root / f"{shader_id}.agsl").read_text(encoding="utf-8")


def kotlin_source(package: str) -> str:
    consts = "\n\n".join(f'    private const val {i.upper()} = """\n{agsl_source(i).rstrip()}\n"""' for i in AGSL_IDS)
    return KOTLIN.replace("@PKG@", package).replace("@SOURCES@", consts)


def shader_files(package: str) -> tuple[Files, list[str]]:
    return [(AGSL_FILE, kotlin_source(package).encode())], [NOTE_AGSL]


KOTLIN = r'''package @PKG@

import android.graphics.Bitmap
import android.graphics.BitmapShader
import android.graphics.RenderEffect
import android.graphics.RuntimeShader
import android.graphics.Shader
import android.os.Build
import androidx.annotation.RequiresApi
import androidx.compose.runtime.Composable
import androidx.compose.runtime.FloatState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.Paint
import androidx.compose.ui.graphics.asComposeRenderEffect
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.toArgb

object AssetStudioShaders {
    /** RuntimeShader (AGSL) exists from API 33; below it every modifier here leaves the content untouched. */
    val supported: Boolean get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU

@SOURCES@

    internal fun source(id: String): String = when (id) {
        "hit_flash" -> HIT_FLASH
        "dissolve" -> DISSOLVE
        "palette_swap" -> PALETTE_SWAP
        "outline_pulse" -> OUTLINE_PULSE
        "water_ripple" -> WATER_RIPPLE
        else -> WIND_SWAY
    }
}

/** Seconds since composition, advancing every frame while [running]; feeds the uTime uniform. */
@Composable
fun rememberShaderTime(running: Boolean = true): FloatState {
    val time = remember { mutableFloatStateOf(0f) }
    LaunchedEffect(running) {
        if (!running) return@LaunchedEffect
        var last = withFrameNanos { it }
        while (true) {
            val now = withFrameNanos { it }
            time.floatValue += (now - last) / 1_000_000_000f
            last = now
        }
    }
    return time
}

@RequiresApi(33)
private fun Modifier.runtimeEffect(
    shader: RuntimeShader,
    time: FloatState,
    uniforms: RuntimeShader.() -> Unit,
): Modifier = graphicsLayer {
    if (size.width > 0f && size.height > 0f) {
        shader.setFloatUniform("uSize", size.width, size.height)
        shader.setFloatUniform("uTime", time.floatValue)
        shader.uniforms()
        renderEffect = RenderEffect.createRuntimeShaderEffect(shader, "uTexture").asComposeRenderEffect()
    }
}

@RequiresApi(33)
@Composable
private fun Modifier.agslApply(id: String, time: FloatState, uniforms: RuntimeShader.() -> Unit): Modifier {
    val shader = remember(id) { RuntimeShader(AssetStudioShaders.source(id)) }
    return runtimeEffect(shader, time, uniforms)
}

@Composable
private fun Modifier.agsl(id: String, time: FloatState, uniforms: RuntimeShader.() -> Unit): Modifier =
    if (AssetStudioShaders.supported) agslApply(id, time, uniforms) else this

private fun RuntimeShader.setColorUniform(name: String, c: Color) =
    setFloatUniform(name, c.red, c.green, c.blue, c.alpha)

/** Flashes the content towards [color]; [amount] 0..1, or [speed] > 0 to blink on its own. */
@Composable
fun Modifier.hitFlash(color: Color = Color.White, amount: Float = 0f, speed: Float = 0f): Modifier {
    if (!AssetStudioShaders.supported) {
        val a = (amount * color.alpha).coerceIn(0f, 1f)
        return drawWithContent {
            if (a <= 0f) {
                drawContent()
            } else {
                drawIntoCanvas { canvas ->
                    val paint = Paint().apply { colorFilter = ColorFilter.tint(color.copy(alpha = a), BlendMode.SrcAtop) }
                    canvas.saveLayer(Rect(Offset.Zero, size), paint)
                    drawContent()
                    canvas.restore()
                }
            }
        }
    }
    val time = rememberShaderTime(speed > 0f)
    return agsl("hit_flash", time) {
        setColorUniform("uColor", color)
        setFloatUniform("uAmount", amount)
        setFloatUniform("uSpeed", speed)
    }
}

/** Burns the content away with noise; [progress] 0..1, or [speed] > 0 to ping-pong on its own. */
@Composable
fun Modifier.dissolve(
    progress: Float = 0f,
    edgeColor: Color = Color(0xFFFF8C1A),
    edgeWidth: Float = 0.06f,
    noiseScale: Float = 6f,
    speed: Float = 0f,
): Modifier {
    val time = rememberShaderTime(speed > 0f)
    return agsl("dissolve", time) {
        setColorUniform("uEdgeColor", edgeColor)
        setFloatUniform("uProgress", progress)
        setFloatUniform("uEdgeWidth", edgeWidth)
        setFloatUniform("uNoiseScale", noiseScale)
        setFloatUniform("uSpeed", speed)
    }
}

/** Each palette lists colour stops dark to light, one row per palette; pass the result to [paletteSwap]. */
fun buildPaletteRamp(palettes: List<List<Color>>): Bitmap {
    val width = 256
    val bitmap = Bitmap.createBitmap(width, palettes.size.coerceAtLeast(1), Bitmap.Config.ARGB_8888)
    palettes.forEachIndexed { row, stops ->
        for (x in 0 until width) {
            val t = x / (width - 1f) * (stops.size - 1)
            val i = t.toInt().coerceIn(0, stops.size - 1)
            val c = lerp(stops[i], stops[minOf(i + 1, stops.size - 1)], t - i)
            bitmap.setPixel(x, row, Color(c.red, c.green, c.blue, 1f).toArgb())
        }
    }
    return bitmap
}

/** A pixel's luma picks the colour from [row] of the [ramp] made by [buildPaletteRamp]. */
@Composable
fun Modifier.paletteSwap(ramp: Bitmap, row: Int = 0, strength: Float = 1f): Modifier {
    val time = rememberShaderTime(false)
    val rampShader = remember(ramp) { BitmapShader(ramp, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP) }
    return agsl("palette_swap", time) {
        setInputShader("uRamp", rampShader)
        setFloatUniform("uRow", row.toFloat())
        setFloatUniform("uRows", ramp.height.toFloat())
        setFloatUniform("uStrength", strength)
    }
}

/** [width] in layer pixels; the outline is clipped to the layer, so pad the content by at least [width]. */
@Composable
fun Modifier.outlinePulse(
    color: Color = Color(0xFFFFD94D),
    width: Float = 1f,
    speed: Float = 1.5f,
    minAlpha: Float = 0.25f,
): Modifier {
    val time = rememberShaderTime()
    return agsl("outline_pulse", time) {
        setColorUniform("uColor", color)
        setFloatUniform("uWidth", width)
        setFloatUniform("uSpeed", speed)
        setFloatUniform("uMinAlpha", minAlpha)
    }
}

@Composable
fun Modifier.waterRipple(
    tint: Color = Color.White,
    amplitude: Float = 1.5f,
    wavelength: Float = 12f,
    speed: Float = 1f,
): Modifier {
    val time = rememberShaderTime()
    return agsl("water_ripple", time) {
        setColorUniform("uTint", tint)
        setFloatUniform("uAmplitude", amplitude)
        setFloatUniform("uWavelength", wavelength)
        setFloatUniform("uSpeed", speed)
    }
}

/** The foot (bottom of the layer) stays fixed and the top sways by [strength] x height; [worldX] offsets the phase. */
@Composable
fun Modifier.windSway(
    strength: Float = 0.06f,
    speed: Float = 0.6f,
    wave: Float = 0.02f,
    worldX: Float = 0f,
): Modifier {
    val time = rememberShaderTime()
    return agsl("wind_sway", time) {
        setFloatUniform("uStrength", strength)
        setFloatUniform("uSpeed", speed)
        setFloatUniform("uWave", wave)
        setFloatUniform("uWorldX", worldX)
    }
}
'''
