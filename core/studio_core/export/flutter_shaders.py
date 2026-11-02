"""Flutter Shader Pack: FragmentProgram ports of the sprite shader presets (hit_flash, dissolve, palette_swap,
outline_pulse, water_ripple, wind_sway) plus a 2D normal-map lighting shader, and the Flame helpers that drive them.
The .frag files and the Dart runtime are identical for every asset, so a pack merge keeps one copy."""
from __future__ import annotations

from pathlib import Path

from studio_core.export.flutter_dart import GEN_HEAD
from studio_core.export.shaders import template_dir
from studio_core.export.source import Files, Source

SHADERS_FILE = "lib/gen/asset_studio_shaders.dart"
SHADER_IDS = ("hit_flash", "dissolve", "palette_swap", "outline_pulse", "water_ripple", "wind_sway", "lit_sprite")
SHADER_DIR = "shaders"
MAX_LIGHTS = 4

NOTE_SHADERS = """- shaders/asset_studio_*.frag + lib/gen/asset_studio_shaders.dart: Shader Pack cho FragmentProgram (Impeller / Skia),
  đã khai báo trong khối `flutter: shaders:` của pubspec_assets.yaml. Dùng với Flame:
    final sprite = ShaderSpriteComponent(sprite: Sprite(await game.images.load('sprites/{asset}.png'), srcSize: cell),
        effect: await HitFlashEffect.load());   // effect.flash() khi trúng đòn
  Hiệu ứng: HitFlashEffect, DissolveEffect, PaletteSwapEffect, OutlinePulseEffect, WaterRippleEffect, WindSwayEffect;
  ShaderSpriteAnimationComponent cho clip. Mỗi component cần một effect riêng (effect giữ uniform của nó).{lit}"""
NOTE_LIT = """
- Đèn 2D: sprites/{asset}_normal.png cùng bố cục ô với sheet. effect = await LitSpriteEffect.load(normalMap: await
  loadSheetNormal(game.images, '{asset}'), lights: [...]); LightSource (PositionComponent, tối đa {maxn} đèn điểm) thêm
  vào world, ánh sáng nền chỉnh bằng effect.ambient. Không có normal map thì LitSpriteEffect.load() chiếu sáng phẳng."""


def frag_files() -> Files:
    root = template_dir() / "flutter"
    return [(f"{SHADER_DIR}/asset_studio_{i}.frag", root / f"{i}.frag") for i in SHADER_IDS]


def shader_files(src: Source, normal: Path | bytes | None, asset: str) -> tuple[Files, list[str]]:
    """The normal map sits next to the sheet with the same cell layout; the lit shader ships only when there is one."""
    files: Files = [(n, d) for n, d in frag_files() if normal is not None or not n.endswith("lit_sprite.frag")]
    files.append((SHADERS_FILE, SHADERS_DART.encode()))
    lit = ""
    if normal is not None:
        files.append((f"assets/images/sprites/{asset}_normal.png", normal))
        lit = NOTE_LIT.format(asset=asset, maxn=MAX_LIGHTS)
    return files, [NOTE_SHADERS.format(asset=asset, lit=lit)]


SHADERS_DART = GEN_HEAD + r"""import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flame/cache.dart';
import 'package:flame/components.dart';

class AssetStudioShaders {
  AssetStudioShaders._();

  static const int maxLights = 4;
  static final Map<String, Future<ui.FragmentProgram>> _programs = {};

  static Future<ui.FragmentProgram> program(String name) =>
      _programs.putIfAbsent(
        name,
        () => ui.FragmentProgram.fromAsset('shaders/asset_studio_$name.frag'),
      );
}

/// `assets/images/sprites/<asset>_normal.png`: normal map with the same cell layout as the sprite sheet.
Future<ui.Image> loadSheetNormal(Images images, String asset) =>
    images.load('sprites/${asset}_normal.png');

class UniformWriter {
  UniformWriter(this.shader);

  final ui.FragmentShader shader;
  int _i = 0;

  void float(double v) => shader.setFloat(_i++, v);

  void vec2(double a, double b) => this
    ..float(a)
    ..float(b);

  void vec4(double a, double b, double c, double d) => this
    ..float(a)
    ..float(b)
    ..float(c)
    ..float(d);

  void color(ui.Color c) => vec4(c.r, c.g, c.b, c.a);
}

/// One shader instance per component: the effect owns its uniforms, so do not share it between sprites.
abstract class SpriteShaderEffect {
  SpriteShaderEffect(ui.FragmentProgram program)
      : _shader = program.fragmentShader();

  final ui.FragmentShader _shader;
  final ui.Paint _paint = ui.Paint();
  double time = 0;

  void update(double dt) => time += dt;

  /// Extra pixels drawn around the sprite for effects that move or add pixels outside the cell.
  double bleed(ui.Rect dest, ui.Rect src) => 0;

  void writeUniforms(UniformWriter w, PositionComponent owner);

  void bindSamplers(ui.FragmentShader shader, ui.Image sprite) {}

  void paint(
    ui.Canvas canvas,
    Sprite sprite,
    ui.Rect dest,
    PositionComponent owner,
  ) {
    final src = sprite.src;
    final w = UniformWriter(_shader)
      ..vec4(dest.left, dest.top, dest.width, dest.height)
      ..vec4(src.left, src.top, src.width, src.height)
      ..vec2(sprite.image.width.toDouble(), sprite.image.height.toDouble())
      ..float(time);
    writeUniforms(w, owner);
    _shader.setImageSampler(0, sprite.image);
    bindSamplers(_shader, sprite.image);
    _paint.shader = _shader;
    canvas.drawRect(dest.inflate(bleed(dest, src)), _paint);
  }

  void dispose() => _shader.dispose();
}

class HitFlashEffect extends SpriteShaderEffect {
  HitFlashEffect._(super.program);

  static Future<HitFlashEffect> load() async =>
      HitFlashEffect._(await AssetStudioShaders.program('hit_flash'));

  ui.Color color = const ui.Color(0xFFFFFFFF);
  double amount = 0;
  double speed = 0;
  double _left = 0;
  double _total = 1;

  /// Fades `amount` from 1 to 0 over [duration] seconds.
  void flash({double duration = 0.15}) {
    _total = duration <= 0 ? 0.001 : duration;
    _left = _total;
    amount = 1;
  }

  @override
  void update(double dt) {
    super.update(dt);
    if (_left > 0) {
      _left = math.max(0, _left - dt);
      amount = _left / _total;
    }
  }

  @override
  void writeUniforms(UniformWriter w, PositionComponent owner) => w
    ..color(color)
    ..float(amount)
    ..float(speed);
}

class DissolveEffect extends SpriteShaderEffect {
  DissolveEffect._(super.program);

  static Future<DissolveEffect> load() async =>
      DissolveEffect._(await AssetStudioShaders.program('dissolve'));

  ui.Color edgeColor = const ui.Color(0xFFFF8C1A);
  double progress = 0;
  double edgeWidth = 0.06;
  double noiseScale = 6;
  double speed = 0;

  @override
  void writeUniforms(UniformWriter w, PositionComponent owner) => w
    ..color(edgeColor)
    ..float(progress)
    ..float(edgeWidth)
    ..float(noiseScale)
    ..float(speed);
}

class PaletteSwapEffect extends SpriteShaderEffect {
  PaletteSwapEffect._(super.program, this._ramp, this.rows);

  /// Each palette lists colour stops from dark to light; a pixel's luma picks the colour from the active [row].
  static Future<PaletteSwapEffect> load(List<List<ui.Color>> palettes) async {
    final ramp = await buildPaletteRamp(palettes);
    return PaletteSwapEffect._(
      await AssetStudioShaders.program('palette_swap'),
      ramp,
      palettes.length,
    );
  }

  final ui.Image _ramp;
  final int rows;
  int row = 0;
  double strength = 1;

  @override
  void writeUniforms(UniformWriter w, PositionComponent owner) => w
    ..float(row.clamp(0, rows - 1).toDouble())
    ..float(rows.toDouble())
    ..float(strength);

  @override
  void bindSamplers(ui.FragmentShader shader, ui.Image sprite) =>
      shader.setImageSampler(1, _ramp);
}

Future<ui.Image> buildPaletteRamp(List<List<ui.Color>> palettes) {
  const width = 256;
  final px = Uint8List(width * palettes.length * 4);
  for (var r = 0; r < palettes.length; r++) {
    final stops = palettes[r];
    for (var x = 0; x < width; x++) {
      final t = x / (width - 1) * (stops.length - 1);
      final i = t.floor().clamp(0, stops.length - 1);
      final c = ui.Color.lerp(
        stops[i],
        stops[math.min(i + 1, stops.length - 1)],
        t - i,
      )!;
      final o = (r * width + x) * 4;
      px[o] = (c.r * 255).round();
      px[o + 1] = (c.g * 255).round();
      px[o + 2] = (c.b * 255).round();
      px[o + 3] = 255;
    }
  }
  final done = Completer<ui.Image>();
  ui.decodeImageFromPixels(
    px,
    width,
    palettes.length,
    ui.PixelFormat.rgba8888,
    done.complete,
  );
  return done.future;
}

class OutlinePulseEffect extends SpriteShaderEffect {
  OutlinePulseEffect._(super.program);

  static Future<OutlinePulseEffect> load() async =>
      OutlinePulseEffect._(await AssetStudioShaders.program('outline_pulse'));

  ui.Color color = const ui.Color(0xFFFFD94D);
  double width = 1;
  double speed = 1.5;
  double minAlpha = 0.25;

  @override
  double bleed(ui.Rect dest, ui.Rect src) => width * dest.width / src.width;

  @override
  void writeUniforms(UniformWriter w, PositionComponent owner) => w
    ..color(color)
    ..float(width)
    ..float(speed)
    ..float(minAlpha);
}

class WaterRippleEffect extends SpriteShaderEffect {
  WaterRippleEffect._(super.program);

  static Future<WaterRippleEffect> load() async =>
      WaterRippleEffect._(await AssetStudioShaders.program('water_ripple'));

  ui.Color tint = const ui.Color(0xFFFFFFFF);
  double amplitude = 1.5;
  double wavelength = 12;
  double speed = 1;

  @override
  double bleed(ui.Rect dest, ui.Rect src) => amplitude * dest.width / src.width;

  @override
  void writeUniforms(UniformWriter w, PositionComponent owner) => w
    ..color(tint)
    ..float(amplitude)
    ..float(wavelength)
    ..float(speed);
}

/// The foot (bottom of the sprite) stays fixed and the top sways by strength x height; the phase follows the
/// component's world x so a row of trees does not move in lockstep.
class WindSwayEffect extends SpriteShaderEffect {
  WindSwayEffect._(super.program);

  static Future<WindSwayEffect> load() async =>
      WindSwayEffect._(await AssetStudioShaders.program('wind_sway'));

  double strength = 0.06;
  double speed = 0.6;
  double wave = 0.02;

  @override
  double bleed(ui.Rect dest, ui.Rect src) => strength * dest.height;

  @override
  void writeUniforms(UniformWriter w, PositionComponent owner) => w
    ..float(strength)
    ..float(speed)
    ..float(wave)
    ..float(owner.absolutePosition.x);
}

/// Point light for [LitSpriteEffect]; [position] is in the same space as the lit components, [radius] in pixels.
class LightSource extends PositionComponent {
  LightSource({
    super.position,
    super.priority,
    this.color = const ui.Color(0xFFFFFFFF),
    this.radius = 160,
    this.intensity = 1,
    this.elevation = 40,
  });

  ui.Color color;
  double radius;
  double intensity;

  /// How far the light floats above the sprite plane; low values give long, grazing highlights.
  double elevation;
}

/// Normal-map lighting: [ambient] plus up to [AssetStudioShaders.maxLights] [LightSource]s. Normals are OpenGL
/// style (green up) and share the sprite sheet's cell layout. Without a normal map the sprite is lit flat.
class LitSpriteEffect extends SpriteShaderEffect {
  LitSpriteEffect._(super.program, this.normalMap);

  static Future<LitSpriteEffect> load({
    ui.Image? normalMap,
    Iterable<LightSource> lights = const [],
    ui.Color ambient = const ui.Color(0xFF3A3F55),
  }) async =>
      LitSpriteEffect._(
        await AssetStudioShaders.program('lit_sprite'),
        normalMap,
      )
        ..lights = lights
        ..ambient = ambient;

  final ui.Image? normalMap;
  Iterable<LightSource> lights = const [];
  ui.Color ambient = const ui.Color(0xFF3A3F55);
  double ambientIntensity = 1;

  @override
  void writeUniforms(UniformWriter w, PositionComponent owner) {
    w
      ..vec4(ambient.r, ambient.g, ambient.b, ambientIntensity)
      ..float(normalMap == null ? 0 : 1);
    final scale = math.max(owner.absoluteScale.x.abs(), 0.0001);
    var n = 0;
    for (final l in lights) {
      if (n == AssetStudioShaders.maxLights) break;
      if (!l.isMounted) continue;
      final p = owner.absoluteToLocal(l.absolutePosition);
      w
        ..vec4(p.x, p.y, l.elevation / scale, l.radius / scale)
        ..vec4(l.color.r, l.color.g, l.color.b, l.intensity);
      n++;
    }
    for (; n < AssetStudioShaders.maxLights; n++) {
      w
        ..vec4(0, 0, 1, 0)
        ..vec4(0, 0, 0, 0);
    }
  }

  @override
  void bindSamplers(ui.FragmentShader shader, ui.Image sprite) =>
      shader.setImageSampler(1, normalMap ?? sprite);
}

class ShaderSpriteComponent extends SpriteComponent {
  ShaderSpriteComponent({
    super.sprite,
    super.size,
    super.position,
    super.anchor,
    super.angle,
    super.scale,
    super.priority,
    this.effect,
  });

  SpriteShaderEffect? effect;

  @override
  void update(double dt) {
    super.update(dt);
    effect?.update(dt);
  }

  @override
  void render(ui.Canvas canvas) {
    final s = sprite;
    final e = effect;
    if (s == null || e == null) {
      super.render(canvas);
      return;
    }
    e.paint(canvas, s, ui.Rect.fromLTWH(0, 0, size.x, size.y), this);
  }
}

class ShaderSpriteAnimationComponent extends SpriteAnimationComponent {
  ShaderSpriteAnimationComponent({
    super.animation,
    super.size,
    super.position,
    super.anchor,
    super.angle,
    super.scale,
    super.priority,
    this.effect,
  });

  SpriteShaderEffect? effect;

  @override
  void update(double dt) {
    super.update(dt);
    effect?.update(dt);
  }

  @override
  void render(ui.Canvas canvas) {
    final s = animationTicker?.getSprite();
    final e = effect;
    if (s == null || e == null) {
      super.render(canvas);
      return;
    }
    e.paint(canvas, s, ui.Rect.fromLTWH(0, 0, size.x, size.y), this);
  }
}
"""
